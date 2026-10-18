from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from frog_code.core.agents.loader import FrogAgentProfile, FrogAgentProfileLoader
from frog_code.core.bus.events import FrogSubagentFinishedEvent, FrogSubagentStartedEvent
from frog_code.core.context import FrogExecutionContext
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.events.writer import FrogEventWriter
from frog_code.core.loop import FrogAgentLoop
from frog_code.core.runs import frog_new_run_id
from frog_code.core.subagent.registry import FrogBackgroundTaskRegistry
from frog_code.core.tools.base import FrogBaseTool, FrogToolResult
from frog_code.core.tools.builtin.bash import FrogBashTool
from frog_code.core.tools.builtin.list_dir import FrogListDirTool
from frog_code.core.tools.builtin.read_file import FrogReadFileTool
from frog_code.core.tools.builtin.task_create import FrogTaskCreateTool
from frog_code.core.tools.builtin.task_get import FrogTaskGetTool
from frog_code.core.tools.builtin.task_list import FrogTaskListTool
from frog_code.core.tools.builtin.task_update import FrogTaskUpdateTool
from frog_code.core.tools.builtin.write_file import FrogWriteFileTool
from frog_code.core.tools.registry import FrogToolRegistry

if TYPE_CHECKING:
    from frog_code.core.llm.base import FrogLLMProvider
    from frog_code.core.permissions.manager import FrogPermissionManager

_profile_loader = FrogAgentProfileLoader()


def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


class FrogSpawnAgentParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    run_in_background: bool = False
    subagent_type: str = ""


# 在隔离的冷启动上下文中派生子 agent，支持前台阻塞和后台并行两种模式
class FrogSpawnAgentTool(FrogBaseTool):
    name = "frog_spawn_agent"
    description = (
        "Spawn an isolated sub-agent to frog_handle a self-contained sub-task. "
        "The sub-agent starts with a clean context containing only the provided prompt — "
        "it does not inherit the current conversation history. "
        "Use run_in_background=true to run in parallel; retrieve result later with agent_result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "3-5 word task description shown in progress display",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Complete task description including all context the sub-agent needs. "
                    "The sub-agent cannot see the parent conversation, so be explicit."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "When true, returns immediately with a run_id; use agent_result to poll.",  # noqa: E501
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent role profile (planner/executor/reviewer). Leave empty for default.",  # noqa: E501
            },
        },
        "required": ["description", "prompt"],
    }
    params_model = FrogSpawnAgentParams

    # 构造 FrogSpawnAgentTool；depth=0 表示根 agent，最大允许嵌套深度为 2
    def __init__(
        self,
        provider: FrogLLMProvider,
        parent_bus: FrogEventBus,
        parent_run_id: str,
        permission_manager: FrogPermissionManager | None,
        max_steps: int,
        task_registry: FrogBackgroundTaskRegistry,
        frog_runs_dir: Path,
        session_id: str,
        depth: int = 0,
    ) -> None:
        self._provider = provider
        self._parent_bus = parent_bus
        self._parent_run_id = parent_run_id
        self._permission_manager = permission_manager
        self._max_steps = max_steps
        self._task_registry = task_registry
        self._runs_dir = frog_runs_dir
        self._session_id = session_id
        self._depth = depth

    # 派生子 agent，前台时阻塞直到完成并返回结果，后台时立即返回 run_id
    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        p = FrogSpawnAgentParams.model_validate(params)

        if self._depth >= 2:
            return FrogToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )

        profile: FrogAgentProfile | None = None
        if p.subagent_type:
            profile = _profile_loader.load(p.subagent_type)

        child_run_id = frog_new_run_id()
        child_context = FrogExecutionContext(
            run_id=child_run_id,
            goal=p.prompt,
            max_steps=self._max_steps,
            system_prompt_override=profile.frog_system_prompt if profile else None,
        )

        child_bus = FrogEventBus()

        # 将子 bus 所有事件桥接到父 bus，TUI 据此渲染嵌套进度
        async def _frog_bridge(event: BaseModel) -> None:
            await self._parent_bus.frog_publish(event)

        child_bus.frog_subscribe(_frog_bridge)

        child_registry = self._frog_build_child_registry(child_bus, child_run_id, profile)
        child_loop = FrogAgentLoop(
            self._provider,
            child_registry,
            child_bus,
            permission_manager=self._permission_manager,
            session_id=self._session_id,
        )

        await self._parent_bus.frog_publish(
            FrogSubagentStartedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                description=p.description,
                ts=_frog_now(),
            )
        )

        child_run_path = self._runs_dir / child_run_id
        child_run_path.mkdir(parents=True, exist_ok=True)

        if p.run_in_background:
            task: asyncio.Task[None] = asyncio.create_task(
                self._frog_run_background(
                    child_loop, child_context, child_bus, child_run_path, child_run_id
                )
            )
            self._task_registry.register(child_run_id, task, child_context)
            return FrogToolResult(
                content=(
                    f"Subagent started in background. run_id={child_run_id}. "
                    f"Use agent_result(run_id='{child_run_id}') to retrieve result."
                )
            )

        async with FrogEventWriter(child_run_path / "events.jsonl") as writer:
            writer.frog_subscribe(child_bus)
            await child_loop.run(child_context)

        await self._parent_bus.frog_publish(
            FrogSubagentFinishedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                status=child_context.status,
                ts=_frog_now(),
            )
        )

        if child_context.status == "success":
            return FrogToolResult(
                content=child_context.result or "Subagent completed with no text output."
            )
        return FrogToolResult(
            content=(
                child_context.result
                or f"Subagent failed (status={child_context.status}, reason={child_context.reason})"
            ),
            is_error=True,
            error_type="runtime_error",
        )

    # 后台任务协程：写事件文件，运行 loop，发布完成事件
    async def _frog_run_background(
        self,
        loop: FrogAgentLoop,
        context: FrogExecutionContext,
        bus: FrogEventBus,
        run_path: Path,
        run_id: str,
    ) -> None:
        async with FrogEventWriter(run_path / "events.jsonl") as writer:
            writer.frog_subscribe(bus)
            await loop.run(context)
        await self._parent_bus.frog_publish(
            FrogSubagentFinishedEvent(
                run_id=run_id,
                parent_run_id=self._parent_run_id,
                status=context.status,
                ts=_frog_now(),
            )
        )

    # 构造子 registry；基于角色配置过滤工具，深度允许时注册嵌套 FrogSpawnAgentTool
    def _frog_build_child_registry(
        self,
        child_bus: FrogEventBus,
        child_run_id: str,
        profile: FrogAgentProfile | None,
    ) -> FrogToolRegistry:
        from frog_code.core.task.manager import FrogTaskManager

        allowed: set[str] | None = (
            set(profile.allowed_tools) if profile and profile.allowed_tools else None
        )

        def _frog_allowed(name: str) -> bool:
            return allowed is None or name in allowed

        registry = FrogToolRegistry()
        _all_tools = [
            FrogReadFileTool(),
            FrogBashTool(),
            FrogWriteFileTool(),
            FrogListDirTool(),
        ]
        for t in _all_tools:
            if _frog_allowed(t.name):
                registry.register(t)

        child_task_manager = FrogTaskManager(self._runs_dir / child_run_id / ".tasks")
        for t in [
            FrogTaskCreateTool(child_task_manager),
            FrogTaskUpdateTool(child_task_manager),
            FrogTaskListTool(child_task_manager),
            FrogTaskGetTool(child_task_manager),
        ]:
            if _frog_allowed(t.name):
                registry.register(t)

        if self._depth < 1:
            nested = FrogSpawnAgentTool(
                provider=self._provider,
                parent_bus=child_bus,
                parent_run_id=child_run_id,
                permission_manager=self._permission_manager,
                max_steps=self._max_steps,
                task_registry=self._task_registry,
                frog_runs_dir=self._runs_dir,
                session_id=self._session_id,
                depth=self._depth + 1,
            )
            if _frog_allowed("frog_spawn_agent"):
                registry.register(nested)
            if _frog_allowed("frog_agent_result"):
                registry.register(FrogAgentResultTool(self._task_registry))

        return registry


class FrogAgentResultParams(BaseModel):
    run_id: str


# 查询后台 subagent 的执行状态和最终结果
class FrogAgentResultTool(FrogBaseTool):
    name = "frog_agent_result"
    description = (
        "Retrieve the result of a background sub-agent previously started with spawn_agent. "
        "Returns 'still running' if the sub-agent has not yet completed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The run_id returned by spawn_agent(run_in_background=true)",
            },
        },
        "required": ["run_id"],
    }
    params_model = FrogAgentResultParams

    # 初始化，持有共享的后台任务注册表
    def __init__(self, task_registry: FrogBackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    # 查询指定 run_id 的后台任务状态，返回结果或错误
    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        p = FrogAgentResultParams.model_validate(params)
        entry = self._task_registry.get(p.run_id)
        if entry is None:
            return FrogToolResult(
                content=f"Unknown run_id: {p.run_id}. Only background subagents can be queried.",
                is_error=True,
                error_type="runtime_error",
            )
        task, context = entry
        if not task.done():
            return FrogToolResult(content="still running")
        if task.cancelled():
            return FrogToolResult(
                content="Subagent was cancelled.", is_error=True, error_type="runtime_error"
            )
        exc = task.exception()
        if exc is not None:
            return FrogToolResult(
                content=f"Subagent raised an exception: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        return FrogToolResult(content=context.result or "Subagent completed with no text result.")

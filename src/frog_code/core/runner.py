from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from frog_code.core.bus.events import FrogRunFinishedEvent, FrogRunStartedEvent
from frog_code.core.compact.compactor import FrogCompactor
from frog_code.core.config import FrogConfig
from frog_code.core.context import FrogExecutionContext
from frog_code.core.events.bus import EventHandler, FrogEventBus
from frog_code.core.events.writer import FrogEventWriter
from frog_code.core.llm.base import FrogLLMProvider
from frog_code.core.llm.provider import FrogAnthropicProvider
from frog_code.core.loop import FrogAgentLoop
from frog_code.core.mcp.server import FrogMcpServerManager
from frog_code.core.memory.loader import frog_load_context_file
from frog_code.core.permissions.manager import FrogPermissionManager
from frog_code.core.runs import RUNS_DIR, frog_new_run_id
from frog_code.core.session.model import FrogSession
from frog_code.core.session.store import FrogSessionStore
from frog_code.core.subagent.registry import FrogBackgroundTaskRegistry
from frog_code.core.subagent.tool import FrogAgentResultTool, FrogSpawnAgentTool
from frog_code.core.task.manager import FrogTaskManager
from frog_code.core.tools.builtin import (
    FrogBashTool,
    FrogListDirTool,
    FrogNoteSaveTool,
    FrogReadFileTool,
    FrogTaskCreateTool,
    FrogTaskGetTool,
    FrogTaskListTool,
    FrogTaskUpdateTool,
    FrogWriteFileTool,
)
from frog_code.core.tools.registry import FrogToolRegistry
from frog_code.core.trace.provider import FrogTracingProvider
from frog_code.core.trace.writer import FrogTraceWriter


def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class FrogRunOutcome:
    status: str
    result: str
    reason: str | None


class FrogAgentRunner:
    # 组装所有运行时依赖，准备执行一次完整的 agent run
    def __init__(
        self,
        config: FrogConfig,
        *,
        bus: FrogEventBus | None = None,
        provider: FrogLLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        frog_runs_dir: Path | None = None,
        trace: FrogTraceWriter | None = None,
        permission_manager: FrogPermissionManager | None = None,
        mcp_manager: FrogMcpServerManager | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._provider = provider
        self._extra_handlers: list[EventHandler] = extra_handlers or []
        self._runs_dir = frog_runs_dir or RUNS_DIR
        self._trace = trace
        self._permission_manager = permission_manager
        self._mcp_manager = mcp_manager
        # 跨 run 共享的后台 subagent 任务注册表
        self._task_registry = FrogBackgroundTaskRegistry()

    # 构建工具注册表，注入 FrogTaskManager（任务工具共享同一实例）；可选注入 FrogSpawnAgentTool
    def _frog_build_registry(
        self,
        task_manager: FrogTaskManager,
        *,
        session: FrogSession | None = None,
        store: FrogSessionStore | None = None,
        run_id: str | None = None,
        provider: FrogLLMProvider | None = None,
        bus: FrogEventBus | None = None,
        child_runs_dir: Path | None = None,
        session_id: str = "",
        tool_whitelist: list[str] | None = None,
    ) -> FrogToolRegistry:
        allowed: set[str] | None = set(tool_whitelist) if tool_whitelist else None

        def _frog_ok(name: str) -> bool:
            return allowed is None or name in allowed

        registry = FrogToolRegistry()
        for t in [FrogReadFileTool(), FrogBashTool(), FrogWriteFileTool(), FrogListDirTool()]:
            if _frog_ok(t.name):
                registry.register(t)
        for t in [
            FrogTaskCreateTool(task_manager),
            FrogTaskUpdateTool(task_manager),
            FrogTaskListTool(task_manager),
            FrogTaskGetTool(task_manager),
        ]:
            if _frog_ok(t.name):
                registry.register(t)
        if session is not None and store is not None and run_id is not None:
            note_tool = FrogNoteSaveTool(store, session.id, run_id)
            if _frog_ok(note_tool.name):
                registry.register(note_tool)
        if provider is not None and bus is not None and run_id is not None:
            frog_runs_dir = child_runs_dir or self._runs_dir
            if _frog_ok("frog_spawn_agent"):
                registry.register(
                    FrogSpawnAgentTool(
                        provider=provider,
                        parent_bus=bus,
                        parent_run_id=run_id,
                        permission_manager=self._permission_manager,
                        max_steps=self._config.agent.max_steps,
                        task_registry=self._task_registry,
                        frog_runs_dir=frog_runs_dir,
                        session_id=session_id,
                        depth=0,
                    )
                )
            if _frog_ok("frog_agent_result"):
                registry.register(FrogAgentResultTool(self._task_registry))
        if self._mcp_manager is not None:
            for mcp_tool in self._mcp_manager.get_tools():
                if _frog_ok(mcp_tool.name):
                    registry.register(mcp_tool)
        return registry

    # 执行一次完整的 agent run（委托给 frog_run_and_capture，忽略返回值）
    async def run(self, goal: str, *, run_id: str | None = None) -> None:
        await self.frog_run_and_capture(goal, run_id=run_id)

    # 执行 agent run 并返回 FrogRunOutcome（含最终文字结果）
    async def frog_run_and_capture(
        self,
        goal: str,
        *,
        run_id: str | None = None,
        session: FrogSession | None = None,
        store: FrogSessionStore | None = None,
        system_prompt_override: str | None = None,
        tool_whitelist: list[str] | None = None,
    ) -> FrogRunOutcome:
        run_id = run_id or frog_new_run_id()
        if session is not None and store is not None:
            run_path = store.frog_runs_dir(session.id) / run_id
            history = store.frog_read_messages(session.id)
            notes = store.frog_read_notes(session.id)
        else:
            run_path = self._runs_dir / run_id
            history = [{"role": "user", "content": goal}]
            notes = ""
        run_path.mkdir(parents=True, exist_ok=True)

        global_ctx = frog_load_context_file(Path("~/.frog/context.md").expanduser())
        project_ctx = frog_load_context_file(Path(".frog/context.md"))

        task_manager = FrogTaskManager(run_path / ".tasks")

        bus = self._bus if self._bus is not None else FrogEventBus()
        for h in self._extra_handlers:
            bus.frog_subscribe(h)

        context = FrogExecutionContext(
            run_id=run_id,
            goal=goal,
            max_steps=self._config.agent.max_steps,
            prefill_messages=history,
            session_notes=notes,
            global_context=global_ctx,
            project_context=project_ctx,
            system_prompt_override=system_prompt_override,
        )
        prefill_len = len(history)

        async with FrogEventWriter(run_path / "events.jsonl") as writer:
            writer.frog_subscribe(bus)
            await bus.frog_publish(FrogRunStartedEvent(run_id=run_id, goal=goal, ts=_frog_now()))

            cancelled = False
            try:
                provider: FrogLLMProvider = self._provider or FrogAnthropicProvider(
                    self._config.llm.default_model,
                    base_url=self._config.llm.base_url,
                )
                if self._trace is not None:
                    provider = FrogTracingProvider(
                        provider,
                        self._trace,
                        include_payload=self._config.trace.include_llm_payload,
                    )
                session_id_str = session.id if session is not None else ""
                child_runs_dir = (
                    store.frog_runs_dir(session.id)
                    if session is not None and store is not None
                    else self._runs_dir
                )
                registry = self._frog_build_registry(
                    task_manager,
                    session=session,
                    store=store,
                    run_id=run_id,
                    provider=provider,
                    bus=bus,
                    child_runs_dir=child_runs_dir,
                    session_id=session_id_str,
                    tool_whitelist=tool_whitelist,
                )
                frog_session_dir = (
                    store.frog_session_dir(session.id)
                    if session is not None and store is not None
                    else run_path
                )
                compactor = FrogCompactor(bus, frog_session_dir, session_id_str)
                loop = FrogAgentLoop(
                    provider, registry, bus,
                    permission_manager=self._permission_manager,
                    compactor=compactor,
                    compact_threshold=self._config.compaction.auto_threshold,
                    session_id=session_id_str,
                )
                await loop.run(context)
            except asyncio.CancelledError:
                cancelled = True
                if not context.frog_is_done():
                    context.frog_mark_failed("cancelled")
            except Exception:
                logging.getLogger(__name__).exception(
                    "agent run failed run_id=%s step=%d", run_id, context.step
                )
                if not context.frog_is_done():
                    context.frog_mark_failed("llm_error")

            await bus.frog_publish(
                FrogRunFinishedEvent(
                    run_id=run_id,
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_frog_now(),
                )
            )

        if session is not None and store is not None:
            store.frog_append_messages(session.id, context.messages[prefill_len:], run_id=run_id)

        if cancelled:
            raise asyncio.CancelledError()

        return FrogRunOutcome(
            status=context.status,
            result=context.result,
            reason=context.reason,
        )

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from frog_code.core.bus.events import FrogStepFinishedEvent, FrogStepStartedEvent
from frog_code.core.context import FrogExecutionContext
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.llm.base import FrogLLMProvider
from frog_code.core.tools.invocation import frog_invoke_tool
from frog_code.core.tools.registry import FrogToolRegistry

if TYPE_CHECKING:
    from frog_code.core.compact.compactor import FrogCompactor
    from frog_code.core.permissions.manager import FrogPermissionManager


log = logging.getLogger(__name__)

def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


class FrogAgentLoop:
    # 初始化循环所需依赖：LLM provider、工具注册表、事件总线，以及可选的权限管理器、压缩器和 session ID
    def __init__(
        self,
        provider: FrogLLMProvider,
        registry: FrogToolRegistry,
        bus: FrogEventBus,
        *,
        permission_manager: FrogPermissionManager | None = None,
        compactor: FrogCompactor | None = None,
        compact_threshold: float = 0.80,
        session_id: str = "",
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._bus = bus
        self._permission_manager = permission_manager
        self._compactor = compactor
        self._compact_threshold = compact_threshold
        self._session_id = session_id

    # 驱动 plan→act→observe 循环直到上下文终止；CancelledError 向上传播
    async def run(self, context: FrogExecutionContext) -> None:
        while not context.frog_is_done():
            context.step += 1
            await self._bus.frog_publish(
                FrogStepStartedEvent(run_id=context.run_id, step=context.step, ts=_frog_now())
            )

            # [plan] call LLM — API errors terminate the run
            try:
                response = await self._provider.frog_chat(
                    messages=context.messages,
                    frog_tool_schemas=self._registry.frog_tool_schemas(),
                    bus=self._bus,
                    run_id=context.run_id,
                    step=context.step,
                    system=context.frog_system_prompt(
                        "You are a helpful AI assistant. "
                        "Use the available tools to complete the user's goal. "
                        "When the goal is fully achieved, respond with a final answer "
                        "and do not call any more tools."
                    ),
                )
            except asyncio.CancelledError:
                context.frog_mark_failed("cancelled")
                raise
            except Exception:
                logging.getLogger(__name__).exception(
                    "LLM call failed run_id=%s step=%d", context.run_id, context.step
                )
                context.frog_mark_failed("llm_error")
                break

            # [observe] append assistant content blocks to context
            # thinking blocks must come first and be preserved verbatim for extended thinking mode
            blocks: list[dict[str, object]] = list(response.thinking_blocks)
            if response.text:
                blocks.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                )
            context.frog_add_assistant_message(blocks)

            # [act] execute each requested tool; errors become tool results so loop continues
            if response.stop_reason == "tool_use":
                for tc in response.tool_calls:
                    result = await frog_invoke_tool(
                        self._registry, tc, self._bus, context.run_id,
                        permission_manager=self._permission_manager,
                        session_id=self._session_id,
                    )
                    context.frog_add_tool_result(tc.id, result.content, is_error=result.is_error)
            elif response.stop_reason == "max_tokens" and response.tool_calls:
                # Output token limit hit mid-tool-call; input is incomplete.
                # Add synthetic error results so the conversation stays balanced.
                for tc in response.tool_calls:
                    context.frog_add_tool_result(
                        tc.id,
                        "Error: output token limit reached before this tool call could be completed. "
                        "Please break the task into smaller steps and try again.",
                        is_error=True,
                    )

            # Termination check — end_turn wins over max_steps if both hit on same step
            if response.stop_reason == "end_turn":
                context.result = response.text or ""
                context.frog_mark_success()
            elif context.step >= context.max_steps:
                context.frog_mark_failed("exceeded_max_steps")

            # 工具结果追加完毕（messages 末尾为 user）后检查压缩，仅在 run 继续时触发
            # 此时压缩结果 [user_summary, assistant_ack] 对下一次 LLM 调用是合法输入
            if (
                not context.frog_is_done()
                and response.stop_reason == "tool_use"
                and self._compactor is not None
                and self._compact_threshold > 0
                and response.usage is not None
                and response.usage.context_pct >= self._compact_threshold
            ):
                await self._compactor.frog_compact(context, self._provider)

            await self._bus.frog_publish(
                FrogStepFinishedEvent(run_id=context.run_id, step=context.step, ts=_frog_now())
            )

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from frog_code.core.bus.events import (
    FrogPermissionDeniedEvent,
    FrogPermissionGrantedEvent,
    FrogPermissionRequestedEvent,
    FrogToolCallFailedEvent,
    FrogToolCallFinishedEvent,
    FrogToolCallStartedEvent,
)
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.llm.types import FrogToolCallBlock
from frog_code.core.tools.base import FrogToolResult
from frog_code.core.tools.errors import FrogRateLimitedError
from frog_code.core.tools.registry import FrogToolRegistry

if TYPE_CHECKING:
    from frog_code.core.permissions.manager import FrogPermissionManager

_DEFAULT_TIMEOUT: float = 120.0
_MAX_RETRIES: int = 2
_RETRY_BASE_S: float = 2.0  # backoff base; tests can monkeypatch to 0
_RETRYABLE: frozenset[str] = frozenset({"runtime_error", "rate_limited"})


def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


# 发布 FrogToolCallFailedEvent 并返回对应 FrogToolResult
async def _frog_fail(
    bus: FrogEventBus,
    run_id: str,
    tool_call: FrogToolCallBlock,
    error_class: str,
    error_message: str,
    elapsed_ms: int,
    *,
    attempt: int = 1,
) -> FrogToolResult:
    await bus.frog_publish(
        FrogToolCallFailedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            error_class=error_class,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            attempt=attempt,
            ts=_frog_now(),
        )
    )
    return FrogToolResult(content=error_message, is_error=True, error_type=error_class)


# 校验参数、检查权限、限时调用工具、发布进度事件，失败时指数退避重试，返回 FrogToolResult（不抛异常）
async def frog_invoke_tool(
    registry: FrogToolRegistry,
    tool_call: FrogToolCallBlock,
    bus: FrogEventBus,
    run_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
    *,
    permission_manager: FrogPermissionManager | None = None,
    session_id: str = "",
) -> FrogToolResult:
    t0 = time.monotonic()

    await bus.frog_publish(
        FrogToolCallStartedEvent(
            run_id=run_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            ts=_frog_now(),
        )
    )

    def frog_elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    tool = registry.get(tool_call.name)
    if tool is None:
        return await _frog_fail(
            bus, run_id, tool_call,
            "runtime_error", f"unknown tool: {tool_call.name}", frog_elapsed(),
        )

    if tool.params_model is not None:
        try:
            tool.params_model.model_validate(dict(tool_call.input))
        except ValidationError as exc:
            return await _frog_fail(
                bus, run_id, tool_call,
                "schema_error", str(exc), frog_elapsed(),
            )

    if permission_manager is not None:
        async def _frog_emit_permission(raw: dict[str, Any]) -> None:
            await bus.frog_publish(FrogPermissionRequestedEvent(**raw, run_id=run_id))

        allowed, decision = await permission_manager.frog_check_and_wait(
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            session_id=session_id,
            event_emitter=_frog_emit_permission,
        )
        if allowed:
            if decision not in ("auto_allow",):
                await bus.frog_publish(
                    FrogPermissionGrantedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_frog_now(),
                    )
                )
        else:
            if decision != "auto_deny":
                await bus.frog_publish(
                    FrogPermissionDeniedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_frog_now(),
                    )
                )
            return await _frog_fail(
                bus, run_id, tool_call,
                "permission_denied",
                "Permission denied by user. You may not execute this command. "
                "Try an alternative approach or ask the user what to do.",
                frog_elapsed(),
            )

    for attempt in range(1, _MAX_RETRIES + 2):
        error_class: str | None = None
        error_message: str | None = None

        try:
            result = await asyncio.wait_for(
                tool.frog_invoke(dict(tool_call.input)), timeout=timeout
            )
            ms = frog_elapsed()

            if result.is_error:
                error_class = result.error_type or "runtime_error"
                error_message = result.content
            else:
                await bus.frog_publish(
                    FrogToolCallFinishedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        tool_name=tool_call.name,
                        elapsed_ms=ms,
                        output=result.content,
                        ts=_frog_now(),
                    )
                )
                return result

        except FrogRateLimitedError as exc:
            error_class = "rate_limited"
            error_message = str(exc)
        except TimeoutError:
            return await _frog_fail(
                bus, run_id, tool_call,
                "timeout", f"tool timed out after {timeout}s", frog_elapsed(),
                attempt=attempt,
            )
        except Exception as exc:
            error_class = "runtime_error"
            error_message = str(exc)

        assert error_class is not None and error_message is not None
        ms = frog_elapsed()

        if error_class in _RETRYABLE and attempt <= _MAX_RETRIES:
            await bus.frog_publish(
                FrogToolCallFailedEvent(
                    run_id=run_id,
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    error_class=error_class,
                    error_message=error_message,
                    elapsed_ms=ms,
                    attempt=attempt,
                    ts=_frog_now(),
                )
            )
            await asyncio.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))
            continue

        return await _frog_fail(
            bus, run_id, tool_call,
            error_class, error_message, ms,
            attempt=attempt,
        )

    # unreachable, but keeps mypy happy
    return FrogToolResult(content="internal error", is_error=True, error_type="runtime_error")

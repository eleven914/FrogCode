from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime
from typing import Any

from frog_code.core.events.bus import FrogEventBus
from frog_code.core.llm.base import FrogLLMProvider
from frog_code.core.llm.types import FrogLlmResponse
from frog_code.core.trace.record import FrogTraceRecord
from frog_code.core.trace.writer import FrogTraceWriter


def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


class FrogTracingProvider:
    # 包裹真实 FrogLLMProvider，在每次 frog_chat() 调用前后向 FrogTraceWriter 写入完整 API I/O 记录
    def __init__(
        self,
        inner: FrogLLMProvider,
        trace: FrogTraceWriter,
        *,
        include_payload: bool = True,
    ) -> None:
        self._inner = inner
        self._trace = trace
        self._include_payload = include_payload

    # 记录 CORE→LLM 请求，调用真实 provider，记录 LLM→CORE 响应（含延迟）
    async def frog_chat(
        self,
        messages: list[dict[str, object]],
        frog_tool_schemas: list[dict[str, object]],
        bus: FrogEventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> FrogLlmResponse:
        call_data: dict[str, Any]
        if self._include_payload:
            call_data = {"messages": messages, "frog_tool_schemas": frog_tool_schemas, "system": system}
        else:
            call_data = {
                "message_count": len(messages),
                "tool_count": len(frog_tool_schemas),
            }

        self._trace.frog_emit(
            FrogTraceRecord(
                ts=_frog_now(),
                direction="CORE→LLM",
                layer="llm",
                kind="api_call",
                run_id=run_id,
                step=step,
                data=call_data,
            )
        )

        t0 = time.monotonic()
        result = await self._inner.frog_chat(
            messages, frog_tool_schemas, bus, run_id, step=step, system=system
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        resp_data: dict[str, Any]
        if self._include_payload:
            resp_data = {
                "stop_reason": result.stop_reason,
                "text": result.text,
                "tool_calls": [dataclasses.asdict(tc) for tc in result.tool_calls],
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }
        else:
            resp_data = {
                "stop_reason": result.stop_reason,
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }

        self._trace.frog_emit(
            FrogTraceRecord(
                ts=_frog_now(),
                direction="LLM→CORE",
                layer="llm",
                kind="api_response",
                run_id=run_id,
                step=step,
                data=resp_data,
            )
        )

        return result

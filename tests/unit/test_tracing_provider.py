from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from frog_code.core.events.bus import FrogEventBus
from frog_code.core.llm.types import FrogLlmResponse, FrogUsageStats
from frog_code.core.trace.provider import FrogTracingProvider
from frog_code.core.trace.record import FrogTraceRecord
from frog_code.core.trace.writer import FrogTraceWriter


def _make_response(stop_reason: str = "end_turn") -> FrogLlmResponse:
    return FrogLlmResponse(
        stop_reason=stop_reason,
        tool_calls=[],
        text="done",
        usage=FrogUsageStats(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


async def _make_writer(tmp_path: Path) -> FrogTraceWriter:
    w = FrogTraceWriter(tmp_path / "trace.jsonl")
    await w.start()
    return w


# 功能：验证 frog_chat() 调用前后各 frog_emit 一条 CORE→LLM 和 LLM→CORE 记录
# 设计：mock inner provider，收集 FrogTraceWriter 收到的 record；断言 direction 顺序和 kind
@pytest.mark.asyncio
async def test_emits_api_call_and_api_response(tmp_path: Path) -> None:
    records: list[FrogTraceRecord] = []

    writer = await _make_writer(tmp_path)
    inner = AsyncMock()
    inner.frog_chat = AsyncMock(return_value=_make_response())

    original_emit = writer.frog_emit

    def capturing_emit(record: FrogTraceRecord) -> None:
        records.append(record)
        original_emit(record)

    writer.frog_emit = capturing_emit  # type: ignore[method-assign]

    provider = FrogTracingProvider(inner, writer)
    bus = FrogEventBus()

    await provider.frog_chat(
        messages=[{"role": "user", "content": "hi"}],
        frog_tool_schemas=[],
        bus=bus,
        run_id="run-1",
        step=2,
    )
    await writer.stop()

    assert len(records) == 2
    assert records[0].direction == "CORE→LLM"
    assert records[0].kind == "api_call"
    assert records[0].step == 2
    assert records[0].run_id == "run-1"
    assert records[1].direction == "LLM→CORE"
    assert records[1].kind == "api_response"
    assert records[1].step == 2


# 功能：验证 include_payload=True 时 data 包含完整 messages 和 response text
# 设计：断言 api_call.data["messages"] 存在且 api_response.data["text"] 存在
@pytest.mark.asyncio
async def test_include_payload_true_embeds_full_content(tmp_path: Path) -> None:
    records: list[FrogTraceRecord] = []

    writer = await _make_writer(tmp_path)
    inner = AsyncMock()
    inner.frog_chat = AsyncMock(return_value=_make_response())

    original_emit = writer.frog_emit

    def capturing_emit(r: FrogTraceRecord) -> None:
        records.append(r)
        original_emit(r)

    writer.frog_emit = capturing_emit  # type: ignore[method-assign]

    provider = FrogTracingProvider(inner, writer, include_payload=True)
    await provider.frog_chat(
        messages=[{"role": "user", "content": "hello"}],
        frog_tool_schemas=[{"name": "foo"}],
        bus=FrogEventBus(),
        run_id="r",
        step=1,
    )
    await writer.stop()

    assert "messages" in records[0].data
    assert "text" in records[1].data


# 功能：验证 include_payload=False 时只保留摘要字段，不包含完整 messages
# 设计：断言 api_call.data 只有 message_count/tool_count，不含 messages 键
@pytest.mark.asyncio
async def test_include_payload_false_uses_summary(tmp_path: Path) -> None:
    records: list[FrogTraceRecord] = []

    writer = await _make_writer(tmp_path)
    inner = AsyncMock()
    inner.frog_chat = AsyncMock(return_value=_make_response())

    original_emit = writer.frog_emit

    def capturing_emit(r: FrogTraceRecord) -> None:
        records.append(r)
        original_emit(r)

    writer.frog_emit = capturing_emit  # type: ignore[method-assign]

    provider = FrogTracingProvider(inner, writer, include_payload=False)
    await provider.frog_chat(
        messages=[{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
        frog_tool_schemas=[],
        bus=FrogEventBus(),
        run_id="r",
        step=1,
    )
    await writer.stop()

    call_data = records[0].data
    assert "messages" not in call_data
    assert call_data["message_count"] == 2

    resp_data = records[1].data
    assert "text" not in resp_data
    assert "latency_ms" in resp_data


# 功能：验证 FrogTracingProvider 将 step 参数透传给 inner provider
# 设计：用 AsyncMock 捕获 inner.frog_chat 的调用参数，断言 step 关键字参数正确
@pytest.mark.asyncio
async def test_step_forwarded_to_inner_provider(tmp_path: Path) -> None:
    writer = await _make_writer(tmp_path)
    inner = AsyncMock()
    inner.frog_chat = AsyncMock(return_value=_make_response())

    provider = FrogTracingProvider(inner, writer)
    await provider.frog_chat([], [], FrogEventBus(), "r", step=7)
    await writer.stop()

    inner.frog_chat.assert_called_once()
    _, kwargs = inner.frog_chat.call_args
    assert kwargs["step"] == 7

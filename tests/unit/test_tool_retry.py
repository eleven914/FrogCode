from __future__ import annotations

import pytest

import frog_code.core.tools.invocation as inv_mod
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.llm.types import FrogToolCallBlock
from frog_code.core.tools.base import FrogBaseTool, FrogToolResult
from frog_code.core.tools.errors import FrogRateLimitedError
from frog_code.core.tools.invocation import frog_invoke_tool
from frog_code.core.tools.registry import FrogToolRegistry

# --- stub tools --------------------------------------------------------------

class _FailNTimes(FrogBaseTool):
    """Fails with runtime_error for the first n calls, then succeeds."""
    name = "fail_n"
    description = "Fails n times then succeeds"
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, n: int, *, error_type: str = "runtime_error") -> None:
        self._remaining = n
        self._error_type = error_type

    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        if self._remaining > 0:
            self._remaining -= 1
            return FrogToolResult(content="transient error", is_error=True, error_type=self._error_type)
        return FrogToolResult(content="ok")


class _RateLimitedNTimes(FrogBaseTool):
    """Raises FrogRateLimitedError for the first n calls, then succeeds."""
    name = "rate_n"
    description = "Rate-limits n times then succeeds"
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, n: int) -> None:
        self._remaining = n

    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        if self._remaining > 0:
            self._remaining -= 1
            raise FrogRateLimitedError("429 Too Many Requests")
        return FrogToolResult(content="ok")


class _AlwaysFails(FrogBaseTool):
    name = "always_fail"
    description = "Always fails"
    input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, error_type: str = "runtime_error") -> None:
        self._error_type = error_type

    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        return FrogToolResult(content="permanent error", is_error=True, error_type=self._error_type)


# --- helper ------------------------------------------------------------------

def _frog_call(name: str) -> FrogToolCallBlock:
    return FrogToolCallBlock(id="t1", name=name, input={})


async def _run(tool: FrogBaseTool, *, monkeypatch: pytest.MonkeyPatch) -> tuple[FrogToolResult, list]:
    monkeypatch.setattr(inv_mod, "_RETRY_BASE_S", 0.0)
    registry = FrogToolRegistry()
    registry.register(tool)
    bus = FrogEventBus()
    events: list = []

    async def _collect(e: object) -> None:
        events.append(e)

    bus.frog_subscribe(_collect)
    result = await frog_invoke_tool(registry, _frog_call(tool.name), bus, run_id="r")
    return result, events


# --- tests -------------------------------------------------------------------


# 功能：验证 runtime_error 在首次失败后自动重试，最多 2 次，第 2 次成功时返回 ok
# 设计：_FailNTimes(1) 第一次返回 runtime_error，第二次成功；monkeypatch 消除 sleep 延迟
async def test_runtime_error_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    result, events = await _run(_FailNTimes(1), monkeypatch=monkeypatch)
    assert not result.is_error
    assert result.content == "ok"
    failed_events = [e for e in events if e.type == "frog.tool.call_failed"]  # type: ignore[attr-defined]
    assert len(failed_events) == 1
    assert failed_events[0].attempt == 1  # type: ignore[attr-defined]


# 功能：验证 rate_limited 异常触发重试，重试成功后返回正常结果
# 设计：_RateLimitedNTimes(1) 抛 FrogRateLimitedError 一次，第二次成功；检查 error_class 字段
async def test_rate_limited_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    result, events = await _run(_RateLimitedNTimes(1), monkeypatch=monkeypatch)
    assert not result.is_error
    failed_events = [e for e in events if e.type == "frog.tool.call_failed"]  # type: ignore[attr-defined]
    assert len(failed_events) == 1
    assert failed_events[0].error_class == "rate_limited"  # type: ignore[attr-defined]


# 功能：验证 runtime_error 超过 2 次重试后最终返回失败，attempt 字段递增
# 设计：_AlwaysFails 三次都失败；断言最终结果 is_error + 收到 3 个 failed 事件，attempt 为 1/2/3
async def test_runtime_error_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    result, events = await _run(_AlwaysFails("runtime_error"), monkeypatch=monkeypatch)
    assert result.is_error
    assert result.error_type == "runtime_error"
    failed_events = [e for e in events if e.type == "frog.tool.call_failed"]  # type: ignore[attr-defined]
    assert len(failed_events) == 3
    attempts = [e.attempt for e in failed_events]  # type: ignore[attr-defined]
    assert attempts == [1, 2, 3]


# 功能：验证 rate_limited 耗尽重试后最终返回失败，error_class 为 rate_limited
# 设计：_RateLimitedNTimes(10) 始终抛异常，断言 3 个 failed 事件且 error_class 统一
async def test_rate_limited_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    result, events = await _run(_RateLimitedNTimes(10), monkeypatch=monkeypatch)
    assert result.is_error
    assert result.error_type == "rate_limited"
    failed_events = [e for e in events if e.type == "frog.tool.call_failed"]  # type: ignore[attr-defined]
    assert len(failed_events) == 3
    assert all(e.error_class == "rate_limited" for e in failed_events)  # type: ignore[attr-defined]


# 功能：验证 schema_error 不触发重试，直接失败
# 设计：_AlwaysFails("schema_error") 首次即 schema 错误，断言只发一次 failed 事件且 attempt=1
async def test_schema_error_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    result, events = await _run(_AlwaysFails("schema_error"), monkeypatch=monkeypatch)
    assert result.is_error
    assert result.error_type == "schema_error"
    failed_events = [e for e in events if e.type == "frog.tool.call_failed"]  # type: ignore[attr-defined]
    assert len(failed_events) == 1
    assert failed_events[0].attempt == 1  # type: ignore[attr-defined]


# 功能：验证 timeout_error 不触发重试，直接失败
# 设计：SlowTool 配合极短超时触发 TimeoutError；断言只发一次 failed 事件，不重试（重试会再次超时）
async def test_timeout_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    class _SlowTool(FrogBaseTool):
        name = "slow"
        description = "sleeps"
        input_schema: dict[str, object] = {"type": "object", "properties": {}, "required": []}

        async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
            await asyncio.sleep(60)
            return FrogToolResult(content="done")

    monkeypatch.setattr(inv_mod, "_RETRY_BASE_S", 0.0)
    registry = FrogToolRegistry()
    registry.register(_SlowTool())
    bus = FrogEventBus()
    events: list = []

    async def _collect(e: object) -> None:
        events.append(e)

    bus.frog_subscribe(_collect)
    result = await frog_invoke_tool(registry, _frog_call("slow"), bus, run_id="r", timeout=0.05)

    assert result.is_error
    assert result.error_type == "timeout"
    failed_events = [e for e in events if e.type == "frog.tool.call_failed"]  # type: ignore[attr-defined]
    assert len(failed_events) == 1


# 功能：验证成功后的 tool.call_failed 事件中 error_class 字段存在且取值合法
# 设计：_FailNTimes(2) 两次失败后成功，检查所有 failed 事件的 error_class 均在合法枚举内
async def test_failed_event_has_valid_error_class(monkeypatch: pytest.MonkeyPatch) -> None:
    valid_classes = {"runtime_error", "timeout", "schema_error", "permission_denied", "rate_limited"}
    result, events = await _run(_FailNTimes(2), monkeypatch=monkeypatch)
    assert not result.is_error
    for e in events:
        if e.type == "frog.tool.call_failed":  # type: ignore[attr-defined]
            assert e.error_class in valid_classes  # type: ignore[attr-defined]

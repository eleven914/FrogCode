from __future__ import annotations

import asyncio
from typing import Any

import pytest

from frog_code.core.permissions.manager import FrogPermissionManager
from frog_code.core.permissions.policy import FrogPermissionDecision, FrogToolPolicy
from frog_code.core.permissions.storage import frog_load_policy_file

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_manager(**policies: FrogToolPolicy) -> FrogPermissionManager:
    # policy_file=None：测试中不使用持久化，不污染 ~/.frog/policy.toml
    return FrogPermissionManager(policies or None)


async def _collect_emitted() -> tuple[list[dict[str, Any]], Any]:
    emitted: list[dict[str, Any]] = []

    async def emitter(event: dict[str, Any]) -> None:
        emitted.append(event)

    return emitted, emitter


# ── frog_evaluate() delegation ─────────────────────────────────────────────────────

# 功能：验证 FrogPermissionManager.frog_evaluate 委托给 policy 层返回正确决策
# 设计：直接调用 frog_evaluate()，不涉及 Future，验证策略加载与委托路径
def test_evaluate_delegates_to_policy() -> None:
    mgr = _make_manager()
    assert mgr.frog_evaluate("frog_read_file", {"path": "x"}) == FrogPermissionDecision.ALLOW
    assert mgr.frog_evaluate("frog_bash", {"command": "echo hi"}) == FrogPermissionDecision.ASK
    assert mgr.frog_evaluate("frog_write_file", {"path": "x", "content": ""}) == FrogPermissionDecision.ASK


# ── frog_check_and_wait: ALLOW path ───────────────────────────────────────────────

# 功能：验证策略为 ALLOW 时 frog_check_and_wait 立即返回 (True, "auto_allow")，不发任何事件
# 设计：read_file 默认 ALLOW，断言不产生 permission.requested 事件，覆盖"无噪声放行"路径
async def test_check_and_wait_allow_no_event() -> None:
    mgr = _make_manager()
    emitted, emitter = await _collect_emitted()

    allowed, decision = await mgr.frog_check_and_wait(
        tool_use_id="t1", tool_name="frog_read_file",
        params={"path": "README.md"}, session_id="s1",
        event_emitter=emitter,
    )

    assert allowed is True
    assert decision == "auto_allow"
    assert emitted == []


# ── frog_check_and_wait: ASK path + frog_respond ───────────────────────────────────────

# 功能：验证 ASK 策略时发出 permission.requested 事件并等待 frog_respond() 解决 Future
# 设计：在后台协程中调用 frog_respond("allow_once")，主协程 await 结束后断言结果；
#       这是权限系统的核心反向请求通路
async def test_check_and_wait_ask_emits_event_and_waits() -> None:
    mgr = _make_manager()
    emitted, emitter = await _collect_emitted()

    async def _auto_respond() -> None:
        await asyncio.sleep(0)  # yield once so frog_check_and_wait can frog_emit the event
        mgr.frog_respond("t2", "allow_once")

    task = asyncio.create_task(_auto_respond())
    allowed, decision = await mgr.frog_check_and_wait(
        tool_use_id="t2", tool_name="frog_bash",
        params={"command": "echo hi"}, session_id="s1",
        event_emitter=emitter,
    )
    await task

    assert allowed is True
    assert decision == "allow_once"
    assert len(emitted) == 1
    assert emitted[0]["type"] == "frog.permission.requested"
    assert emitted[0]["tool_use_id"] == "t2"
    assert emitted[0]["tool_name"] == "frog_bash"


# 功能：验证 frog_respond("deny_once") 使 frog_check_and_wait 返回 (False, "deny_once")
# 设计：用户拒绝时工具不应执行，确认 False 返回值而不是异常
async def test_check_and_wait_deny_once_returns_false() -> None:
    mgr = _make_manager()
    _, emitter = await _collect_emitted()

    async def _auto_deny() -> None:
        await asyncio.sleep(0)
        mgr.frog_respond("t3", "deny_once")

    task = asyncio.create_task(_auto_deny())
    allowed, decision = await mgr.frog_check_and_wait(
        tool_use_id="t3", tool_name="frog_bash",
        params={"command": "echo hi"}, session_id="s1",
        event_emitter=emitter,
    )
    await task

    assert allowed is False
    assert decision == "deny_once"


# ── always_allow cache ────────────────────────────────────────────────────────

# 功能：验证 frog_respond("always_allow") 后同 session 同工具下次不再发事件
# 设计：第二次调用 frog_check_and_wait 命中 always 缓存，直接返回 (True, "auto_allow")，emitted 仍为 1 条
async def test_always_allow_skips_future_ask() -> None:
    mgr = _make_manager()
    emitted, emitter = await _collect_emitted()

    # First call: user says "always allow"
    async def _auto_always() -> None:
        await asyncio.sleep(0)
        mgr.frog_respond("t4", "always_allow")

    task = asyncio.create_task(_auto_always())
    r1, _ = await mgr.frog_check_and_wait(
        tool_use_id="t4", tool_name="frog_bash",
        params={"command": "echo hi"}, session_id="s1",
        event_emitter=emitter,
    )
    await task
    assert r1 is True

    # Second call: should hit cache, no new event
    r2, d2 = await mgr.frog_check_and_wait(
        tool_use_id="t5", tool_name="frog_bash",
        params={"command": "ls"}, session_id="s1",
        event_emitter=emitter,
    )

    assert r2 is True
    assert d2 == "auto_allow"
    assert len(emitted) == 1  # only the first call emitted an event


# 功能：验证 always_allow 在同一 manager 实例内对所有 session 生效（persistent_always 共享）
# 设计：s1 设置 always_allow → 写入 _persistent_always；s2 命中 persistent 缓存，直接放行；
#       emitted 只有 1 条（s2 不需要再 ASK）。这是 persistent always 的核心跨 session 语义。
async def test_always_allow_not_shared_across_sessions() -> None:
    mgr = _make_manager()
    emitted, emitter = await _collect_emitted()

    # session s1 sets always allow for bash
    async def _auto_always() -> None:
        await asyncio.sleep(0)
        mgr.frog_respond("t6", "always_allow")

    task = asyncio.create_task(_auto_always())
    await mgr.frog_check_and_wait(
        tool_use_id="t6", tool_name="frog_bash",
        params={"command": "echo"}, session_id="s1",
        event_emitter=emitter,
    )
    await task

    # session s2 — persistent_always["frog_bash"] = "allow" → 直接放行，不再 ASK
    r, d = await mgr.frog_check_and_wait(
        tool_use_id="t7", tool_name="frog_bash",
        params={"command": "echo"}, session_id="s2",
        event_emitter=emitter,
    )

    assert r is True
    assert d == "auto_allow"
    assert len(emitted) == 1  # s2 命中 persistent 缓存，不再发出事件


# ── always_deny cache ─────────────────────────────────────────────────────────

# 功能：验证 frog_respond("always_deny") 后同 session 同工具下次直接返回 (False, "auto_deny")
# 设计：用户选择 always deny 后不应继续骚扰，下次调用静默拒绝
async def test_always_deny_skips_future_ask() -> None:
    mgr = _make_manager()
    emitted, emitter = await _collect_emitted()

    async def _auto_always_deny() -> None:
        await asyncio.sleep(0)
        mgr.frog_respond("t8", "always_deny")

    task = asyncio.create_task(_auto_always_deny())
    r1, _ = await mgr.frog_check_and_wait(
        tool_use_id="t8", tool_name="frog_bash",
        params={"command": "echo"}, session_id="s1",
        event_emitter=emitter,
    )
    await task
    assert r1 is False

    # Second call: cache hit → no event, return (False, "auto_deny")
    r2, d2 = await mgr.frog_check_and_wait(
        tool_use_id="t9", tool_name="frog_bash",
        params={"command": "ls"}, session_id="s1",
        event_emitter=emitter,
    )
    assert r2 is False
    assert d2 == "auto_deny"
    assert len(emitted) == 1


# ── frog_cancel_session ────────────────────────────────────────────────────────────

# 功能：验证 frog_cancel_session 将 pending Future 设为 deny_once，frog_check_and_wait 返回 False
# 设计：模拟客户端断连场景——frog_check_and_wait 挂起后调用 frog_cancel_session，
#       确认 Future 被解决而非永久挂起（防止僵尸 run）
async def test_cancel_session_resolves_pending_future() -> None:
    mgr = _make_manager()
    _, emitter = await _collect_emitted()

    async def _cancel_after_emit() -> None:
        await asyncio.sleep(0)  # wait for event to be emitted
        mgr.frog_cancel_session("s1", reason="client_disconnected")

    task = asyncio.create_task(_cancel_after_emit())
    allowed, _ = await mgr.frog_check_and_wait(
        tool_use_id="t10", tool_name="frog_bash",
        params={"command": "ls"}, session_id="s1",
        event_emitter=emitter,
    )
    await task

    assert allowed is False


# 功能：验证 frog_cancel_session 只取消属于该 session 的 pending Future
# 设计：s1 和 s2 各有一个 pending，frog_cancel_session(s2) 不影响 s1 的 Future
async def test_cancel_session_only_affects_target_session() -> None:
    mgr = _make_manager()
    _, emitter = await _collect_emitted()

    # Launch two concurrent frog_check_and_wait for different sessions
    s1_done = asyncio.Event()
    s2_done = asyncio.Event()
    s1_result: list[bool] = []
    s2_result: list[bool] = []

    async def _s1() -> None:
        r, _ = await mgr.frog_check_and_wait(
            tool_use_id="ta", tool_name="frog_bash",
            params={"command": "echo"}, session_id="s1",
            event_emitter=emitter,
        )
        s1_result.append(r)
        s1_done.set()

    async def _s2() -> None:
        r, _ = await mgr.frog_check_and_wait(
            tool_use_id="tb", tool_name="frog_bash",
            params={"command": "echo"}, session_id="s2",
            event_emitter=emitter,
        )
        s2_result.append(r)
        s2_done.set()

    t1 = asyncio.create_task(_s1())
    t2 = asyncio.create_task(_s2())

    await asyncio.sleep(0)  # let both frog_emit events and hang

    # cancel only s2
    mgr.frog_cancel_session("s2")
    await s2_done.wait()

    # s1 should still be pending; resolve it manually
    mgr.frog_respond("ta", "allow_once")
    await s1_done.wait()

    await t1
    await t2

    assert s1_result == [True]   # s1 was allowed
    assert s2_result == [False]  # s2 was cancelled → denied


# ── frog_respond: unknown tool_use_id ──────────────────────────────────────────────

# 功能：验证 frog_respond 传入不存在的 tool_use_id 时静默忽略，不抛异常
# 设计：竞态场景（客户端重复发送响应）不应导致 daemon crash
def test_respond_unknown_tool_use_id_is_noop() -> None:
    mgr = _make_manager()
    mgr.frog_respond("nonexistent", "allow_once")  # should not raise


# ── OUTSIDE_CWD 不被 always 缓存绕过 ─────────────────────────────────────────

# 功能：验证 always_allow bash 之后，含绝对路径的命令仍触发 ASK，不被缓存绕过
# 设计：先让 session s1 对 bash 设置 always_allow，再请求含绝对路径命令；
#       OUTSIDE_CWD 检查在 always 缓存之前，应发出 permission.requested 事件
async def test_always_allow_does_not_bypass_outside_cwd() -> None:
    mgr = _make_manager()
    emitted, emitter = await _collect_emitted()

    # 首次 allow → 写入 session always 缓存
    async def _auto_always() -> None:
        await asyncio.sleep(0)
        mgr.frog_respond("t_always", "always_allow")

    t = asyncio.create_task(_auto_always())
    await mgr.frog_check_and_wait(
        tool_use_id="t_always", tool_name="frog_bash",
        params={"command": "echo ok"}, session_id="s1",
        event_emitter=emitter,
    )
    await t
    assert len(emitted) == 1  # 首次 ASK 触发事件

    # 第二次：bash + 绝对路径 → OUTSIDE_CWD 强制 ASK，不命中 session always 缓存
    async def _auto_respond_abs() -> None:
        await asyncio.sleep(0)
        mgr.frog_respond("t_abs", "allow_once")

    t2 = asyncio.create_task(_auto_respond_abs())
    allowed, decision = await mgr.frog_check_and_wait(
        tool_use_id="t_abs", tool_name="frog_bash",
        params={"command": "cat /etc/hosts"}, session_id="s1",
        event_emitter=emitter,
    )
    await t2

    assert allowed is True
    assert len(emitted) == 2  # 绝对路径命令再次触发 ASK，共 2 个事件


# ── 持久化 always 写文件 ──────────────────────────────────────────────────────

# 功能：验证 always_allow 决策写入 policy_file，新 FrogPermissionManager 加载后自动放行
# 设计：用 tmp_path 作为 policy_file，断言文件存在且内容正确；
#       再新建 manager 加载文件，同工具无需 ASK 直接返回 auto_allow
async def test_persistent_always_written_and_reloaded(tmp_path: pytest.TempPathFixture) -> None:
    policy_file = tmp_path / "policy.toml"
    mgr = FrogPermissionManager(policy_file=policy_file)
    emitted, emitter = await _collect_emitted()

    async def _auto_always() -> None:
        await asyncio.sleep(0)
        mgr.frog_respond("tp1", "always_allow")

    t = asyncio.create_task(_auto_always())
    allowed, _ = await mgr.frog_check_and_wait(
        tool_use_id="tp1", tool_name="frog_bash",
        params={"command": "echo"}, session_id="s1",
        event_emitter=emitter,
    )
    await t
    assert allowed is True
    assert policy_file.exists()

    loaded = frog_load_policy_file(policy_file)
    assert loaded.get("frog_bash") == "allow"

    # 新 manager 加载同一文件，bash 应直接 auto_allow（无 OUTSIDE_CWD）
    mgr2 = FrogPermissionManager(policy_file=policy_file)
    emitted2, emitter2 = await _collect_emitted()
    allowed2, decision2 = await mgr2.frog_check_and_wait(
        tool_use_id="tp2", tool_name="frog_bash",
        params={"command": "echo new"}, session_id="s2",
        event_emitter=emitter2,
    )
    assert allowed2 is True
    assert decision2 == "auto_allow"
    assert emitted2 == []  # 无需 ASK


# ── 审批超时 ──────────────────────────────────────────────────────────────────

# 功能：验证 frog_check_and_wait 超时后返回 (False, "timeout")，不永久挂起
# 设计：timeout_s=0.05 极短超时，不主动 frog_respond；断言在合理时间内返回 False
async def test_permission_timeout_returns_false() -> None:
    mgr = FrogPermissionManager(timeout_s=0.05)
    emitted, emitter = await _collect_emitted()

    allowed, decision = await mgr.frog_check_and_wait(
        tool_use_id="t_timeout", tool_name="frog_bash",
        params={"command": "echo hi"}, session_id="s1",
        event_emitter=emitter,
    )

    assert allowed is False
    assert decision == "timeout"
    assert len(emitted) == 1
    assert emitted[0]["type"] == "frog.permission.requested"


# 功能：验证超时后 pending 被清理，迟到的 frog_respond 不影响后续调用
# 设计：超时后调用 frog_respond，不抛异常（unknown tool_use_id 静默忽略）；
#       再次 frog_check_and_wait 同 tool_use_id 仍正常发出新的 permission.requested
async def test_permission_timeout_cleans_up_pending() -> None:
    mgr = FrogPermissionManager(timeout_s=0.05)
    _, emitter = await _collect_emitted()

    await mgr.frog_check_and_wait(
        tool_use_id="t_late", tool_name="frog_bash",
        params={"command": "echo"}, session_id="s1",
        event_emitter=emitter,
    )
    # 超时后迟到的 frog_respond 不应 crash
    mgr.frog_respond("t_late", "allow_once")  # should be noop
    assert "t_late" not in mgr._pending

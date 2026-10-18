from __future__ import annotations

from pathlib import Path

import pytest

from frog_code.core.bus.envelope import FrogHandlerError
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.runner import FrogRunOutcome
from frog_code.core.session.manager import SESSION_CLOSED, SESSION_NOT_FOUND, FrogSessionManager
from frog_code.core.session.model import FrogSession
from frog_code.core.session.store import FrogSessionStore


class _Runner:
    # 模拟 FrogAgentRunner，将 run 新消息写入 thread 后返回成功
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
        assert run_id is not None
        assert session is not None
        assert store is not None
        store.frog_append_messages(
            session.id,
            [{"role": "assistant", "content": [{"type": "text", "text": f"done {goal}"}]}],
            run_id,
        )
        return FrogRunOutcome(status="success", result="done", reason=None)


# 功能：验证 create 会创建 active session、写入 meta 并发布 session.created 事件
# 设计：用真实 FrogSessionStore + FrogEventBus 收集事件，覆盖 manager 与 store/bus 的协作边界
async def test_create_session_writes_meta_and_event(tmp_path: Path) -> None:
    events: list[object] = []
    bus = FrogEventBus()

    async def collect(event: object) -> None:
        events.append(event)

    bus.frog_subscribe(collect)
    store = FrogSessionStore(tmp_path)
    manager = FrogSessionManager(store, lambda: _Runner(), bus)  # type: ignore[arg-type]

    session = await manager.create("chat", "title")

    assert session.status == "active"
    assert store.frog_read_meta(session.id).title == "title"
    assert [e.type for e in events] == ["frog.session.created"]  # type: ignore[attr-defined]


# 功能：验证 frog_chat session 处理一条消息后进入 waiting_for_input，并保留 user/assistant thread
# 设计：mock runner 主动追加 assistant 消息，确认 frog_send_message 负责 user 消息、状态流转和 run_id 记录
async def test_send_message_chat_enters_waiting_and_writes_thread(tmp_path: Path) -> None:
    store = FrogSessionStore(tmp_path)
    manager = FrogSessionManager(store, lambda: _Runner(), FrogEventBus())  # type: ignore[arg-type]
    session = await manager.create("chat")

    run_id = await manager.frog_send_message(session.id, "hello")

    loaded = store.frog_read_meta(session.id)
    assert loaded.status == "waiting_for_input"
    assert loaded.run_ids == [run_id]
    messages = store.frog_read_messages(session.id)
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1]["role"] == "assistant"


# 功能：验证 one_shot session 在单次消息完成后自动 closed
# 设计：复用 mock runner 的成功路径，聚焦 mode 对最终状态的影响，保证 frog frog-run 的统一路径正确
async def test_one_shot_auto_closes(tmp_path: Path) -> None:
    store = FrogSessionStore(tmp_path)
    manager = FrogSessionManager(store, lambda: _Runner(), FrogEventBus())  # type: ignore[arg-type]
    session = await manager.create("one_shot")

    await manager.frog_send_message(session.id, "hello")

    assert store.frog_read_meta(session.id).status == "closed"


# 功能：验证不存在的 session_id 返回 session_not_found 错误码
# 设计：直接调用 frog_get_history 的查找路径，断言 FrogHandlerError code，覆盖 IPC handler 可结构化返回错误
async def test_missing_session_raises_handler_error(tmp_path: Path) -> None:
    manager = FrogSessionManager(FrogSessionStore(tmp_path), lambda: _Runner(), FrogEventBus())  # type: ignore[arg-type]
    with pytest.raises(FrogHandlerError) as exc:
        await manager.frog_get_history("missing")
    assert exc.value.code == SESSION_NOT_FOUND


# 功能：验证 closed session 不能继续 frog_send_message
# 设计：先显式 close，再发送消息，断言 session_closed 错误码，覆盖状态机拒绝路径
async def test_closed_session_rejects_message(tmp_path: Path) -> None:
    store = FrogSessionStore(tmp_path)
    manager = FrogSessionManager(store, lambda: _Runner(), FrogEventBus())  # type: ignore[arg-type]
    session = await manager.create("chat")
    await manager.close(session.id)

    with pytest.raises(FrogHandlerError) as exc:
        await manager.frog_send_message(session.id, "again")
    assert exc.value.code == SESSION_CLOSED

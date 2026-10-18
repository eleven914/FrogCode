from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from frog_code.core.bus.envelope import FrogHandlerError
from frog_code.core.bus.events import (
    FrogSessionClosedEvent,
    FrogSessionCreatedEvent,
    FrogSessionMessageReceivedEvent,
    FrogSessionResumedEvent,
    FrogSessionWaitingForInputEvent,
    FrogSkillInvokedEvent,
)
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.runs import frog_new_run_id
from frog_code.core.session.model import FrogSession, SessionMode
from frog_code.core.session.store import FrogSessionStore
from frog_code.core.skills.loader import FrogSkillLoader

if TYPE_CHECKING:
    from frog_code.core.llm.base import FrogLLMProvider
    from frog_code.core.runner import FrogAgentRunner

SESSION_NOT_FOUND = -32010
SESSION_CLOSED = -32011
SESSION_BUSY = -32012


# 返回当前 UTC 时间的 ISO 8601 字符串
def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


class FrogSessionManager:
    # 初始化会话管理器，接入文件存储、runner 工厂、事件总线和可选的 LLM provider（用于手动压缩）
    def __init__(
        self,
        store: FrogSessionStore,
        runner_factory: Callable[[], FrogAgentRunner],
        bus: FrogEventBus,
        provider: FrogLLMProvider | None = None,
    ) -> None:
        self._store = store
        self._runner_factory = runner_factory
        self._bus = bus
        self._provider = provider
        self._sessions: dict[str, FrogSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._skill_loader = FrogSkillLoader()

    # 创建新 session 并写入 meta.json
    async def create(self, mode: SessionMode, title: str = "") -> FrogSession:
        sid = f"sess-{uuid.uuid4().hex[:12]}"
        ts = _frog_now()
        session = FrogSession(
            id=sid,
            mode=mode,
            status="active",
            title=title,
            created_at=ts,
            updated_at=ts,
            run_ids=[],
        )
        self._sessions[sid] = session
        self._locks[sid] = asyncio.Lock()
        self._store.frog_write_meta(session)
        await self._bus.frog_publish(FrogSessionCreatedEvent(session_id=sid, mode=mode, ts=ts))
        return session

    # 处理用户消息，追加 thread 并启动一次 agent run
    async def frog_send_message(self, sid: str, content: str, *, run_id: str | None = None) -> str:
        session = self._frog_get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise FrogHandlerError(SESSION_BUSY, "session busy")

        async with lock:
            if session.status == "closed":
                raise FrogHandlerError(SESSION_CLOSED, "session already closed")

            if session.status == "waiting_for_input":
                await self._bus.frog_publish(FrogSessionResumedEvent(session_id=sid, ts=_frog_now()))

            self._store.frog_append_message(sid, "user", content)
            await self._bus.frog_publish(
                FrogSessionMessageReceivedEvent(session_id=sid, content=content, ts=_frog_now())
            )

            if not session.title:
                session.title = content[:40]

            run_id = run_id or frog_new_run_id()
            session.run_ids.append(run_id)
            session.updated_at = _frog_now()
            self._store.frog_write_meta(session)

            # FrogSkill 解析：检测 "/" 前缀，展开为系统提示覆盖和工具白名单
            goal = content
            system_prompt_override: str | None = None
            tool_whitelist: list[str] | None = None
            if content.startswith("/"):
                parts = content[1:].split(None, 1)
                skill_name = parts[0]
                arguments = parts[1] if len(parts) > 1 else ""
                skill = self._skill_loader.resolve(skill_name)
                if skill is not None:
                    goal = self._skill_loader.frog_render_prompt(skill, arguments)
                    system_prompt_override = skill.system_prompt_template
                    tool_whitelist = skill.allowed_tools or None
                    await self._bus.frog_publish(
                        FrogSkillInvokedEvent(
                            skill_name=skill_name,
                            arguments=arguments,
                            run_id=run_id,
                            ts=_frog_now(),
                        )
                    )

            runner = self._runner_factory()
            await runner.frog_run_and_capture(
                goal,
                run_id=run_id,
                session=session,
                store=self._store,
                system_prompt_override=system_prompt_override,
                tool_whitelist=tool_whitelist,
            )

            session.updated_at = _frog_now()
            if session.mode == "one_shot":
                session.status = "closed"
                await self._bus.frog_publish(FrogSessionClosedEvent(session_id=sid, ts=session.updated_at))
            else:
                session.status = "waiting_for_input"
                await self._bus.frog_publish(
                    FrogSessionWaitingForInputEvent(
                        session_id=sid,
                        last_run_id=run_id,
                        ts=session.updated_at,
                    )
                )
            self._store.frog_write_meta(session)
            return run_id

    # 关闭指定 session 并更新 meta.json
    async def close(self, sid: str) -> None:
        session = self._frog_get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise FrogHandlerError(SESSION_BUSY, "session busy")
        async with lock:
            session.status = "closed"
            session.updated_at = _frog_now()
            self._store.frog_write_meta(session)
            await self._bus.frog_publish(FrogSessionClosedEvent(session_id=sid, ts=session.updated_at))

    # 手动压缩指定 session 的 thread，将摘要持久化写入 thread.jsonl
    async def frog_compact(self, sid: str, focus: str = "") -> Any:
        self._frog_get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise FrogHandlerError(SESSION_BUSY, "session busy")
        if self._provider is None:
            raise FrogHandlerError(-32020, "provider not available for compaction")
        async with lock:
            from frog_code.core.bus.commands import FrogSessionCompactResult
            from frog_code.core.compact.compactor import FrogCompactor
            messages = self._store.frog_read_messages(sid)
            frog_session_dir = self._store.frog_session_dir(sid)
            compactor = FrogCompactor(self._bus, frog_session_dir, sid)
            result = await compactor.frog_compact_messages(messages, self._provider, focus=focus)
            if result is None:
                raise FrogHandlerError(-32021, "compaction failed or not beneficial")
            self._store.frog_write_compacted(sid, [
                {"role": "user", "content": result.summary_text},
                {"role": "assistant", "content": "Understood, I'll continue from this summary."},
            ])
            return FrogSessionCompactResult(
                summary_tokens=result.summary_tokens,
                saved_tokens=max(0, result.original_token_estimate - result.summary_tokens),
            )

    # 读取指定 session 的完整 thread 历史
    async def frog_get_history(self, sid: str) -> list[dict[str, Any]]:
        self._frog_get_session(sid)
        return self._store.frog_read_messages(sid)

    # 从内存索引取 session，不存在时抛 JSON-RPC 结构化错误
    def _frog_get_session(self, sid: str) -> FrogSession:
        session = self._sessions.get(sid)
        if session is None:
            raise FrogHandlerError(SESSION_NOT_FOUND, "session not found")
        return session

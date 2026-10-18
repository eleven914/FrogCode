from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from frog_code.core.bus.envelope import FrogEventPushEnvelope
from frog_code.core.trace.record import FrogTraceRecord
from frog_code.core.trace.writer import FrogTraceWriter

logger = logging.getLogger(__name__)


def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class FrogSubscription:
    sub_id: str
    writer: asyncio.StreamWriter
    topics: list[str]
    scope: str


class FrogIpcEventBroadcaster:
    def __init__(self, trace: FrogTraceWriter | None = None) -> None:
        self._subscriptions: list[FrogSubscription] = []
        self._trace = trace

    # 注册一个客户端订阅，返回 subscription_id
    def frog_subscribe(
        self,
        writer: asyncio.StreamWriter,
        topics: list[str],
        scope: str = "global",
    ) -> str:
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"
        sub = FrogSubscription(sub_id=sub_id, writer=writer, topics=topics, scope=scope)
        self._subscriptions.append(sub)
        return sub_id

    # 移除指定 writer 的所有订阅
    def frog_unsubscribe(self, writer: asyncio.StreamWriter) -> None:
        self._subscriptions = [s for s in self._subscriptions if s.writer is not writer]

    # 将事件推送到所有匹配的订阅客户端，写入失败时延迟清理死连接
    async def frog_handle(self, event: BaseModel) -> None:
        event_dict = event.model_dump()
        event_type: str = event_dict.get("type", "")
        run_id: str | None = event_dict.get("run_id")

        dead: list[asyncio.StreamWriter] = []

        for sub in list(self._subscriptions):
            if not self._frog_matches_topic(event_type, sub.topics):
                continue
            if not self._frog_matches_scope(run_id, sub.scope):
                continue
            try:
                envelope = FrogEventPushEnvelope(event=event_dict)
                sub.writer.write(envelope.model_dump_json().encode() + b"\n")
                await sub.writer.drain()
                if self._trace is not None:
                    client_id = str(sub.writer.get_extra_info("peername", "<unknown>"))
                    self._trace.frog_emit(
                        FrogTraceRecord(
                            ts=_frog_now(),
                            direction="CORE→CLIENT",
                            layer="ipc",
                            kind="push",
                            run_id=run_id,
                            client_id=client_id,
                            data={"sub_id": sub.sub_id, "event_type": event_type},
                        )
                    )
            except (ConnectionResetError, BrokenPipeError, OSError):
                logger.debug("dead connection for sub %s, scheduling cleanup", sub.sub_id)
                dead.append(sub.writer)

        for writer in dead:
            self.frog_unsubscribe(writer)

    # 检查事件类型是否匹配订阅的 topic 列表（支持 fnmatch glob 模式）
    @staticmethod
    def _frog_matches_topic(event_type: str, topics: list[str]) -> bool:
        return any(fnmatch.fnmatch(event_type, pattern) for pattern in topics)

    # 检查事件 run_id 是否匹配订阅的 scope（global 全通，run:<id> 精确匹配）
    @staticmethod
    def _frog_matches_scope(run_id: str | None, scope: str) -> bool:
        if scope == "global":
            return True
        if scope.startswith("run:"):
            return run_id == scope[4:]
        return False

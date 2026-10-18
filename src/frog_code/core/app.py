from __future__ import annotations

import asyncio
import datetime
import fnmatch
import json
import logging
import os
import signal
import time
from datetime import UTC
from pathlib import Path
from typing import Any

from pydantic import BaseModel

import frog_code
from frog_code.core.bus.commands import (
    FrogAgentRunCommand,
    FrogAgentRunResult,
    FrogEventSubscribeCommand,
    FrogEventSubscribeResult,
    FrogPermissionRespondCommand,
    FrogPermissionRespondResult,
    FrogPongResult,
    FrogSessionCloseCommand,
    FrogSessionCloseResult,
    FrogSessionCompactCommand,
    FrogSessionCompactResult,
    FrogSessionCreateCommand,
    FrogSessionCreateResult,
    FrogSessionGetHistoryCommand,
    FrogSessionGetHistoryResult,
    FrogSessionSendMessageCommand,
    FrogSessionSendMessageResult,
)
from frog_code.core.bus.envelope import FrogEventPushEnvelope
from frog_code.core.config import FrogConfig, frog_get_config
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.llm.provider import FrogAnthropicProvider
from frog_code.core.logging_setup import frog_setup_logging
from frog_code.core.mcp.server import FrogMcpServerManager
from frog_code.core.permissions.manager import FrogPermissionManager
from frog_code.core.permissions.storage import frog_load_policy_file
from frog_code.core.runner import FrogAgentRunner
from frog_code.core.runs import frog_events_file, frog_new_run_id
from frog_code.core.session import FrogSessionManager, FrogSessionStore
from frog_code.core.trace.record import FrogTraceRecord
from frog_code.core.trace.writer import FrogTraceWriter
from frog_code.core.transport.ipc_broadcaster import FrogIpcEventBroadcaster
from frog_code.core.transport.socket_server import FrogSocketServer, frog_get_connection_writer

logger = logging.getLogger(__name__)


def _frog_now() -> str:
    return datetime.datetime.now(UTC).isoformat()


class FrogCoreApp:
    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._bus = FrogEventBus()
        self._broadcaster: FrogIpcEventBroadcaster | None = None
        self._trace: FrogTraceWriter | None = None
        self._config: FrogConfig | None = None
        self._running_runs: set[asyncio.Task[Any]] = set()
        self._sessions: FrogSessionManager | None = None
        self._permission_manager: FrogPermissionManager | None = None
        self._mcp_manager: FrogMcpServerManager | None = None

    # 处理 core.ping 请求，返回服务版本、运行时长和接收时间
    async def _frog_ping_handler(self, params: dict[str, Any]) -> FrogPongResult:
        client = params.get("client", "unknown")
        logger.debug("ping from %s", client)
        return FrogPongResult(
            server_version=frog_code.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 将 FrogEventBus 事件写入 trace（作为 FrogEventBus 订阅者）
    async def _frog_trace_event_handler(self, event: BaseModel) -> None:
        assert self._trace is not None
        event_dict = event.model_dump()
        self._trace.frog_emit(
            FrogTraceRecord(
                ts=_frog_now(),
                direction="CORE",
                layer="event",
                kind="event",
                run_id=event_dict.get("run_id"),
                data=event_dict,
            )
        )

    # 启动一次 agent run：异步创建 FrogAgentRunner 并立即返回 run_id
    async def _frog_agent_run_handler(self, params: dict[str, Any]) -> FrogAgentRunResult:
        assert self._sessions is not None
        cmd = FrogAgentRunCommand.model_validate(params)
        session = await self._sessions.create(mode="one_shot", title=cmd.goal[:40])
        run_id = frog_new_run_id()
        run_task = asyncio.create_task(
            self._sessions.frog_send_message(session.id, cmd.goal, run_id=run_id)
        )
        self._running_runs.add(run_task)
        run_task.add_done_callback(self._running_runs.discard)
        return FrogAgentRunResult(run_id=run_id)

    # 创建 frog_chat 或 one_shot session，并返回 session_id
    async def _frog_session_create_handler(self, params: dict[str, Any]) -> FrogSessionCreateResult:
        assert self._sessions is not None
        cmd = FrogSessionCreateCommand.model_validate(params)
        session = await self._sessions.create(mode=cmd.mode, title=cmd.title)
        return FrogSessionCreateResult(session_id=session.id, status=session.status)

    # 向 session 发送一条用户消息并同步等待对应 run 完成
    async def _frog_session_send_handler(self, params: dict[str, Any]) -> FrogSessionSendMessageResult:
        assert self._sessions is not None
        cmd = FrogSessionSendMessageCommand.model_validate(params)
        run_id = await self._sessions.frog_send_message(cmd.session_id, cmd.content)
        return FrogSessionSendMessageResult(run_id=run_id)

    # 返回 session 的完整 Anthropic messages 历史
    async def _frog_session_history_handler(self, params: dict[str, Any]) -> FrogSessionGetHistoryResult:
        assert self._sessions is not None
        cmd = FrogSessionGetHistoryCommand.model_validate(params)
        messages = await self._sessions.frog_get_history(cmd.session_id)
        return FrogSessionGetHistoryResult(messages=messages)

    # 接收客户端权限审批响应，resolve 对应挂起的 Future
    async def _frog_permission_respond_handler(self, params: dict[str, Any]) -> FrogPermissionRespondResult:
        cmd = FrogPermissionRespondCommand.model_validate(params)
        logger.info(
            "frog.permission.respond received tool_use_id=%s decision=%s",
            cmd.tool_use_id, cmd.decision,
        )
        if self._permission_manager is None:
            logger.error("frog.permission.respond: FrogPermissionManager not initialized")
            return FrogPermissionRespondResult()
        self._permission_manager.frog_respond(cmd.tool_use_id, cmd.decision)
        return FrogPermissionRespondResult()

    # 手动压缩 session thread，将摘要持久化写入 thread.jsonl
    async def _frog_session_compact_handler(self, params: dict[str, Any]) -> FrogSessionCompactResult:
        assert self._sessions is not None
        cmd = FrogSessionCompactCommand.model_validate(params)
        result = await self._sessions.frog_compact(cmd.session_id, cmd.focus)
        return result  # type: ignore[no-any-return]

    # 关闭 session 并返回 closed 状态
    async def _frog_session_close_handler(self, params: dict[str, Any]) -> FrogSessionCloseResult:
        assert self._sessions is not None
        cmd = FrogSessionCloseCommand.model_validate(params)
        await self._sessions.close(cmd.session_id)
        return FrogSessionCloseResult(status="closed")

    # 注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    async def _frog_subscribe_handler(self, params: dict[str, Any]) -> FrogEventSubscribeResult:
        cmd = FrogEventSubscribeCommand.model_validate(params)
        writer = frog_get_connection_writer()

        replayed_count = 0
        if cmd.replay_from_run is not None:
            replayed_count = await self._frog_replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        assert self._broadcaster is not None
        sub_id = self._broadcaster.frog_subscribe(writer, cmd.topics, cmd.scope)
        return FrogEventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    async def _frog_replay_events(
        self,
        run_id: str,
        writer: asyncio.StreamWriter,
        topics: list[str],
    ) -> int:
        path = frog_events_file(run_id)
        if not path.exists():
            for candidate in Path("~/.frog/sessions").expanduser().glob(
                f"*/runs/{run_id}/events.jsonl"
            ):
                path = candidate
                break
        if not path.exists():
            return 0

        count = 0
        for line in path.read_text().splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type: str = event.get("type", "")
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            envelope = FrogEventPushEnvelope(event=event)
            writer.write(envelope.model_dump_json().encode() + b"\n")
            count += 1

        if count:
            await writer.drain()
        return count

    # 启动守护进程：加载配置、初始化日志、启动 trace、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        self._start_time = time.monotonic()
        self._config = frog_get_config()
        frog_setup_logging(self._config)

        if self._config.trace.enabled:
            trace_path = Path(self._config.trace.file).expanduser()
            self._trace = FrogTraceWriter(trace_path)
            await self._trace.start()
            self._bus.frog_subscribe(self._frog_trace_event_handler)

        policy_file = Path("~/.frog/policy.toml").expanduser()
        self._permission_manager = FrogPermissionManager(
            policy_file=policy_file,
            timeout_s=self._config.permission.timeout_s,
        )
        logger.info(
            "permission manager: timeout_s=%.1f  persistent=%d entries",
            self._config.permission.timeout_s,
            len(frog_load_policy_file(policy_file)),
        )

        self._broadcaster = FrogIpcEventBroadcaster(trace=self._trace)
        self._bus.frog_subscribe(self._broadcaster.frog_handle)
        sessions_root = Path("~/.frog/sessions").expanduser()
        store = FrogSessionStore(sessions_root)
        assert self._config is not None
        # Core 的 ping、会话管理等本地能力不依赖模型密钥。仅在配置了密钥时
        # 初始化手动压缩 provider；真正执行 Agent 时 runner 仍会按需校验密钥。
        compact_provider = (
            FrogAnthropicProvider(self._config.llm.default_model)
            if os.environ.get("ANTHROPIC_API_KEY")
            else None
        )

        self._mcp_manager = FrogMcpServerManager()
        if self._config.mcp.servers:
            logger.info("mcp: starting %d server(s)", len(self._config.mcp.servers))
            await self._mcp_manager.frog_start_all(self._config.mcp.servers)

        self._sessions = FrogSessionManager(
            store,
            runner_factory=lambda: FrogAgentRunner(
                self._config,  # type: ignore[arg-type]
                bus=self._bus,
                trace=self._trace,
                permission_manager=self._permission_manager,
                mcp_manager=self._mcp_manager,
            ),
            bus=self._bus,
            provider=compact_provider,
        )

        server = FrogSocketServer(
            self._config.host,
            self._config.port,
            self._broadcaster,
            trace=self._trace,
        )
        server.register("frog.core.ping", self._frog_ping_handler)
        server.register("frog.agent.run", self._frog_agent_run_handler)
        server.register("frog.event.subscribe", self._frog_subscribe_handler)
        server.register("frog.session.create", self._frog_session_create_handler)
        server.register("frog.session.send_message", self._frog_session_send_handler)
        server.register("frog.session.get_history", self._frog_session_history_handler)
        server.register("frog.session.close", self._frog_session_close_handler)
        server.register("frog.permission.respond", self._frog_permission_respond_handler)
        server.register("frog.session.compact", self._frog_session_compact_handler)

        addr = await server.start()
        logger.info("frog-core %s listening addr=%s", frog_code.__version__, addr)
        logger.info("config: %s", self._config)

        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        try:
            loop.add_signal_handler(signal.SIGINT, shutdown.set)
            loop.add_signal_handler(signal.SIGTERM, shutdown.set)
        except NotImplementedError:
            # Windows ProactorEventLoop 不实现 add_signal_handler，改用同步信号回调投递事件
            def frog_request_shutdown(_signum: int, _frame: Any) -> None:
                loop.call_soon_threadsafe(shutdown.set)

            signal.signal(signal.SIGINT, frog_request_shutdown)
            signal.signal(signal.SIGTERM, frog_request_shutdown)

        await shutdown.wait()

        logger.info("shutting down")
        for run_task in list(self._running_runs):
            run_task.cancel()
        if self._running_runs:
            await asyncio.gather(*self._running_runs, return_exceptions=True)
        if self._mcp_manager is not None:
            await self._mcp_manager.frog_stop_all()
        await server.stop()
        if self._trace is not None:
            await self._trace.stop()


# 同步入口：启动 FrogCoreApp 事件循环
def frog_run() -> None:
    asyncio.run(FrogCoreApp().run())

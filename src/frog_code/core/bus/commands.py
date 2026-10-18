from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator

from frog_code.core.session.model import SessionMode, SessionStatus


class FrogPingCommand(BaseModel):
    type: Literal["frog.core.ping"] = "frog.core.ping"
    client: str


class FrogPongResult(BaseModel):
    server_version: str
    uptime_ms: int
    received_at: str  # ISO 8601


class FrogAgentRunCommand(BaseModel):
    type: Literal["frog.agent.run"] = "frog.agent.run"
    goal: str


class FrogAgentRunResult(BaseModel):
    run_id: str


class FrogEventSubscribeCommand(BaseModel):
    type: Literal["frog.event.subscribe"] = "frog.event.subscribe"
    topics: list[str]          # fnmatch 模式，如 ["frog.step.*", "frog.tool.*"]
    scope: str = "global"      # "global" | "run:<run_id>"
    replay_from_run: str | None = None  # 设置则先从 events.jsonl 回放历史再接实时流


class FrogEventSubscribeResult(BaseModel):
    subscription_id: str
    replayed_count: int = 0


class FrogSessionCreateCommand(BaseModel):
    type: Literal["frog.session.create"] = "frog.session.create"
    mode: SessionMode = "chat"
    title: str = ""


class FrogSessionCreateResult(BaseModel):
    session_id: str
    status: SessionStatus


class FrogSessionSendMessageCommand(BaseModel):
    type: Literal["frog.session.send_message"] = "frog.session.send_message"
    session_id: str
    content: str


class FrogSessionSendMessageResult(BaseModel):
    run_id: str


class FrogSessionGetHistoryCommand(BaseModel):
    type: Literal["frog.session.get_history"] = "frog.session.get_history"
    session_id: str


class FrogSessionGetHistoryResult(BaseModel):
    messages: list[dict[str, Any]]


class FrogSessionCloseCommand(BaseModel):
    type: Literal["frog.session.close"] = "frog.session.close"
    session_id: str


class FrogSessionCloseResult(BaseModel):
    status: SessionStatus


class FrogPermissionRespondCommand(BaseModel):
    type: Literal["frog.permission.respond"] = "frog.permission.respond"
    tool_use_id: str
    # "allow_once" | "always_allow" | "deny_once" | "always_deny"
    decision: str


class FrogPermissionRespondResult(BaseModel):
    ok: bool = True


class FrogSessionCompactCommand(BaseModel):
    type: Literal["frog.session.compact"] = "frog.session.compact"
    session_id: str
    focus: str = ""


class FrogSessionCompactResult(BaseModel):
    summary_tokens: int
    saved_tokens: int


# 根据 type 字段决定命令类型的判别联合
Command = Annotated[
    FrogPingCommand
    | FrogAgentRunCommand
    | FrogEventSubscribeCommand
    | FrogSessionCreateCommand
    | FrogSessionSendMessageCommand
    | FrogSessionGetHistoryCommand
    | FrogSessionCloseCommand
    | FrogPermissionRespondCommand
    | FrogSessionCompactCommand,
    Discriminator("type"),
]

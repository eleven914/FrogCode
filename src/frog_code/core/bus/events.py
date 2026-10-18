from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator


class FrogCoreStartedEvent(BaseModel):
    type: Literal["frog.core.started"] = "frog.core.started"
    listen_addr: str  # e.g. "127.0.0.1:7437"
    version: str


class FrogRunStartedEvent(BaseModel):
    type: Literal["frog.run.started"] = "frog.run.started"
    run_id: str
    goal: str
    ts: str  # ISO 8601


class FrogRunFinishedEvent(BaseModel):
    type: Literal["frog.run.finished"] = "frog.run.finished"
    run_id: str
    status: str  # "success" | "failed"
    reason: str | None = None  # "exceeded_max_steps" | "cancelled" | "llm_error" | ...
    steps: int
    ts: str


class FrogStepStartedEvent(BaseModel):
    type: Literal["frog.step.started"] = "frog.step.started"
    run_id: str
    step: int
    ts: str


class FrogStepFinishedEvent(BaseModel):
    type: Literal["frog.step.finished"] = "frog.step.finished"
    run_id: str
    step: int
    ts: str


class FrogToolCallStartedEvent(BaseModel):
    type: Literal["frog.tool.call_started"] = "frog.tool.call_started"
    run_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    ts: str


class FrogToolCallFinishedEvent(BaseModel):
    type: Literal["frog.tool.call_finished"] = "frog.tool.call_finished"
    run_id: str
    tool_use_id: str
    tool_name: str
    elapsed_ms: int
    output: str = ""  # tool result content, for TUI display
    ts: str


class FrogToolCallFailedEvent(BaseModel):
    type: Literal["frog.tool.call_failed"] = "frog.tool.call_failed"
    run_id: str
    tool_use_id: str
    tool_name: str
    # "runtime_error" | "timeout" | "schema_error" | "permission_denied" | "rate_limited"
    error_class: str
    error_message: str
    elapsed_ms: int
    attempt: int = 1  # 1=first attempt, 2=first retry, 3=second retry
    ts: str


class FrogLlmTokenEvent(BaseModel):
    type: Literal["frog.llm.token"] = "frog.llm.token"
    run_id: str
    token: str
    ts: str


class FrogLlmUsageEvent(BaseModel):
    type: Literal["frog.llm.usage"] = "frog.llm.usage"
    run_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    context_pct: float = 0.0
    ts: str


class FrogLlmModelSelectedEvent(BaseModel):
    type: Literal["frog.llm.model_selected"] = "frog.llm.model_selected"
    run_id: str
    model: str
    strategy: str  # "static" | "rule_based" | "cost_budget"
    ts: str


class FrogLogLineEvent(BaseModel):
    type: Literal["frog.log.line"] = "frog.log.line"
    run_id: str
    level: str  # "DEBUG" | "INFO" | "WARNING" | "ERROR"
    source: str
    message: str
    ts: str


class FrogSessionCreatedEvent(BaseModel):
    type: Literal["frog.session.created"] = "frog.session.created"
    session_id: str
    mode: str
    ts: str


class FrogSessionMessageReceivedEvent(BaseModel):
    type: Literal["frog.session.message_received"] = "frog.session.message_received"
    session_id: str
    content: str
    ts: str


class FrogSessionWaitingForInputEvent(BaseModel):
    type: Literal["frog.session.waiting_for_input"] = "frog.session.waiting_for_input"
    session_id: str
    last_run_id: str
    ts: str


class FrogSessionResumedEvent(BaseModel):
    type: Literal["frog.session.resumed"] = "frog.session.resumed"
    session_id: str
    ts: str


class FrogSessionClosedEvent(BaseModel):
    type: Literal["frog.session.closed"] = "frog.session.closed"
    session_id: str
    ts: str


class FrogContextCompactedEvent(BaseModel):
    type: Literal["frog.context.compacted"] = "frog.context.compacted"
    session_id: str
    run_id: str
    original_tokens: int
    summary_tokens: int
    ts: str


class FrogPermissionRequestedEvent(BaseModel):
    type: Literal["frog.permission.requested"] = "frog.permission.requested"
    run_id: str
    tool_use_id: str
    tool_name: str
    params: dict[str, Any]
    frog_param_preview: str
    session_id: str
    ts: str


class FrogPermissionGrantedEvent(BaseModel):
    type: Literal["frog.permission.granted"] = "frog.permission.granted"
    run_id: str
    tool_use_id: str
    # "allow_once" | "always_allow" | "auto_allow"
    decision: str
    ts: str


class FrogPermissionDeniedEvent(BaseModel):
    type: Literal["frog.permission.denied"] = "frog.permission.denied"
    run_id: str
    tool_use_id: str
    # "deny_once" | "always_deny" | "auto_deny"
    decision: str
    ts: str


class FrogSubagentStartedEvent(BaseModel):
    type: Literal["frog.subagent.started"] = "frog.subagent.started"
    run_id: str          # 子 agent run_id
    parent_run_id: str
    description: str
    ts: str


class FrogSubagentFinishedEvent(BaseModel):
    type: Literal["frog.subagent.finished"] = "frog.subagent.finished"
    run_id: str
    parent_run_id: str
    status: str          # "success" | "failed"
    ts: str


class FrogSkillInvokedEvent(BaseModel):
    type: Literal["frog.skill.invoked"] = "frog.skill.invoked"
    skill_name: str
    arguments: str
    run_id: str
    ts: str


# 根据 type 字段决定事件类型的判别联合
Event = Annotated[
    FrogCoreStartedEvent
    | FrogRunStartedEvent
    | FrogRunFinishedEvent
    | FrogStepStartedEvent
    | FrogStepFinishedEvent
    | FrogToolCallStartedEvent
    | FrogToolCallFinishedEvent
    | FrogToolCallFailedEvent
    | FrogLlmTokenEvent
    | FrogLlmUsageEvent
    | FrogLlmModelSelectedEvent
    | FrogLogLineEvent
    | FrogSessionCreatedEvent
    | FrogSessionMessageReceivedEvent
    | FrogSessionWaitingForInputEvent
    | FrogSessionResumedEvent
    | FrogSessionClosedEvent
    | FrogContextCompactedEvent
    | FrogPermissionRequestedEvent
    | FrogPermissionGrantedEvent
    | FrogPermissionDeniedEvent
    | FrogSubagentStartedEvent
    | FrogSubagentFinishedEvent
    | FrogSkillInvokedEvent,
    Discriminator("type"),
]

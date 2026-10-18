from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FrogUsageStats:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    context_pct: float = 0.0


@dataclass
class FrogToolCallBlock:
    id: str
    name: str
    input: dict[str, object]


@dataclass
class FrogLlmResponse:
    stop_reason: str  # "end_turn" | "tool_use"
    tool_calls: list[FrogToolCallBlock] = field(default_factory=list)
    text: str = ""
    usage: FrogUsageStats | None = None
    # thinking blocks from extended thinking — must be preserved verbatim in conversation history
    thinking_blocks: list[dict[str, object]] = field(default_factory=list)

from __future__ import annotations

from typing import Protocol

from frog_code.core.events.bus import FrogEventBus
from frog_code.core.llm.types import FrogLlmResponse


class FrogLLMProvider(Protocol):
    # 流式调用 LLM 并发布进度事件，返回完整响应
    async def frog_chat(
        self,
        messages: list[dict[str, object]],
        frog_tool_schemas: list[dict[str, object]],
        bus: FrogEventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> FrogLlmResponse: ...

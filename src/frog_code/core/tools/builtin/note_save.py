from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from frog_code.core.session.store import FrogSessionStore
from frog_code.core.tools.base import FrogBaseTool, FrogToolResult


class FrogNoteSaveParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: str


class FrogNoteSaveTool(FrogBaseTool):
    params_model = FrogNoteSaveParams
    name = "frog_note_save"
    description = (
        "Save a concise fact or decision to this session's notes. "
        "These notes are visible in future turns of the same session."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The durable fact or decision to remember.",
            },
        },
        "required": ["content"],
    }

    # 绑定当前 session 与 run，使工具调用能写入对应 notes.md
    def __init__(self, store: FrogSessionStore, session_id: str, run_id: str) -> None:
        self._store = store
        self._session_id = session_id
        self._run_id = run_id

    # 将非空 content 追加到 session notes.md
    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        content = FrogNoteSaveParams.model_validate(params).content.strip()
        if not content:
            return FrogToolResult(
                content="empty content",
                is_error=True,
                error_type="runtime_error",
            )
        self._store.frog_append_note(self._session_id, content, self._run_id)
        return FrogToolResult(content="saved")

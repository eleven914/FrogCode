from __future__ import annotations

import json

from frog_code.core.task.manager import FrogTaskManager
from frog_code.core.task.model import TaskStatus
from frog_code.core.tools.base import FrogBaseTool, FrogToolResult


class FrogTaskUpdateTool(FrogBaseTool):
    name = "frog_task_update"
    description = (
        "Update a task's status or dependency list. "
        "Set status to 'in_progress' when starting work on a task, "
        "'completed' when finished (automatically clears it from other tasks' blocked_by). "
        "Returns the updated task as JSON."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to update.",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "New status for the task.",
            },
            "add_blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "FrogTask IDs to add to blocked_by.",
            },
            "remove_blocked_by": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "FrogTask IDs to remove from blocked_by.",
            },
        },
        "required": ["task_id"],
    }

    # 持有 FrogTaskManager 实例，供 frog_invoke 调用
    def __init__(self, task_manager: FrogTaskManager) -> None:
        self._manager = task_manager

    # 更新任务并返回 JSON 字符串
    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        task_id = int(str(params["task_id"]))
        status: TaskStatus | None = params.get("status")  # type: ignore[assignment]
        raw_add: list[object] = list(params.get("add_blocked_by") or [])  # type: ignore[call-overload]
        raw_rem: list[object] = list(params.get("remove_blocked_by") or [])  # type: ignore[call-overload]
        add_blocked = [int(str(x)) for x in raw_add]
        remove_blocked = [int(str(x)) for x in raw_rem]
        try:
            task = self._manager.update(
                task_id,
                status=status,
                add_blocked_by=add_blocked or None,
                remove_blocked_by=remove_blocked or None,
            )
            return FrogToolResult(content=json.dumps(task.frog_to_dict(), ensure_ascii=False))
        except ValueError as exc:
            return FrogToolResult(content=str(exc), is_error=True, error_type="runtime_error")

from __future__ import annotations

import json

from frog_code.core.task.manager import FrogTaskManager
from frog_code.core.tools.base import FrogBaseTool, FrogToolResult


class FrogTaskGetTool(FrogBaseTool):
    name = "frog_task_get"
    description = "Get full details of a task by its integer ID. Returns the task as JSON."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to retrieve.",
            },
        },
        "required": ["task_id"],
    }

    # 持有 FrogTaskManager 实例，供 frog_invoke 调用
    def __init__(self, task_manager: FrogTaskManager) -> None:
        self._manager = task_manager

    # 获取任务详情并返回 JSON 字符串
    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        task_id = int(str(params["task_id"]))
        try:
            task = self._manager.get(task_id)
            return FrogToolResult(content=json.dumps(task.frog_to_dict(), ensure_ascii=False))
        except ValueError as exc:
            return FrogToolResult(content=str(exc), is_error=True, error_type="runtime_error")

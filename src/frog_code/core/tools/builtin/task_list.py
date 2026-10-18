from __future__ import annotations

from frog_code.core.task.manager import FrogTaskManager
from frog_code.core.tools.base import FrogBaseTool, FrogToolResult


class FrogTaskListTool(FrogBaseTool):
    name = "frog_task_list"
    description = (
        "List all tasks with their current status and blocking dependencies. "
        "Use this to check what work remains and what can be started next."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    # 持有 FrogTaskManager 实例，供 frog_invoke 调用
    def __init__(self, task_manager: FrogTaskManager) -> None:
        self._manager = task_manager

    # 返回格式化的任务列表摘要
    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        return FrogToolResult(content=self._manager.frog_format_list())

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from frog_code.core.task.model import FrogTask, TaskStatus


def _frog_now() -> str:
    return datetime.now(UTC).isoformat()


class FrogTaskManager:
    # 初始化：确保目录存在，扫描现有文件确定下一个 ID
    def __init__(self, tasks_dir: Path) -> None:
        self._dir = tasks_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._frog_max_id() + 1

    # 扫描目录中 task_*.json 文件，返回最大 ID（无文件则返回 0）
    def _frog_max_id(self) -> int:
        ids = [
            int(f.stem.split("_")[1])
            for f in self._dir.glob("task_*.json")
            if f.stem.split("_")[1].isdigit()
        ]
        return max(ids) if ids else 0

    # 读取指定 ID 的任务文件
    def _frog_load(self, task_id: int) -> FrogTask:
        path = self._dir / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"task {task_id} not found")
        return FrogTask.frog_from_dict(json.loads(path.read_text()))

    # 将任务写入对应 JSON 文件
    def _frog_save(self, task: FrogTask) -> None:
        path = self._dir / f"task_{task.id}.json"
        path.write_text(json.dumps(task.frog_to_dict(), indent=2, ensure_ascii=False))

    # 创建新任务，写入 JSON 文件，返回 FrogTask
    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
    ) -> FrogTask:
        for dep_id in (blocked_by or []):
            if not (self._dir / f"task_{dep_id}.json").exists():
                raise ValueError(f"blocked_by task {dep_id} not found")
        now = _frog_now()
        task = FrogTask(
            id=self._next_id,
            subject=subject,
            description=description,
            status="pending",
            blocked_by=list(blocked_by or []),
            created_at=now,
            updated_at=now,
        )
        self._frog_save(task)
        self._next_id += 1
        return task

    # 读取指定 ID 的任务
    def get(self, task_id: int) -> FrogTask:
        return self._frog_load(task_id)

    # 更新任务状态或依赖列表；status="completed" 时自动清理其他任务的 blocked_by
    def update(
        self,
        task_id: int,
        *,
        status: TaskStatus | None = None,
        add_blocked_by: list[int] | None = None,
        remove_blocked_by: list[int] | None = None,
    ) -> FrogTask:
        task = self._frog_load(task_id)
        if status is not None:
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"invalid status: {status!r}")
            task.status = status
            if status == "completed":
                self._frog_clear_dependency(task_id)
        if add_blocked_by:
            task.blocked_by = list(set(task.blocked_by + add_blocked_by))
        if remove_blocked_by:
            task.blocked_by = [x for x in task.blocked_by if x not in remove_blocked_by]
        task.updated_at = _frog_now()
        self._frog_save(task)
        return task

    # 返回所有任务，按 ID 升序排列
    def list_all(self) -> list[FrogTask]:
        tasks = []
        for f in sorted(self._dir.glob("task_*.json"), key=lambda p: int(p.stem.split("_")[1])):
            try:
                tasks.append(FrogTask.frog_from_dict(json.loads(f.read_text())))
            except (ValueError, KeyError):
                pass
        return tasks

    # 将 completed_id 从所有其他任务的 blocked_by 列表中移除
    def _frog_clear_dependency(self, completed_id: int) -> None:
        for f in self._dir.glob("task_*.json"):
            try:
                data = json.loads(f.read_text())
            except (ValueError, json.JSONDecodeError):
                continue
            blocked = [int(x) for x in data.get("blocked_by", [])]
            if completed_id in blocked:
                data["blocked_by"] = [x for x in blocked if x != completed_id]
                data["updated_at"] = _frog_now()
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # 格式化任务列表摘要，供 task_list 工具返回给 Agent
    def frog_format_list(self) -> str:
        tasks = self.list_all()
        if not tasks:
            return "No tasks."
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines = []
        for t in tasks:
            blocked = f" (blocked by: {t.blocked_by})" if t.blocked_by else ""
            lines.append(f"{marker.get(t.status, '[?]')} #{t.id}: {t.subject}{blocked}")
        return "\n".join(lines)

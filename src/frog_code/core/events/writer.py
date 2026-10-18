from __future__ import annotations

import logging
from pathlib import Path
from typing import IO

from pydantic import BaseModel

from frog_code.core.events.bus import FrogEventBus

logger = logging.getLogger(__name__)


class FrogEventWriter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    # 打开事件文件（追加模式），供 async with 使用
    async def __aenter__(self) -> FrogEventWriter:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")
        return self

    # 关闭事件文件
    async def __aexit__(self, *args: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    # 将事件序列化为 JSON 行并写入文件，写入失败时记录日志但不抛出异常
    async def frog_handle(self, event: BaseModel) -> None:
        if self._file is None:
            return
        try:
            self._file.write(event.model_dump_json() + "\n")
            self._file.flush()
        except (OSError, ValueError) as e:
            logger.error("FrogEventWriter: failed to write event: %s", e)

    # 将 frog_handle 注册为 bus 的订阅者
    def frog_subscribe(self, bus: FrogEventBus) -> None:
        bus.frog_subscribe(self.frog_handle)

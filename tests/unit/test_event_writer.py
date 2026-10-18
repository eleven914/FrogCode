from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from frog_code.core.bus.events import FrogRunFinishedEvent, FrogRunStartedEvent
from frog_code.core.events.bus import FrogEventBus
from frog_code.core.events.writer import FrogEventWriter


# 功能：验证 frog_handle 后事件被正确序列化为单行 JSONL 写入磁盘
# 设计：使用真实文件（tmp_path）而非 mock，因为 FrogEventWriter 的核心职责是磁盘写入，只有实际读取文件内容才能证明写入正确
async def test_event_writer_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    event = FrogRunStartedEvent(run_id="run-1", goal="test goal", ts="2026-05-11T00:00:00Z")

    async with FrogEventWriter(path) as writer:
        await writer.frog_handle(event)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["type"] == "frog.run.started"
    assert data["run_id"] == "run-1"
    assert data["goal"] == "test goal"


# 功能：验证目标路径的父目录不存在时 FrogEventWriter 自动创建多级目录
# 设计：传入多级不存在的路径，只断言文件最终存在，避免过度测试实现细节（如 mkdir 调用次数）
async def test_event_writer_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "abc123" / "events.jsonl"
    event = FrogRunStartedEvent(run_id="abc123", goal="test", ts="2026-05-11T00:00:00Z")

    async with FrogEventWriter(path) as writer:
        await writer.frog_handle(event)

    assert path.exists()


# 功能：验证多次 frog_handle 是追加写入而非覆盖
# 设计：写两条不同类型事件，检查行数和各行 type 字段，确认 JSONL 追加语义
async def test_event_writer_appends_multiple_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"

    async with FrogEventWriter(path) as writer:
        await writer.frog_handle(FrogRunStartedEvent(run_id="r1", goal="g1", ts="2026-05-11T00:00:00Z"))
        finished = FrogRunFinishedEvent(
            run_id="r1", status="success", steps=2, ts="2026-05-11T00:00:01Z"
        )
        await writer.frog_handle(finished)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "frog.run.started"
    assert json.loads(lines[1])["type"] == "frog.run.finished"


# 功能：验证 frog_subscribe 把 writer 接入 FrogEventBus 后，bus.frog_publish 能触发文件写入
# 设计：通过 bus.frog_publish 触发写入（而非直接调 writer.frog_handle），测试集成路径，确认订阅接线正确
async def test_event_writer_subscribe_via_bus(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    bus = FrogEventBus()
    event = FrogRunStartedEvent(run_id="r1", goal="g", ts="2026-05-11T00:00:00Z")

    async with FrogEventWriter(path) as writer:
        writer.frog_subscribe(bus)
        await bus.frog_publish(event)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == "r1"


# 功能：验证文件未通过 async with 打开时 frog_handle 静默返回、不抛异常
# 设计：直接实例化 writer（跳过 async with），调用 frog_handle 后不断言文件存在，以"不引发异常"为唯一判据；对应 FrogEventWriter 的防御性设计
async def test_event_writer_handle_when_not_open_is_noop(tmp_path: Path) -> None:
    writer = FrogEventWriter(tmp_path / "events.jsonl")
    event = FrogRunStartedEvent(run_id="r1", goal="g", ts="2026-05-11T00:00:00Z")
    await writer.frog_handle(event)  # _file is None, should not raise


# 功能：验证磁盘写入失败时只记录 ERROR 日志、不向上传播异常
# 设计：手动关闭已打开的文件句柄触发 OSError，用 caplog 断言 ERROR 级别日志；FrogEventWriter 的契约是"不因写文件失败终止 agent"
async def test_event_writer_oserror_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "events.jsonl"
    event = FrogRunStartedEvent(run_id="r1", goal="g", ts="2026-05-11T00:00:00Z")

    with caplog.at_level(logging.ERROR, logger="frog_code.core.events.writer"):
        async with FrogEventWriter(path) as writer:
            assert writer._file is not None
            writer._file.close()
            # _file 仍非 None 但已关闭，write 会抛 OSError
            await writer.frog_handle(event)

    assert any("failed to write" in r.message for r in caplog.records)

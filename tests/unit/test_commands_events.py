from __future__ import annotations

import pytest
from pydantic import ValidationError

from frog_code.core.bus.commands import FrogPingCommand, FrogPongResult
from frog_code.core.bus.events import FrogCoreStartedEvent


# 功能：验证 FrogPingCommand 序列化后再反序列化，client 和 type 字段完整保留
# 设计：JSON 往返测试确认 wire 协议的序列化正确性，type 字段是 discriminated union 的判别键
def test_ping_command_roundtrip() -> None:
    cmd = FrogPingCommand(client="cli/0.0.1")
    cmd2 = FrogPingCommand.model_validate_json(cmd.model_dump_json())
    assert cmd2.client == "cli/0.0.1"
    assert cmd2.type == "frog.core.ping"


# 功能：验证 FrogPingCommand 的 type 字段默认值为 "frog.core.ping"
# 设计：Literal 默认值测试，type 是 Command union 的判别键，必须与 union 定义完全一致，否则反序列化时会路由到错误类型
def test_ping_command_default_type() -> None:
    cmd = FrogPingCommand(client="x")
    assert cmd.type == "frog.core.ping"


# 功能：验证缺少必填 client 字段时 pydantic 校验失败
# 设计：传入空 dict 触发校验，确认 client 是必填字段，防止 daemon 收到不完整的 ping 命令进入 handler
def test_ping_command_missing_client_raises() -> None:
    with pytest.raises(ValidationError):
        FrogPingCommand.model_validate({})


# 功能：验证 FrogPongResult 序列化往返后所有字段完整保留
# 设计：与 FrogPingCommand 对称，测试命令-响应对的两端序列化，确认 int 和 str 字段类型在往返中不变
def test_pong_result_roundtrip() -> None:
    pong = FrogPongResult(server_version="0.0.1", uptime_ms=42, received_at="2026-05-11T00:00:00Z")
    pong2 = FrogPongResult.model_validate(pong.model_dump())
    assert pong2.server_version == "0.0.1"
    assert pong2.uptime_ms == 42


# 功能：验证 FrogCoreStartedEvent 序列化往返后 listen_addr 和 type 字段正确保留
# 设计：FrogCoreStartedEvent 是 daemon 启动通知，往返测试确认 type 的 Literal 约束在反序列化后保持（不被字段名覆盖）
def test_core_started_event_roundtrip() -> None:
    evt = FrogCoreStartedEvent(listen_addr="127.0.0.1:7437", version="0.0.1")
    evt2 = FrogCoreStartedEvent.model_validate_json(evt.model_dump_json())
    assert evt2.listen_addr == "127.0.0.1:7437"
    assert evt2.type == "frog.core.started"

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from frog_code.core.mcp.client import FrogMcpClient, FrogMcpServerUnavailableError, FrogMcpToolDef
from frog_code.core.mcp.tool import FrogMcpTool


def _make_tool(
    tool_name: str = "read_file",
    server_name: str = "filesystem",
) -> tuple[FrogMcpTool, AsyncMock]:
    client = AsyncMock(spec=FrogMcpClient)
    tool_def = FrogMcpToolDef(
        name=tool_name,
        description=f"Read a file via {server_name}",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    tool = FrogMcpTool(client, server_name, tool_def)
    return tool, client


# 功能：FrogMcpTool.frog_invoke 应调用 client.frog_call_tool 并将返回值封装为 FrogToolResult
# 设计：mock client.frog_call_tool 返回固定字符串，验证 FrogToolResult.content 一致
@pytest.mark.asyncio
async def test_invoke_calls_mcp_client() -> None:
    tool, client = _make_tool()
    client.frog_call_tool = AsyncMock(return_value="file content here")
    result = await tool.frog_invoke({"path": "/tmp/test.txt"})
    assert not result.is_error
    assert result.content == "file content here"
    client.frog_call_tool.assert_called_once_with("read_file", {"path": "/tmp/test.txt"})


# 功能：工具名应以 {server_name}__ 为前缀防止命名冲突
# 设计：验证 FrogMcpTool.name 格式为 "filesystem__read_file"
def test_tool_name_prefixed() -> None:
    tool, _ = _make_tool("read_file", "filesystem")
    assert tool.name == "filesystem__read_file"


# 功能：client 抛 FrogMcpServerUnavailableError 时应返回 is_error=True 且不重新抛出
# 设计：mock client.frog_call_tool 抛该异常，断言 FrogToolResult.is_error=True 且消息含 server 名称
@pytest.mark.asyncio
async def test_unavailable_returns_error() -> None:
    tool, client = _make_tool()
    client.frog_call_tool = AsyncMock(side_effect=FrogMcpServerUnavailableError("process died"))
    result = await tool.frog_invoke({"path": "/tmp/x.txt"})
    assert result.is_error
    assert result.error_type == "runtime_error"
    assert "filesystem" in result.content


# 功能：client 抛其他异常时应返回 runtime_error 类型的 FrogToolResult
# 设计：mock client.frog_call_tool 抛 RuntimeError，断言 FrogToolResult 被正确包装
@pytest.mark.asyncio
async def test_runtime_error_caught() -> None:
    tool, client = _make_tool()
    client.frog_call_tool = AsyncMock(side_effect=RuntimeError("unexpected failure"))
    result = await tool.frog_invoke({"path": "/tmp/y.txt"})
    assert result.is_error
    assert result.error_type == "runtime_error"
    assert "unexpected failure" in result.content


# 功能：input_schema 应直接使用 MCP tool_def 中的 schema，而非 pydantic model
# 设计：验证 params_model 为 None，input_schema 与 tool_def.input_schema 一致
def test_input_schema_from_tool_def() -> None:
    tool, _ = _make_tool()
    assert FrogMcpTool.params_model is None
    assert "path" in tool.input_schema.get("properties", {})

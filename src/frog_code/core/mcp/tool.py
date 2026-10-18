from __future__ import annotations

from typing import Any

from frog_code.core.mcp.client import FrogMcpClient, FrogMcpServerUnavailableError, FrogMcpToolDef, FrogMcpToolError
from frog_code.core.tools.base import FrogBaseTool, FrogToolResult


# 将 MCP 工具包装为 FrogBaseTool，使 FrogToolRegistry 可透明调用
class FrogMcpTool(FrogBaseTool):
    params_model = None  # input_schema 来自 MCP tool_def，不使用 pydantic model

    # 初始化 MCP 工具包装器，工具名以 server_name__ 为前缀防止命名冲突
    def __init__(self, client: FrogMcpClient, server_name: str, tool_def: FrogMcpToolDef) -> None:
        self._client = client
        self._server_name = server_name
        self._tool_def = tool_def
        self.name = f"{server_name}__{tool_def.name}"
        self.description = tool_def.description or f"MCP tool from {server_name}"
        self.input_schema: dict[str, Any] = (
            tool_def.input_schema or {"type": "object", "properties": {}}
        )

    # 调用 MCP server 上的工具，连接不可用或工具执行失败时返回 is_error=True
    async def frog_invoke(self, params: dict[str, object]) -> FrogToolResult:
        try:
            content = await self._client.frog_call_tool(self._tool_def.name, dict(params))
            return FrogToolResult(content=content)
        except FrogMcpServerUnavailableError as exc:
            return FrogToolResult(
                content=f"mcp server '{self._server_name}' unavailable: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except FrogMcpToolError as exc:
            return FrogToolResult(
                content=f"mcp tool '{self.name}' error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except Exception as exc:
            return FrogToolResult(
                content=f"mcp tool '{self.name}' unexpected error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

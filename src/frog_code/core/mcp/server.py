from __future__ import annotations

import logging

from frog_code.core.config import FrogMcpServerConfig
from frog_code.core.mcp.client import FrogMcpClient
from frog_code.core.mcp.tool import FrogMcpTool
from frog_code.core.tools.registry import FrogToolRegistry

log = logging.getLogger(__name__)


# 管理所有 MCP server 连接的生命周期：启动、工具发现、注册、关闭
class FrogMcpServerManager:
    def __init__(self) -> None:
        self._clients: dict[str, FrogMcpClient] = {}
        self._tools: list[FrogMcpTool] = []

    # 依次连接每个 MCP server，发现工具后缓存供后续 registry 使用；失败时记录日志并跳过
    async def frog_start_all(self, servers: list[FrogMcpServerConfig]) -> None:
        for cfg in servers:
            try:
                client = await self._frog_connect(cfg)
                tool_defs = await client.frog_list_tools()
                for tool_def in tool_defs:
                    self._tools.append(FrogMcpTool(client, cfg.name, tool_def))
                self._clients[cfg.name] = client
                log.info(
                    "mcp: server '%s' connected, %d tool(s) discovered",
                    cfg.name, len(tool_defs),
                )
            except Exception:
                log.exception("mcp: server '%s' failed to start, skipping", cfg.name)

    # 将所有已发现的 MCP 工具注册到指定 registry
    def frog_register_tools(self, registry: FrogToolRegistry) -> None:
        for tool in self._tools:
            registry.register(tool)

    # 返回已发现的 MCP 工具列表（用于 runner 每次 run 时注入新 registry）
    def get_tools(self) -> list[FrogMcpTool]:
        return list(self._tools)

    # 关闭所有 MCP 连接并终止 stdio 子进程
    async def frog_stop_all(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                await client.close()
                log.info("mcp: server '%s' closed", name)
            except Exception:
                log.warning("mcp: error closing server '%s'", name)
        self._clients.clear()

    # 根据 transport 类型建立连接
    async def _frog_connect(self, cfg: FrogMcpServerConfig) -> FrogMcpClient:
        client = FrogMcpClient()
        if cfg.transport == "stdio":
            if not cfg.command:
                raise ValueError(f"mcp server '{cfg.name}': stdio transport requires 'command'")
            await client.frog_connect_stdio(cfg.command, cfg.args, cfg.env or None)
        elif cfg.transport == "tcp":
            await client.frog_connect_tcp(cfg.host, cfg.port)
        else:
            raise ValueError(f"mcp server '{cfg.name}': unknown transport '{cfg.transport}'")
        return client

from __future__ import annotations

import asyncio
import json
import sys
import time

import frog_code
from frog_code.core.bus.commands import FrogPongResult
from frog_code.core.bus.envelope import FrogJsonRpcError, FrogJsonRpcSuccess
from frog_code.core.config import FrogConfig


# 同步入口：运行 ping 协程，连接失败时打印错误并退出
def frog_cmd_ping(config: FrogConfig) -> None:
    try:
        asyncio.run(_frog_ping(config))
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        sys.exit(1)


# 向 core 守护进程发送 ping 请求，打印 pong 响应及延迟
async def _frog_ping(config: FrogConfig) -> None:
    t0 = time.monotonic()
    reader, writer = await asyncio.open_connection(config.host, config.port)

    req = {
        "jsonrpc": "2.0",
        "id": "cli-1",
        "method": "frog.core.ping",
        "params": {"client": f"cli/{frog_code.__version__}"},
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()

    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    latency_ms = int((time.monotonic() - t0) * 1000)

    writer.close()
    await writer.wait_closed()

    raw = json.loads(line)
    if "error" in raw:
        err = FrogJsonRpcError.model_validate(raw)
        print(f"error: {err.error.code} {err.error.message}", file=sys.stderr)
        sys.exit(1)

    resp = FrogJsonRpcSuccess.model_validate(raw)
    result = FrogPongResult.model_validate(resp.result)
    print(f"pong server={result.server_version} uptime={result.uptime_ms}ms latency={latency_ms}ms")

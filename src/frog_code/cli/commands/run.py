from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from frog_code.core.config import FrogConfig
from frog_code.core.transport.socket_client import FrogIpcError, FrogSocketClient


class FrogStdoutPrinter:
    # 接收 dict 格式的事件并将运行进度格式化打印到终端
    def __init__(self) -> None:
        self._inline = False  # True while LLM tokens are mid-line
        self._run_start: float = 0.0

    # 若当前行有未换行的 token，补一个换行符
    def _frog_ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 根据事件 type 字段分发并格式化打印到 stdout/stderr
    async def frog_handle(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")

        if t == "frog.run.started":
            self._run_start = time.monotonic()
            print(f"[run] {event.get('run_id', '')}")

        elif t == "frog.step.started":
            self._frog_ensure_newline()
            print(f"[step {event.get('step')}] planning...")

        elif t == "frog.llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True

        elif t == "frog.tool.call_started":
            self._frog_ensure_newline()
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            print(f"[tool] {event.get('tool_name', '')} {params_str}")

        elif t == "frog.tool.call_finished":
            print(f"[tool] {event.get('tool_name', '')} ✓  {event.get('elapsed_ms')}ms")

        elif t == "frog.tool.call_failed":
            print(
                f"[tool] {event.get('tool_name', '')} ✗  {event.get('error_message', '')}",
                file=sys.stderr,
            )

        elif t == "frog.step.finished":
            self._frog_ensure_newline()
            print(f"[step {event.get('step')}] done")

        elif t == "frog.run.finished":
            self._frog_ensure_newline()
            frog_elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.get('status', '')}  {event.get('steps')} steps  {frog_elapsed:.1f}s")


# 异步核心：连接 daemon，订阅事件，触发 run，等待 run.finished
async def _frog_run_async(goal: str, config: FrogConfig) -> int:
    client = FrogSocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    printer = FrogStdoutPrinter()
    finished = asyncio.Event()
    exit_code = 0

    async def on_event(event: dict[str, Any]) -> None:
        nonlocal exit_code
        await printer.frog_handle(event)
        if event.get("type") == "frog.run.finished":
            if event.get("status") != "success":
                exit_code = 1
            finished.set()

    client.on_event(on_event)
    loop_task = asyncio.create_task(client.frog_run_event_loop())

    try:
        await client.frog_send_command(
            "frog.event.subscribe",
            {
                "topics": ["frog.run.*", "frog.step.*", "frog.tool.*", "frog.llm.token", "frog.llm.usage"],
                "scope": "global",
            },
        )
        await client.frog_send_command("frog.agent.run", {"goal": goal})
    except FrogIpcError as e:
        print(f"error: {e}", file=sys.stderr)
        loop_task.cancel()
        await client.close()
        return 1

    await finished.wait()

    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    await client.close()
    return exit_code


# 执行 frog frog-run --goal "..." 命令
def frog_cmd_run(goal: str, config: FrogConfig) -> None:
    try:
        exit_code = asyncio.run(_frog_run_async(goal, config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)

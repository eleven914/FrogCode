from __future__ import annotations

import asyncio
import sys
from typing import Any

from frog_code.core.config import FrogConfig
from frog_code.core.transport.socket_client import FrogIpcError, FrogSocketClient

_DECISION_MAP: dict[str, str] = {
    "y": "allow_once",
    "a": "always_allow",
    "n": "deny_once",
    "d": "always_deny",
}


class FrogChatPrinter:
    # 初始化 frog_chat 模式的流式输出状态和待审批权限请求
    def __init__(self) -> None:
        self._inline = False
        self.pending_permission_id: str | None = None

    # 若当前 LLM token 尚未换行，则补一个换行
    def _frog_ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 按事件类型打印 frog_chat 输出、等待提示和权限审批请求
    async def frog_handle(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")
        if t == "frog.llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True
        elif t == "frog.tool.call_started":
            self._frog_ensure_newline()
            print(f"[tool] {event.get('tool_name', '')}")
        elif t == "frog.permission.requested":
            self._frog_ensure_newline()
            tool_name = str(event.get("tool_name", ""))
            frog_param_preview = str(event.get("frog_param_preview", ""))
            tool_use_id = str(event.get("tool_use_id", ""))
            print(f"[permission] {tool_name}  {frog_param_preview}")
            print("  y=allow once  a=always allow  n=deny once  d=always deny")
            self.pending_permission_id = tool_use_id
        elif t == "frog.session.waiting_for_input":
            self._frog_ensure_newline()
            self.pending_permission_id = None
            print("[waiting for input]")
        elif t == "frog.session.closed":
            self._frog_ensure_newline()
            print("session closed.")


# 在线程池中读取 stdin，避免阻塞 socket event loop
async def _frog_readline(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# 异步核心：创建 frog_chat session，循环读取用户输入并发送到 daemon；权限请求时优先处理审批
async def _frog_chat_async(config: FrogConfig) -> int:
    client = FrogSocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    printer = FrogChatPrinter()
    client.on_event(printer.frog_handle)
    loop_task = asyncio.create_task(client.frog_run_event_loop())

    try:
        await client.frog_send_command(
            "frog.event.subscribe",
            {
                "topics": ["frog.session.*", "frog.run.*", "frog.tool.*", "frog.llm.token", "frog.permission.*"],
                "scope": "global",
            },
        )
        created = await client.frog_send_command("frog.session.create", {"mode": "chat"})
        session_id = str(created["session_id"])
        print(f"[session: {session_id}]")

        while True:
            try:
                line = await _frog_readline("> ")
            except (EOFError, KeyboardInterrupt):
                break
            content = line.strip()
            if not content:
                continue

            # 有待审批的权限请求时，将用户输入解释为决策而非聊天消息
            if printer.pending_permission_id:
                decision = _DECISION_MAP.get(content.lower())
                if decision is None:
                    print("  enter y (allow once), a (always allow), "
                          "n (deny once), d (always deny)")
                    continue
                tool_use_id = printer.pending_permission_id
                printer.pending_permission_id = None
                await client.frog_send_command(
                    "frog.permission.respond",
                    {"tool_use_id": tool_use_id, "decision": decision},
                )
                continue

            await client.frog_send_command(
                "frog.session.send_message",
                {"session_id": session_id, "content": content},
            )

        await client.frog_send_command("frog.session.close", {"session_id": session_id})
    except FrogIpcError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await client.close()
    return 0


# 执行 frog frog-chat 命令
def frog_cmd_chat(config: FrogConfig) -> None:
    try:
        exit_code = asyncio.run(_frog_chat_async(config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)

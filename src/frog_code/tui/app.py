from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from rich.markdown import Markdown
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Static, TextArea

from frog_code.core.config import FrogConfig
from frog_code.core.skills.loader import FrogSkillLoader
from frog_code.core.transport.socket_client import FrogIpcError, FrogSocketClient

log = logging.getLogger(__name__)


def _frog_preview(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s




def _frog_params_str(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False, indent=2)


# 从工具参数中提取最适合摘要展示的关键字段
def _frog_param_summary(tool_name: str, params: dict[str, Any], max_len: int = 72) -> str:
    keys_by_tool = {
        "frog_read_file": ("path",),
        "frog_write_file": ("path",),
        "frog_list_dir": ("path", "max_depth"),
        "frog_bash": ("command",),
        "frog_note_save": ("content",),
    }
    keys = keys_by_tool.get(tool_name, ())
    parts = [f"{key}={params[key]!r}" for key in keys if key in params]
    if not parts:
        parts = [f"{key}={value!r}" for key, value in list(params.items())[:2]]
    return _frog_preview(", ".join(parts), max_len)


class FrogLLMStreamBlock(Static):
    """在同一个 Static widget 中累积 LLM 流式 token。"""

    DEFAULT_CSS = "FrogLLMStreamBlock { padding: 0 2; color: $text; }"

    # 初始化为空文本块
    def __init__(self) -> None:
        super().__init__("")
        self._text = ""
        self._finalized = False

    # 追加一个 token 并刷新显示
    def frog_append_token(self, token: str) -> None:
        if self._finalized:
            return
        self._text += token
        self.update(self._text)

    # 将累积文本渲染为 Markdown，供流式块结束后显示
    def frog_finalize_markdown(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        if self._text.strip():
            self.update(Markdown(self._text, code_theme="monokai"))


class FrogToolCallBlock(Widget):
    """可折叠的工具调用块：折叠时显示摘要，点击后展开完整 params 和 output。"""

    DEFAULT_CSS = """
    FrogToolCallBlock { height: auto; padding: 0 2; color: $text-muted; }
    FrogToolCallBlock > .summary { color: $text-muted; }
    FrogToolCallBlock > .detail { display: none; padding: 0 2 0 4; color: $text-muted; }
    FrogToolCallBlock.expanded > .detail { display: block; }
    """

    # 初始化工具调用信息
    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._params = params
        self._params_full = _frog_params_str(params)
        self._output = ""
        self._elapsed_ms = 0
        self._is_error = False
        self._finished = False

    def compose(self) -> ComposeResult:
        yield Static(self._frog_summary(), classes="summary")
        yield Static("", classes="detail")

    # 生成摘要行文本
    def _frog_summary(self) -> str:
        if self._tool_name == "frog_note_save" and self._finished and not self._is_error:
            return f"  [green]remembered[/green]  [dim]{self._elapsed_ms}ms[/dim]"

        params_pre = _frog_param_summary(self._tool_name, self._params)
        line = f"  [dim]tool[/dim] [bold]{self._tool_name}[/bold]"
        if params_pre:
            line += f"  [dim]{params_pre}[/dim]"
        if self._finished:
            color = "red" if self._is_error else "green"
            status = "failed" if self._is_error else "done"
            hint = "  [dim](click to expand)[/dim]" if self._output else ""
            line += f"  [{color}]{status}[/{color}]  [dim]{self._elapsed_ms}ms[/dim]{hint}"
        return line

    # 工具调用完成时更新结果并刷新摘要（widget 未挂载时跳过 DOM 更新）
    def frog_set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        self._output = output
        self._elapsed_ms = elapsed_ms
        self._is_error = is_error
        self._finished = True
        if self.children:
            self.query_one(".summary", Static).update(self._frog_summary())

    # 点击时切换展开/折叠状态
    def on_click(self) -> None:
        if not self._finished:
            return
        if "expanded" in self.classes:
            self.remove_class("expanded")
        else:
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params[/dim]\n{self._params_full}\n\n"
                f"[dim]output[/dim]\n{self._output}\n\n"
                f"[dim]frog_elapsed:[/dim] {self._elapsed_ms}ms"
            )
            self.add_class("expanded")


class FrogPermissionSelect(Static):
    """内联权限选择控件：挂载在日志流中，键盘焦点无需 ModalScreen。"""

    can_focus = True

    DEFAULT_CSS = """
    FrogPermissionSelect {
        height: auto;
        padding: 1 2;
        margin: 0 1 1 1;
        border: round #ffd166;
        background: #171208;
    }
    """

    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("allow_once",   "Allow once",   "y / 1"),
        ("always_allow", "Always allow", "a / 2"),
        ("deny_once",    "Deny",         "n / 3"),
        ("always_deny",  "Always deny",  "d / 4"),
    )
    _KEY_MAP: dict[str, str] = {
        "y": "allow_once",  "1": "allow_once",
        "a": "always_allow","2": "always_allow",
        "n": "deny_once",   "3": "deny_once",
        "d": "always_deny", "4": "always_deny",
    }

    # 用户作出权限决策时发布，携带工具 ID 和决策字符串
    class Decided(Message):
        # 初始化决策消息，存储控件引用、工具 ID 和决策
        def __init__(self, widget: FrogPermissionSelect, tool_use_id: str, decision: str) -> None:
            self.widget = widget
            self.tool_use_id = tool_use_id
            self.decision = decision
            super().__init__()

    # 初始化控件，存储工具 ID（用于 IPC 回复）
    def __init__(self, tool_use_id: str) -> None:
        super().__init__("")
        self._tool_use_id = tool_use_id
        self._cursor = 0

    def on_mount(self) -> None:
        self.update(self._frog_render_ui())
        self.focus()
        log.debug(
            "FrogPermissionSelect.on_mount  can_focus=%s  focused_after=%r",
            self.can_focus,
            self.app.focused,
        )
        self.app.call_after_refresh(self._frog_log_deferred_focus)

    # 下一帧再次请求焦点；挂载期间 VerticalScroll 可能在首次 focus() 后抢回焦点
    def _frog_log_deferred_focus(self) -> None:
        if not self.has_focus:
            self.focus()
        log.debug(
            "FrogPermissionSelect.deferred_focus  app.focused=%r  has_focus=%s  focusable=%s",
            self.app.focused,
            self.has_focus,
            self.focusable,
        )

    # 焦点到达时记录，用于确认 focus() 是否真正生效
    def on_focus(self, event: events.Focus) -> None:
        log.debug("FrogPermissionSelect.on_focus  has_focus=%s  app.focused=%r", self.has_focus, self.app.focused)

    # 焦点离开时记录，用于追踪是否被其他控件抢走焦点
    def on_blur(self, event: events.Blur) -> None:
        log.debug("FrogPermissionSelect.on_blur  app.focused=%r", self.app.focused)

    # 生成带光标高亮的选项列表文本
    def _frog_render_ui(self) -> str:
        lines: list[str] = []
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ {label}[/bold cyan]  [dim]{key_hint}[/dim]")
            else:
                lines.append(f"    {label}  [dim]{key_hint}[/dim]")
        lines.append("[dim]  ↑↓ navigate   enter confirm[/dim]")
        return "\n".join(lines)

    # 方向键导航；快捷键直接选择；enter 确认光标位置
    def on_key(self, event: events.Key) -> None:
        log.debug("FrogPermissionSelect.on_key  key=%r  char=%r", event.key, event.character)
        key = event.key
        if key in ("up", "k"):
            event.stop()
            self._cursor = (self._cursor - 1) % len(self._CHOICES)
            self.update(self._frog_render_ui())
        elif key in ("down", "j"):
            event.stop()
            self._cursor = (self._cursor + 1) % len(self._CHOICES)
            self.update(self._frog_render_ui())
        elif key == "enter":
            event.stop()
            self._frog_pick(self._CHOICES[self._cursor][0])
        else:
            decision = self._KEY_MAP.get(key)
            if decision is not None:
                event.stop()
                self._frog_pick(decision)

    # 发布决策消息，由宿主 App 负责 IPC 回复和控件清理
    def _frog_pick(self, decision: str) -> None:
        log.debug("FrogPermissionSelect._frog_pick  decision=%s", decision)
        self.post_message(self.Decided(self, self._tool_use_id, decision))


class FrogPermissionBlock(Static):
    """日志里的权限审批摘要"""

    _LABEL_MAP: dict[str, str] = {
        "allow_once":   "allowed (once)",
        "always_allow": "always allowed",
        "deny_once":    "denied",
        "always_deny":  "always denied",
        "timeout":      "⏱ timed out",
    }
    LABEL_MAP = _LABEL_MAP

    # 子类提交消息：用户作出权限决策时发布
    class Resolved(Message):
        def __init__(self, block: FrogPermissionBlock, decision: str) -> None:
            self.block = block
            self.decision = decision
            super().__init__()

    # 初始化审批块，记录工具 ID、名称和参数预览
    def __init__(self, tool_use_id: str, tool_name: str, frog_param_preview: str) -> None:
        self._tool_use_id = tool_use_id
        self._tool_name = tool_name
        self._param_preview = frog_param_preview
        self._resolved = False
        super().__init__(self._frog_pending_text(), classes="log-line")

    def _frog_pending_text(self) -> str:
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        return f"[bold red]? permission[/bold red]  [bold]{self._tool_name}[/bold]{preview}"

    # 将块收缩为单行摘要并发布 Resolved 消息
    def _frog_resolve(self, decision: str) -> None:
        if self._resolved:
            return
        self._resolved = True
        allowed = decision in ("allow_once", "always_allow")
        icon = "[bold green]✓[/bold green]" if allowed else "[bold red]✗[/bold red]"
        label = self._LABEL_MAP.get(decision, decision)
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        self.update(
            f"{icon} permission  [bold]{self._tool_name}[/bold]{preview}  [dim]{label}[/dim]"
        )
        self.post_message(self.Resolved(self, decision))


class FrogSlashCompleteWidget(Static):
    """斜杠命令自动补全弹出框：输入 / 时显示可用 skill 列表并支持键盘筛选与选择。"""

    can_focus = False

    DEFAULT_CSS = """
    FrogSlashCompleteWidget {
        height: auto;
        padding: 0 1;
        margin: 0 1;
        background: #07150f;
        border: round #2dd879;
    }
    """

    # 用户选中某条命令时发布
    class Selected(Message):
        # 初始化，携带被选中的 skill 名称
        def __init__(self, skill_name: str) -> None:
            self.skill_name = skill_name
            super().__init__()

    # 初始化，接收全量 (name, description) 列表
    def __init__(self, items: list[tuple[str, str]]) -> None:
        super().__init__("")
        self._all_items = items
        self._filtered: list[tuple[str, str]] = list(items)
        self._cursor = 0

    # 根据查询字符串筛选列表，重置光标并重新渲染
    def frog_set_query(self, query: str) -> None:
        q = query.lower()
        self._filtered = [(n, d) for n, d in self._all_items if not q or q in n.lower()]
        self._cursor = min(self._cursor, max(0, len(self._filtered) - 1))
        if self.is_attached:
            self._frog_redraw()

    # 向上移动光标并重新渲染
    def frog_move_up(self) -> None:
        if self._filtered:
            self._cursor = (self._cursor - 1) % len(self._filtered)
            self._frog_redraw()

    # 向下移动光标并重新渲染
    def frog_move_down(self) -> None:
        if self._filtered:
            self._cursor = (self._cursor + 1) % len(self._filtered)
            self._frog_redraw()

    # 选中当前光标项并发布 Selected 消息
    def frog_select_current(self) -> None:
        if self._filtered:
            self.post_message(self.Selected(self._filtered[self._cursor][0]))

    # 返回当前是否有可选项
    def frog_has_selection(self) -> bool:
        return len(self._filtered) > 0

    def on_mount(self) -> None:
        self._frog_redraw()

    # 渲染筛选后的命令列表，高亮当前光标项
    def _frog_redraw(self) -> None:
        if not self._filtered:
            self.update("[dim]  no matching commands[/dim]")
            return
        lines: list[str] = []
        for i, (name, desc) in enumerate(self._filtered):
            desc_part = f"  [dim]{desc}[/dim]" if desc else ""
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ /{name}[/bold cyan]{desc_part}")
            else:
                lines.append(f"    [cyan]/{name}[/cyan]{desc_part}")
        lines.append("[dim]  ↑↓ navigate   tab/enter select   esc dismiss[/dim]")
        self.update("\n".join(lines))


class FrogChatTextArea(TextArea):
    """支持 Enter 提交、Cmd/Shift/Alt+Enter 换行的多行聊天输入框。"""

    DEFAULT_CSS = """
    FrogChatTextArea {
        height: auto;
        min-height: 3;
        max-height: 12;
        border: round $surface-lighten-2;
        background: $background;
        padding: 0 1;
        margin: 1 2;
        scrollbar-size-vertical: 1;
    }
    FrogChatTextArea:focus {
        border: round $accent;
        background: $background;
    }
    """

    # 子类自定义的提交消息，供宿主 App 监听
    class Submitted(Message):
        def __init__(self, area: FrogChatTextArea) -> None:
            self.text_area = area
            self.value = area.text
            super().__init__()

    # 输入内容以 / 开头且无空格时发布，query 为 / 之后的字符串（可为空串）；None 表示收起弹窗
    class SlashChanged(Message):
        def __init__(self, query: str | None) -> None:
            self.query = query
            super().__init__()

    # 文本变化时检测 / 前缀，通知宿主 App 更新自动补全弹窗
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = self.text
        if text.startswith("/") and " " not in text:
            self.post_message(FrogChatTextArea.SlashChanged(query=text[1:]))
        else:
            self.post_message(FrogChatTextArea.SlashChanged(query=None))

    # 使用公开 Binding/Action API 拦截 Enter，避免 TextArea 私有 _on_key 在新版本 Textual 中先行吞键
    def action_frog_submit(self) -> None:
        if self.read_only or self.disabled:
            return
        try:
            popup = self.app.query_one(FrogSlashCompleteWidget)
        except NoMatches:
            popup = None
        if popup is not None and popup.frog_has_selection():
            popup.frog_select_current()
        elif self.text.strip():
            app = self.app
            if isinstance(app, FrogTuiApp):
                app._frog_submit_prompt(self, self.text)
            else:
                self.post_message(self.Submitted(self))

    # 组合键显式插入换行，保留聊天输入框的多行编辑能力
    def action_frog_newline(self) -> None:
        if not self.read_only and not self.disabled:
            self.insert("\n")

    # Enter 提交；↑↓/Tab/Esc 路由到自动补全弹窗；Cmd/Shift/Alt+Enter 插入换行；其余键交回 TextArea
    async def _on_key(self, event: events.Key) -> None:
        key = event.key

        popup: FrogSlashCompleteWidget | None = None
        try:
            popup = self.app.query_one(FrogSlashCompleteWidget)
        except NoMatches:
            popup = None

        if popup is not None:
            if key == "up":
                event.stop()
                event.prevent_default()
                popup.frog_move_up()
                return
            elif key == "down":
                event.stop()
                event.prevent_default()
                popup.frog_move_down()
                return
            elif key == "tab":
                event.stop()
                event.prevent_default()
                popup.frog_select_current()
                return
            elif key == "escape":
                event.stop()
                event.prevent_default()
                self.post_message(FrogChatTextArea.SlashChanged(query=None))
                return
        await super()._on_key(event)


class FrogTuiApp(App[None]):
    """FrogCode TUI：青蛙主题仪表盘，实时展示 agent 执行过程。"""

    TITLE = "FrogCode"
    BINDINGS = [
        Binding("ctrl+q", "quit", "quit"),
        Binding("enter", "frog_enter", show=False, priority=True),
        Binding(
            "alt+enter,shift+enter,ctrl+j,super+enter",
            "frog_newline",
            show=False,
            priority=True,
        ),
    ]
    CSS = """
    Screen {
        background: #030806;
        color: #d7f5e3;
    }
    #header {
        width: 100%;
        height: 3;
        background: #07150f;
        color: #d7f5e3;
        border-bottom: solid #2dd879;
        padding: 0 2;
        content-align: left middle;
    }
    #dashboard {
        width: 100%;
        height: 1fr;
        padding: 1;
    }
    #left-rail {
        width: 28;
        padding-right: 1;
    }
    #right-rail {
        width: 34;
        padding-left: 1;
    }
    #center-pane {
        width: 1fr;
        height: 1fr;
        border: round #35d6c1;
        background: #020705;
    }
    #log-view {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-color: #2dd879;
        scrollbar-background: #07150f;
    }
    .frog-panel {
        height: auto;
        min-height: 6;
        border: round #25885a;
        background: #07110d;
        color: #bcebd0;
        padding: 1 1;
        margin-bottom: 1;
    }
    #frog-panel {
        height: 12;
        color: #7cf3a8;
        content-align: center middle;
    }
    #session-panel {
        height: 1fr;
        min-height: 9;
    }
    #run-panel {
        height: 11;
        border: round #35d6c1;
    }
    #activity-panel {
        height: 1fr;
        min-height: 10;
        border: round #c77dff;
    }
    #changes-panel {
        height: 8;
        border: round #ff8fcb;
    }
    #skills-panel {
        height: 9;
        border: round #8db7ff;
        margin-bottom: 0;
    }
    #banner {
        padding: 2 2 1 2;
        color: #bcebd0;
        content-align: center top;
    }
    Static.user-turn {
        color: #e7fff0;
        background: #0a1a12;
        border-left: thick #2dd879;
        padding: 1 2;
        margin: 1 1 0 1;
    }
    Static.run-header { color: #7dc9b0; padding: 1 2 0 2; }
    Static.step-divider { color: #69a88f; padding: 0 2; }
    Static.run-ok { color: #54e88d; padding: 0 2 1 2; }
    Static.run-err { color: #ff6b7a; padding: 0 2 1 2; }
    Static.usage { padding: 0 2; }
    Static.log-line { padding: 0 2; }
    #prompt {
        margin: 0 1 1 1;
        border: round #25885a;
        background: #06100b;
    }
    #prompt:focus {
        border: round #7cf3a8;
        background: #08170f;
    }
    #footer {
        width: 100%;
        height: 1;
        background: #07150f;
        color: #73b99a;
        padding: 0 2;
    }
    Screen.frog-compact #right-rail { display: none; }
    Screen.frog-narrow #left-rail { display: none; }
    Screen.frog-narrow #dashboard { padding: 0; }
    """

    _BANNER = (
        "[bold #7cf3a8]F  R  O  G  C  O  D  E[/bold #7cf3a8]\n"
        "[#35d6c1]Local Agent Pond[/#35d6c1]\n\n"
        "[bold #7cf3a8]      @..@[/bold #7cf3a8]\n"
        "[bold #7cf3a8]     (----)[/bold #7cf3a8]\n"
        "[bold #7cf3a8]    ( >__< )[/bold #7cf3a8]\n"
        "[bold #7cf3a8]    ^^ ~~ ^^[/bold #7cf3a8]\n\n"
        "[dim]在下方输入目标并按 Enter · 输入 / 查看 Frog Skills[/dim]"
    )

    _FROG_MASCOT = (
        "[bold #7cf3a8]   @..@[/bold #7cf3a8]\n"
        "[bold #7cf3a8]  (----)[/bold #7cf3a8]\n"
        "[bold #7cf3a8] ( >__< )[/bold #7cf3a8]\n"
        "[bold #7cf3a8] ^^ ~~ ^^[/bold #7cf3a8]\n"
        "[dim]FROG IS READY[/dim]"
    )

    # 初始化连接参数和 TUI 内部状态
    def __init__(
        self,
        host: str,
        port: int,
        replay_run_id: str | None = None,
        model: str = "auto",
    ) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._replay_run_id = replay_run_id
        self._model = model
        self._client: FrogSocketClient | None = None
        self._current_llm: FrogLLMStreamBlock | None = None
        self._pending_tool_blocks: dict[str, FrogToolCallBlock] = {}
        self._pending_permission_blocks: dict[str, FrogPermissionBlock] = {}
        self._session_id: str | None = None
        self._busy = False
        self._last_context_pct: float = 0.0
        self._slash_items: list[tuple[str, str]] = []
        self._subagent_run_ids: dict[str, str] = {}  # child run_id -> description
        self._subagent_start_times: dict[str, float] = {}  # child run_id -> start time
        self._connection_state = "connecting"
        self._current_run_id = ""
        self._current_goal = ""
        self._current_step = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_tokens = 0
        self._tool_count = 0
        self._message_count = 0
        self._recent_activity: list[str] = []
        self._changed_paths: list[str] = []

    def compose(self) -> ComposeResult:
        yield Label("[bold #7cf3a8]🐸 FROGCODE[/bold #7cf3a8]  [dim]awakening the pond...[/dim]", id="header")
        with Horizontal(id="dashboard"):
            with Vertical(id="left-rail"):
                yield Static("", id="status-panel", classes="frog-panel")
                yield Static(self._FROG_MASCOT, id="frog-panel", classes="frog-panel")
                yield Static("", id="session-panel", classes="frog-panel")
            with Vertical(id="center-pane"):
                yield VerticalScroll(id="log-view")
                yield FrogChatTextArea(id="prompt", show_line_numbers=False)
            with Vertical(id="right-rail"):
                yield Static("", id="run-panel", classes="frog-panel")
                yield Static("", id="activity-panel", classes="frog-panel")
                yield Static("", id="changes-panel", classes="frog-panel")
                yield Static("", id="skills-panel", classes="frog-panel")
        yield Label(
            "[bold #7cf3a8]Enter[/bold #7cf3a8] send  "
            "[bold]Shift+Enter[/bold] newline  [bold]/[/bold] skills  [bold]Ctrl+Q[/bold] quit",
            id="footer",
        )

    def on_mount(self) -> None:
        self._slash_items = self._frog_build_slash_items()
        self._frog_append(Static(self._BANNER, id="banner"))
        self._frog_refresh_sidebars()
        self.run_worker(self._frog_socket_loop(), exclusive=True, name="socket")
        prompt = self.query_one("#prompt", FrogChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."

    # 根据终端宽度隐藏辅助栏，保证聊天和输入在窄窗口中仍然可用
    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(event.size.width < 118, "frog-compact")
        self.screen.set_class(event.size.width < 82, "frog-narrow")

    # App 级 priority binding 会在 TextArea 私有处理器前收到 Enter
    def action_frog_enter(self) -> None:
        # 权限请求优先于普通输入。挂载控件时 VerticalScroll 可能暂时持有焦点，
        # 因此不能只依赖 isinstance(self.focused, FrogPermissionSelect)。
        if self._pending_permission_blocks:
            try:
                select = self.query_one(FrogPermissionSelect)
            except NoMatches:
                return
            select._frog_pick(FrogPermissionSelect._CHOICES[select._cursor][0])
            return
        focused = self.focused
        if isinstance(focused, FrogChatTextArea):
            focused.action_frog_submit()

    # 将组合键换行转发给当前聊天输入框
    def action_frog_newline(self) -> None:
        if isinstance(self.focused, FrogChatTextArea):
            self.focused.action_frog_newline()

    # 构建斜杠命令候选列表：内建命令 + 所有已注册 skill
    def _frog_build_slash_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = [("frog-compact", "compress context window")]
        try:
            loader = FrogSkillLoader()
            for skill in loader.frog_list_all_skills():
                desc = skill.description.splitlines()[0] if skill.description else ""
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                items.append((skill.name, desc))
        except Exception:
            pass
        return items

    # 根据 / 前缀查询字符串挂载、更新或移除自动补全弹窗
    def on_frog_chat_text_area_slash_changed(self, event: FrogChatTextArea.SlashChanged) -> None:
        query = event.query
        if query is None:
            try:
                self.query_one(FrogSlashCompleteWidget).remove()
            except NoMatches:
                pass
            return
        try:
            popup = self.query_one(FrogSlashCompleteWidget)
            popup.frog_set_query(query)
        except NoMatches:
            popup = FrogSlashCompleteWidget(self._slash_items)
            self.query_one("#center-pane", Vertical).mount(popup, before="#prompt")
            popup.frog_set_query(query)

    # 用户选中自动补全项后将 /{name} 填入输入框并移除弹窗
    def on_frog_slash_complete_widget_selected(self, event: FrogSlashCompleteWidget.Selected) -> None:
        prompt = self._frog_prompt()
        if prompt is not None:
            prompt.text = f"/{event.skill_name} "
            prompt.move_cursor(prompt.document.end)
        try:
            self.query_one(FrogSlashCompleteWidget).remove()
        except NoMatches:
            pass

    # 记录按键焦点；当 FrogPermissionSelect 失去焦点后作为兜底处理权限快捷键
    def on_key(self, event: events.Key) -> None:
        log.debug("App.on_key  key=%r  focused=%r", event.key, self.focused)
        if not self._pending_permission_blocks:
            return
        try:
            select = self.query_one(FrogPermissionSelect)
            if select.has_focus:
                return  # FrogPermissionSelect 有焦点时自行处理，事件不会冒泡到这里
            key = event.key
            decision = FrogPermissionSelect._KEY_MAP.get(key)
            if decision:
                event.stop()
                select._frog_pick(decision)
            elif key in ("up", "k"):
                event.stop()
                select._cursor = (select._cursor - 1) % len(FrogPermissionSelect._CHOICES)
                select.update(select._frog_render_ui())
            elif key in ("down", "j"):
                event.stop()
                select._cursor = (select._cursor + 1) % len(FrogPermissionSelect._CHOICES)
                select.update(select._frog_render_ui())
            elif key == "enter":
                event.stop()
                select._frog_pick(FrogPermissionSelect._CHOICES[select._cursor][0])
        except Exception:
            pass

    # 退出前尽力关闭当前 session，失败也不阻塞 TUI 退出
    async def action_quit(self) -> None:
        if self._client is not None and self._session_id is not None:
            try:
                await self._client.frog_send_command("frog.session.close", {"session_id": self._session_id})
            except (FrogIpcError, RuntimeError, OSError):
                self._frog_append(Static("[yellow]warning: failed to close session[/yellow]"))
        self.exit()

    # 将输入框提交内容发送给当前 frog_chat session；用 worker 发送，避免 await 阻塞 App 消息泵
    async def on_frog_chat_text_area_submitted(self, event: FrogChatTextArea.Submitted) -> None:
        self._frog_submit_prompt(event.text_area, event.value)

    # 同步处理输入提交，供 Textual 消息和 App 级 Enter action 共用
    def _frog_submit_prompt(self, prompt: FrogChatTextArea, value: str) -> None:
        content = value.strip()
        if not content:
            return
        # 检测 /frog-compact 指令
        if content == "/frog-compact":
            prompt.text = ""
            if self._client is not None and self._session_id is not None and not self._busy:
                self.run_worker(self._frog_do_compact(), name="frog_compact", exclusive=False)
            return
        if self._client is None or self._session_id is None or self._busy:
            self._frog_append(Static("[yellow]agent busy or disconnected[/yellow]", classes="log-line"))
            return
        self._busy = True
        self._message_count += 1
        prompt.text = ""
        prompt.disabled = True
        prompt.read_only = False
        prompt.border_title = "agent is working..."
        self._frog_append(Static(f"[bold]>[/bold] {content}", classes="user-turn"))
        self._frog_refresh_sidebars()
        self._frog_update_header("running")
        self.run_worker(self._frog_do_send_message(content), name="frog_send_message", exclusive=False)

    # 在 worker 中执行手动压缩命令，完成后显示结果横幅
    async def _frog_do_compact(self) -> None:
        if self._client is None or self._session_id is None:
            return
        self._frog_append(Static("[dim]⚡ compacting context...[/dim]", classes="log-line"))
        try:
            result = await self._client.frog_send_command(
                "frog.session.compact",
                {"session_id": self._session_id, "focus": ""},
            )
            summary_tokens = result.get("summary_tokens", 0)
            saved_tokens = result.get("saved_tokens", 0)
            self._last_context_pct = 0.0
            self._frog_append(Static(
                f"[bold cyan]⚡ Context compacted[/bold cyan]"
                f"  [dim]summary={summary_tokens} tokens  saved≈{saved_tokens} tokens[/dim]",
                classes="log-line",
            ))
        except (FrogIpcError, RuntimeError, OSError) as e:
            self._frog_append(Static(f"[red]Frog compact error: {e}[/red]", classes="log-line"))

    # 在 worker 中执行 IPC 发送，使 App 消息泵在 agent 运行期间仍能处理键盘/焦点等消息
    async def _frog_do_send_message(self, content: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.frog_send_command(
                "frog.session.send_message",
                {"session_id": self._session_id, "content": content},
            )
        except (FrogIpcError, RuntimeError, OSError) as e:
            self._busy = False
            prompt = self._frog_prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
            self._frog_update_header("ready")
            self._frog_append(Static(f"[red]send error: {e}[/red]", classes="log-line"))

    # 处理内联审批控件的用户决策：发送 IPC 响应并恢复输入框
    async def on_frog_permission_select_decided(self, msg: FrogPermissionSelect.Decided) -> None:
        tool_use_id = msg.tool_use_id
        decision = msg.decision
        log.info("permission decided tool_use_id=%s decision=%s", tool_use_id, decision)
        try:
            msg.widget.remove()
            perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
            if perm_block is not None:
                perm_block._frog_resolve(decision)
            if self._client is not None:
                try:
                    await self._client.frog_send_command(
                        "frog.permission.respond",
                        {"tool_use_id": tool_use_id, "decision": decision},
                    )
                except (FrogIpcError, RuntimeError, OSError):
                    pass
            if not self._pending_permission_blocks:
                p = self._frog_prompt()
                if p is not None:
                    p.disabled = False
                    p.read_only = False
                    p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    p.focus()
        except Exception:
            log.exception("on_permission_select_decided failed tool_use_id=%s", tool_use_id)

    # 向日志视图追加一个 widget 并滚动到底部
    def _frog_append(self, widget: Widget) -> None:
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)

    # 结束当前 LLM 流式块（下一个 token 将开启新块）
    def _frog_break_llm(self) -> None:
        if self._current_llm is not None:
            self._current_llm.frog_finalize_markdown()
        self._current_llm = None

    # 将选择控件挂载到聊天主栏（#prompt 之前），保持仪表盘布局并避免日志区争抢焦点
    def _frog_mount_permission_select(self, select: FrogPermissionSelect) -> None:
        self.query_one("#center-pane", Vertical).mount(select, before="#prompt")

    # 安全获取输入框，便于组件测试中未挂载时跳过 UI 操作
    def _frog_prompt(self) -> FrogChatTextArea | None:
        try:
            return self.query_one("#prompt", FrogChatTextArea)
        except Exception:
            return None

    # 生成 context 占用率的彩色进度条字符串
    def _frog_render_ctx_bar(self, pct: float) -> str:
        filled = int(pct * 20)
        bar = "█" * filled + "░" * (20 - filled)
        label = f"ctx:{pct * 100:.1f}%"
        if pct >= 0.85:
            color = "bold red"
        elif pct >= 0.70:
            color = "yellow"
        else:
            color = "dim"
        return f"[{color}]{label} {bar}[/{color}]"

    # 向右侧活动面板追加一条记录，并限制面板只保留最近事件
    def _frog_add_activity(self, text: str) -> None:
        self._recent_activity.append(_frog_preview(text, 42))
        del self._recent_activity[:-8]

    # 更新左右辅助栏；未挂载的组件测试中静默跳过
    def _frog_refresh_sidebars(self) -> None:
        state_color = {
            "ready": "#54e88d",
            "running": "#ffd166",
            "success": "#54e88d",
            "failed": "#ff6b7a",
            "disconnected": "#ff6b7a",
            "connecting": "#7dc9b0",
        }.get(self._connection_state, "#7dc9b0")
        status_text = (
            "[bold #7cf3a8]POND STATUS[/bold #7cf3a8]\n"
            f"[{state_color}]● {self._connection_state.upper()}[/{state_color}]\n"
            f"[dim]endpoint[/dim]  {self._host}:{self._port}\n"
            f"[dim]model[/dim]     {_frog_preview(self._model, 20)}"
        )
        session_text = (
            "[bold #7cf3a8]SESSION[/bold #7cf3a8]\n"
            f"[cyan]{_frog_preview(self._session_id or 'not connected', 23)}[/cyan]\n\n"
            f"[dim]messages[/dim]  {self._message_count}\n"
            f"[dim]context[/dim]   {self._last_context_pct * 100:.1f}%"
        )
        run_state = self._connection_state if self._current_run_id else "idle"
        run_text = (
            "[bold #35d6c1]CURRENT RUN[/bold #35d6c1]\n"
            f"[dim]state[/dim]  {run_state}\n"
            f"[dim]id[/dim]     {_frog_preview(self._current_run_id or '—', 20)}\n"
            f"[dim]step[/dim]   {self._current_step}    [dim]tools[/dim] {self._tool_count}\n"
            f"[dim]tokens[/dim] ↑{self._input_tokens} ↓{self._output_tokens}\n"
            f"[dim]goal[/dim]   {_frog_preview(self._current_goal or 'waiting for a goal', 25)}"
        )
        activity_lines = "\n".join(f"[dim]•[/dim] {item}" for item in self._recent_activity)
        activity_text = (
            "[bold #c77dff]ACTIVITY[/bold #c77dff]\n"
            + (activity_lines or "[dim]No activity yet[/dim]")
        )
        change_lines = "\n".join(f"[#ff8fcb]∆[/#ff8fcb] {_frog_preview(path, 26)}" for path in self._changed_paths[-4:])
        changes_text = (
            "[bold #ff8fcb]CHANGES[/bold #ff8fcb]\n"
            + (change_lines or "[dim]No files changed[/dim]")
        )
        skill_names = [name for name, _ in self._slash_items[:5]]
        skills_text = (
            "[bold #8db7ff]FROG SKILLS[/bold #8db7ff]\n"
            + ("\n".join(f"[#8db7ff]/{name}[/#8db7ff]" for name in skill_names) or "[dim]No skills loaded[/dim]")
        )

        for widget_id, content in (
            ("#status-panel", status_text),
            ("#session-panel", session_text),
            ("#run-panel", run_text),
            ("#activity-panel", activity_text),
            ("#changes-panel", changes_text),
            ("#skills-panel", skills_text),
        ):
            try:
                self.query_one(widget_id, Static).update(content)
            except Exception:
                # 纯事件单元测试会在 App 尚未挂载 Screen 时调用此方法。
                pass

    # 从现有事件流提取仪表盘摘要，不改变聊天区原有事件处理
    def _frog_track_dashboard_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        changed = True
        if event_type == "frog.run.started":
            self._current_run_id = str(event.get("run_id", ""))
            self._current_goal = str(event.get("goal", ""))
            self._current_step = 0
            self._tool_count = 0
            self._connection_state = "running"
            self._frog_add_activity(f"run started · {_frog_preview(self._current_goal, 24)}")
        elif event_type == "frog.run.finished":
            self._connection_state = str(event.get("status", "finished"))
            self._frog_add_activity(f"run {self._connection_state} · {event.get('steps', 0)} steps")
        elif event_type == "frog.step.started":
            run_id = str(event.get("run_id", ""))
            if run_id not in self._subagent_run_ids:
                self._current_step = int(event.get("step") or 0)
                self._frog_add_activity(f"step {self._current_step} planning")
        elif event_type == "frog.llm.model_selected":
            self._model = str(event.get("model", self._model))
        elif event_type == "frog.llm.usage":
            run_id = str(event.get("run_id", ""))
            if run_id not in self._subagent_run_ids:
                self._input_tokens = int(event.get("input_tokens") or 0)
                self._output_tokens = int(event.get("output_tokens") or 0)
                self._cache_tokens = int(event.get("cache_read_input_tokens") or 0)
                self._last_context_pct = float(event.get("context_pct") or 0.0)
        elif event_type == "frog.tool.call_started":
            tool_name = str(event.get("tool_name", "tool"))
            params = event.get("params") or {}
            self._tool_count += 1
            self._frog_add_activity(f"{tool_name} · {_frog_param_summary(tool_name, params, 24)}")
            if tool_name == "frog_write_file":
                path = str(params.get("path", ""))
                if path and path not in self._changed_paths:
                    self._changed_paths.append(path)
        elif event_type == "frog.skill.invoked":
            self._frog_add_activity(f"skill /{event.get('skill_name', '')}")
        elif event_type == "frog.permission.requested":
            self._frog_add_activity(f"permission · {event.get('tool_name', '')}")
        elif event_type == "frog.subagent.started":
            self._frog_add_activity(f"subagent · {event.get('description', '')}")
        elif event_type in ("frog.session.waiting_for_input", "frog.session.created"):
            self._connection_state = "ready"
        elif event_type == "frog.session.closed":
            self._connection_state = "disconnected"
        else:
            changed = False

        if changed:
            self._frog_refresh_sidebars()

    # 根据连接和运行状态刷新顶部标题
    def _frog_update_header(self, state: str) -> None:
        try:
            header = self.query_one("#header", Label)
        except NoMatches:
            return
        self._connection_state = state
        self._frog_refresh_sidebars()
        session = f"  [dim]{self._session_id}[/dim]" if self._session_id else ""
        color = {
            "ready": "#54e88d",
            "running": "#ffd166",
            "success": "#54e88d",
            "failed": "#ff6b7a",
            "disconnected": "#ff6b7a",
            "connecting": "#7dc9b0",
        }.get(state, "#7dc9b0")
        header.update(
            f"[bold #7cf3a8]🐸 FROGCODE[/bold #7cf3a8]  [dim]LOCAL AGENT POND[/dim]"
            f"  [{color}]● {state.upper()}[/{color}]  [dim]{self._host}:{self._port}[/dim]{session}"
        )

    # 管理 FrogSocketClient 生命周期：连接、订阅事件、断线重连
    async def _frog_socket_loop(self) -> None:
        header = self.query_one("#header", Label)

        while True:
            client = FrogSocketClient(self._host, self._port)
            self._client = None
            try:
                await client.connect()
            except (ConnectionRefusedError, OSError):
                log.warning("connection refused %s:%s, retrying", self._host, self._port)
                self._frog_update_header("disconnected")
                await asyncio.sleep(2)
                continue

            log.info("connected to %s:%s", self._host, self._port)
            self._client = client
            self._frog_update_header("connecting")
            loop_task = asyncio.create_task(client.frog_run_event_loop())

            async def on_event(event: dict[str, Any]) -> None:
                self._frog_handle_event(event)

            client.on_event(on_event)

            try:
                loop_task.add_done_callback(
                    lambda t: log.error("loop_task failed: %s", t.exception())
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
                params: dict[str, Any] = {
                    "topics": [
                        "frog.session.*",
                        "frog.run.*",
                        "frog.step.*",
                        "frog.tool.*",
                        "frog.llm.token",
                        "frog.llm.usage",
                        "frog.llm.model_selected",
                        "frog.log.*",
                        "frog.permission.*",
                        "frog.context.*",
                        "frog.subagent.*",
                        "frog.skill.*",
                    ],
                    "scope": "global",
                }
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                await client.frog_send_command("frog.event.subscribe", params)
                created = await client.frog_send_command("frog.session.create", {"mode": "chat"})
                self._session_id = str(created["session_id"])
                log.info("session created session_id=%s", self._session_id)
                prompt = self._frog_prompt()
                if prompt is not None:
                    prompt.disabled = False
                    prompt.read_only = False
                    prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    prompt.focus()
                self._frog_update_header("ready")
                await loop_task
            except FrogIpcError as e:
                header.update(f"[bold]FrogCode[/bold]  [red]subscription error: {e}[/red]")
            finally:
                if not loop_task.done():
                    loop_task.cancel()
                self._client = None
                self._session_id = None
                prompt = self._frog_prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.read_only = False
                    prompt.border_title = "disconnected, retrying..."
                self._frog_break_llm()
                await client.close()

            self._frog_update_header("disconnected")
            await asyncio.sleep(2)

    # 根据事件 type 路由到对应渲染逻辑；捕获异常防止 socket loop 因单个事件崩溃
    def _frog_handle_event(self, event: dict[str, Any]) -> None:
        try:
            self._frog_handle_event_inner(event)
        except Exception:
            log.exception("_frog_handle_event crashed  event_type=%s", event.get("type", "?"))

    # 实际的事件路由逻辑
    def _frog_handle_event_inner(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")
        self._frog_track_dashboard_event(event)

        if t == "frog.llm.token":
            token = event.get("token", "")
            if self._current_llm is None:
                llm_block = FrogLLMStreamBlock()
                self._frog_append(llm_block)
                self._current_llm = llm_block
            self._current_llm.frog_append_token(token)
            return

        self._frog_break_llm()

        if t == "frog.session.waiting_for_input":
            self._busy = False
            prompt = self._frog_prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.read_only = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                prompt.focus()
            self._frog_update_header("ready")

        elif t == "frog.session.closed":
            self._busy = False
            prompt = self._frog_prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.read_only = False
                prompt.border_title = "session closed"
            self._frog_update_header("disconnected")

        elif t == "frog.run.started":
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            self._frog_append(Static(
                f"[dim]run[/dim]  [cyan]{run_id}[/cyan]  [dim]{_frog_preview(goal, 96)}[/dim]",
                classes="run-header",
            ))

        elif t == "frog.skill.invoked":
            skill_name = event.get("skill_name", "")
            arguments = event.get("arguments", "")
            args_preview = _frog_preview(arguments, 80) if arguments else ""
            args_part = f"  [dim]{args_preview}[/dim]" if args_preview else ""
            self._frog_append(Static(
                f"[bold cyan]/{skill_name}[/bold cyan]{args_part}",
                classes="log-line",
            ))

        elif t == "frog.subagent.started":
            run_id = event.get("run_id", "")
            description = event.get("description", "")
            self._subagent_run_ids[run_id] = description
            self._subagent_start_times[run_id] = time.monotonic()
            short_id = run_id[:8] if len(run_id) >= 8 else run_id
            self._frog_append(Static(
                f"[dim]┌─[/dim] [cyan]{_frog_preview(description, 72)}[/cyan]  [dim]{short_id}[/dim]",
                classes="log-line",
            ))

        elif t == "frog.subagent.finished":
            run_id = event.get("run_id", "")
            status = event.get("status", "")
            description = self._subagent_run_ids.pop(run_id, event.get("description", ""))
            start = self._subagent_start_times.pop(run_id, None)
            frog_elapsed = f"  [dim]{time.monotonic() - start:.1f}s[/dim]" if start is not None else ""
            desc_part = f"[cyan]{_frog_preview(description, 72)}[/cyan]{frog_elapsed}"
            if status == "success":
                self._frog_append(Static(
                    f"[dim]└─[/dim] [bold green]✓[/bold green] {desc_part}",
                    classes="log-line",
                ))
            else:
                self._frog_append(Static(
                    f"[dim]└─[/dim] [bold red]✗[/bold red] {desc_part}",
                    classes="log-line",
                ))

        elif t == "frog.step.started":
            run_id = event.get("run_id", "")
            if run_id in self._subagent_run_ids:
                return
            step = event.get("step", "")
            self._frog_append(Static(
                f"[dim]step {step}[/dim]",
                classes="step-divider",
            ))

        elif t == "frog.tool.call_started":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            run_id = event.get("run_id", "")
            tc_block = FrogToolCallBlock(tool_name, params)
            if run_id in self._subagent_run_ids:
                tc_block.styles.padding = (0, 2, 0, 6)
            self._pending_tool_blocks[tool_use_id] = tc_block
            self._frog_append(tc_block)

        elif t == "frog.tool.call_finished":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.frog_set_result(output, elapsed_ms)

        elif t == "frog.tool.call_failed":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.frog_set_result(error_msg, elapsed_ms, is_error=True)

        elif t == "frog.run.finished":
            status = event.get("status", "")
            steps = event.get("steps", 0)
            reason = event.get("reason") or ""
            if status == "success":
                self._frog_append(Static(
                    f"[bold green]✓ completed[/bold green]  [dim]{steps} steps[/dim]",
                    classes="run-ok",
                ))
            else:
                detail = f"  [dim]{reason}[/dim]" if reason else ""
                self._frog_append(Static(
                    f"[bold red]✗ failed[/bold red]{detail}  [dim]{steps} steps[/dim]",
                    classes="run-err",
                ))

        elif t == "frog.llm.usage":
            run_id = event.get("run_id", "")
            if run_id in self._subagent_run_ids:
                return
            pct = float(event.get("context_pct") or 0.0)
            self._last_context_pct = pct
            ctx_bar = self._frog_render_ctx_bar(pct)
            self._frog_append(Static(
                f"[dim]  tokens  "
                f"in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache={event.get('cache_read_input_tokens')}[/dim]"
                f"  {ctx_bar}",
                classes="usage",
            ))

        elif t == "frog.context.compacted":
            orig = event.get("original_tokens", 0)
            summary = event.get("summary_tokens", 0)
            self._last_context_pct = 0.0
            self._frog_append(Static(
                f"[bold cyan]⚡ Context compacted[/bold cyan]"
                f"  [dim]original≈{orig} tokens → summary={summary} tokens[/dim]",
                classes="log-line",
            ))

        elif t == "frog.permission.requested":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            frog_param_preview = str(event.get("frog_param_preview", ""))
            try:
                _focused_repr = repr(self.focused)
            except Exception:
                _focused_repr = "?"
            log.info(
                "frog.permission.requested tool=%s id=%s  app.focused=%s",
                tool_name, tool_use_id, _focused_repr,
            )
            perm_block = FrogPermissionBlock(tool_use_id, tool_name, frog_param_preview)
            self._pending_permission_blocks[tool_use_id] = perm_block
            prompt = self._frog_prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.border_title = "permission required"
            self._frog_append(perm_block)
            select = FrogPermissionSelect(tool_use_id)
            self._frog_mount_permission_select(select)
            log.debug("FrogPermissionSelect mounted before #prompt  pending=%d", len(self._pending_permission_blocks))

        elif t == "frog.permission.denied":
            # 处理超时或断连等非用户交互触发的 deny（用户主动 deny 已由 on_permission_select_decided 处理）
            tool_use_id = str(event.get("tool_use_id", ""))
            decision = str(event.get("decision", "denied"))
            if tool_use_id in self._pending_permission_blocks:
                perm_block = self._pending_permission_blocks.pop(tool_use_id)
                perm_block._frog_resolve(decision)
                try:
                    select = self.query_one(FrogPermissionSelect)
                    select.remove()
                except Exception:
                    pass
                if not self._pending_permission_blocks:
                    p = self._frog_prompt()
                    if p is not None:
                        p.disabled = False
                        p.read_only = False
                        p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                        p.focus()

        elif t == "frog.log.line":
            level = event.get("level", "INFO")
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            self._frog_append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))


# TUI 入口：读取配置并启动 FrogTuiApp
def frog_run(config: FrogConfig, replay_run_id: str | None = None) -> None:
    app = FrogTuiApp(
        config.host,
        config.port,
        replay_run_id=replay_run_id,
        model=config.llm.default_model,
    )
    app.run()

"""侧栏与状态栏：展示运行时信息，不承载业务逻辑。"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from ...core.application.runtime import RuntimeInfo
from ...core.application.session import ChatSession
from ...core.application.settings import AgentSettings


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Sidebar(VerticalScroll):
    def __init__(self, info: RuntimeInfo, settings: AgentSettings, id: str | None = "sidebar") -> None:
        super().__init__(id=id)
        self._info = info
        self._settings = settings
        self._session_body = Static("", classes="sidebar-body")
        self._tools_body = Static("", classes="sidebar-body")

    def compose(self) -> ComposeResult:
        yield Static("会话", classes="section-title")
        yield self._session_body
        yield Static(f"工具 · {len(self._info.tools)}", classes="section-title")
        yield self._tools_body
        yield Static("Ctrl+B 收起侧栏 · F1 帮助", classes="sidebar-hint")

    def on_mount(self) -> None:
        self._render_tools()
        self.refresh_session(None)

    def _render_tools(self) -> None:
        body = Text()
        for definition in self._info.tools:
            body.append(f"• {definition.name}\n", style="bold")
            body.append(f"  {_shorten(definition.description, 34)}\n", style="dim")
        self._tools_body.update(body)

    def refresh_session(self, session: ChatSession | None, *, state: str = "就绪", elapsed_s: float = 0.0) -> None:
        body = Text()
        body.append("模型  ", style="dim")
        body.append(f"{self._info.model}\n")
        body.append("接口  ", style="dim")
        body.append(f"{_shorten(self._info.base_url, 32)}\n")
        body.append("目录  ", style="dim")
        body.append(f"{_shorten(self._info.workspace, 32)}\n")
        if session is not None:
            body.append("会话  ", style="dim")
            body.append(f"{session.id}\n")
            body.append("轮次  ", style="dim")
            body.append(f"{session.turn_count}\n")
            body.append("消息  ", style="dim")
            body.append(f"{session.message_count}\n")
        body.append("上限  ", style="dim")
        body.append(f"{self._settings.max_iterations} 次模型调用/轮\n")
        body.append("状态  ", style="dim")
        body.append(state)
        if elapsed_s:
            body.append(f"（{elapsed_s:.1f}s）", style="dim")
        self._session_body.update(body)


class StatusBar(Static):
    """一行状态栏：空闲 / 生成中 / 调用工具 / 出错。"""

    def __init__(self, model: str, id: str | None = "status") -> None:
        super().__init__("", id=id)
        self._model = model
        self._state = "idle"
        self._label = "就绪"
        self._iteration = 0
        self._max_iterations = 0
        self._tools = 0
        self._elapsed_s = 0.0

    def on_mount(self) -> None:
        self._refresh_status()

    @property
    def state(self) -> str:
        return self._state

    @property
    def label(self) -> str:
        return self._label

    def set_idle(self, *, turns: int = 0, elapsed_s: float = 0.0, tools: int = 0) -> None:
        self._state = "idle"
        self._label = "就绪" if not turns else f"就绪 · {turns} 轮"
        self._elapsed_s = elapsed_s
        self._tools = tools
        self._refresh_status()

    def set_thinking(self, iteration: int = 1, max_iterations: int = 0) -> None:
        self._state = "thinking"
        self._label = "生成中"
        self._iteration = iteration
        self._max_iterations = max_iterations
        self._refresh_status()

    def set_tool(self, name: str) -> None:
        self._state = "tool"
        self._label = f"调用工具 {name}"
        self._refresh_status()

    def set_error(self, message: str) -> None:
        self._state = "error"
        self._label = _shorten(message.replace("\n", " "), 70)
        self._refresh_status()

    def set_cancelled(self) -> None:
        self._state = "idle"
        self._label = "已中断"
        self._refresh_status()

    def _refresh_status(self) -> None:
        colors = {
            "idle": "green",
            "thinking": "yellow",
            "tool": "cyan",
            "error": "red",
        }
        icons = {"idle": "●", "thinking": "◐", "tool": "🔧", "error": "✖"}
        text = Text()
        text.append(f"{icons[self._state]} ", style=f"bold {colors[self._state]}")
        text.append(self._label)
        text.append("  │  ", style="dim")
        text.append(self._model, style="dim")
        if self._state == "thinking" and self._max_iterations:
            text.append(f"  │  模型调用 {self._iteration}/{self._max_iterations}", style="dim")
        if self._tools:
            text.append(f"  │  工具 {self._tools} 次", style="dim")
        if self._elapsed_s:
            text.append(f"  │  {self._elapsed_s:.1f}s", style="dim")
        text.append("  │  Esc 中断", style="dim")
        self.update(text)


# 说明：侧栏 / 状态栏只做展示，不参与 Agent 决策。

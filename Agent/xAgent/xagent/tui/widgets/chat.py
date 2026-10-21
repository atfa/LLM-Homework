"""聊天区组件：用户消息、流式助手消息、工具调用卡片、系统提示。"""

from __future__ import annotations

from typing import Literal

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible, Markdown, Static

from ...core.domain.tools import ToolCall, ToolOutcome
from ..formatting import pretty_arguments, summarize_arguments, truncate

NoticeLevel = Literal["info", "success", "warning", "error"]

_NOTICE_ICON: dict[NoticeLevel, str] = {
    "info": "•",
    "success": "✓",
    "warning": "!",
    "error": "✗",
}
_NOTICE_CLASS: dict[NoticeLevel, str] = {
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "error",
}


class UserMessage(Static):
    def __init__(self, prompt: str) -> None:
        body = Text()
        body.append("你\n", style="bold")
        body.append(prompt)
        super().__init__(body, classes="msg user")


class AssistantMessage(Vertical):
    """流式助手消息：增量累积，由 :class:`ChatView` 定时刷到 Markdown。"""

    def __init__(self, author: str = "xAgent") -> None:
        super().__init__(classes="msg assistant streaming")
        self._author = author
        self._markdown = Markdown("", classes="md")
        self._chunks: list[str] = []
        self._dirty = False

    def compose(self) -> ComposeResult:
        yield Static(f"{self._author}", classes="role assistant-role")
        yield self._markdown

    def append(self, text: str) -> None:
        if text:
            self._chunks.append(text)
            self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        self._markdown.update("".join(self._chunks))
        self._dirty = False

    def finish(self) -> None:
        self.flush()
        self.remove_class("streaming")

    @property
    def text(self) -> str:
        """已收流式文本（便于测试与外部导出）。"""
        return "".join(self._chunks)


class ToolActivity(Collapsible):
    """工具调用卡片：默认折叠，失败自动展开。"""

    def __init__(self, call: ToolCall) -> None:
        self._call = call
        self._body = Static(Text("运行中…", style="dim"), classes="tool-body")
        super().__init__(
            self._body,
            title=f"🔧 {call.name}({summarize_arguments(call.arguments)})",
            collapsed=True,
            classes="msg tool running",
        )

    def finish(self, outcome: ToolOutcome, elapsed_s: float) -> None:
        self.remove_class("running")
        self.add_class("ok" if outcome.ok else "error")
        icon = "✓" if outcome.ok else "✗"
        summary = summarize_arguments(self._call.arguments)
        self.title = escape(f"{icon} {self._call.name}({summary}) · {elapsed_s * 1000:.0f} ms")
        self._body.update(self._render_body(outcome))
        if not outcome.ok:
            self.collapsed = False

    def _render_body(self, outcome: ToolOutcome) -> Text:
        body = Text()
        body.append("参数\n", style="bold")
        body.append(truncate(pretty_arguments(self._call.arguments), 1200) + "\n\n")
        body.append("结果\n", style="bold")
        body.append(truncate(outcome.content or "（无输出）", 4000))
        return body


class NoticeMessage(Static):
    def __init__(self, message: str, level: NoticeLevel = "info") -> None:
        self.message = message
        self.level = level
        body = Text()
        body.append(f"{_NOTICE_ICON[level]} ", style="bold")
        body.append(message)
        super().__init__(body, classes=f"notice {_NOTICE_CLASS[level]}")


class WelcomeMessage(Static):
    def __init__(self, *, model: str, workspace: str, tool_count: int) -> None:
        body = Text()
        body.append("xAgent", style="bold")
        body.append(f"  ·  {tool_count} 个工具  ·  {model}\n", style="dim")
        body.append(f"工作目录  {workspace}\n", style="dim")
        body.append("Enter 发送   /help 命令   Esc 中断生成   Ctrl+B 侧栏   Ctrl+N 新会话")
        super().__init__(body, classes="welcome")


class ChatView(VerticalScroll):
    """消息流容器，负责节流刷新与自动滚动。"""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._assistant: AssistantMessage | None = None
        self._tool_widgets: dict[str, ToolActivity] = {}

    def on_mount(self) -> None:
        # 每 80ms 才重排一次 Markdown，避免逐字重排导致卡顿
        self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        if self._assistant is None:
            return
        at_end = self.is_vertical_scroll_end
        self._assistant.flush()
        if at_end:
            self.call_after_refresh(self.scroll_end, animate=False)

    def _stick_to_bottom(self) -> None:
        self.call_after_refresh(self.scroll_end, animate=False)

    def add_welcome(self, *, model: str, workspace: str, tool_count: int) -> None:
        self.mount(WelcomeMessage(model=model, workspace=workspace, tool_count=tool_count))

    def add_user(self, prompt: str) -> None:
        at_end = self.is_vertical_scroll_end
        self.mount(UserMessage(prompt))
        if at_end:
            self._stick_to_bottom()

    def add_assistant_text(self, text: str) -> None:
        if self._assistant is None:
            at_end = self.is_vertical_scroll_end
            self._assistant = AssistantMessage()
            self.mount(self._assistant)
            if at_end:
                self._stick_to_bottom()
        self._assistant.append(text)

    def finish_assistant(self) -> None:
        if self._assistant is not None:
            self._assistant.finish()
            self._assistant = None

    def add_tool_started(self, call: ToolCall) -> None:
        at_end = self.is_vertical_scroll_end
        widget = ToolActivity(call)
        self._tool_widgets[call.id] = widget
        self.mount(widget)
        if at_end:
            self._stick_to_bottom()

    def add_tool_finished(self, call: ToolCall, outcome: ToolOutcome, elapsed_s: float) -> None:
        widget = self._tool_widgets.pop(call.id, None)
        if widget is None:
            self.add_notice(f"工具 {call.name} 已结束，但未找到对应卡片", "warning")
            return
        widget.finish(outcome, elapsed_s)

    def add_notice(self, message: str, level: NoticeLevel = "info") -> None:
        at_end = self.is_vertical_scroll_end
        self.mount(NoticeMessage(message, level))
        if at_end:
            self._stick_to_bottom()

    def clear_messages(self) -> None:
        self._assistant = None
        self._tool_widgets.clear()
        self.remove_children()


# 说明：本模块只依赖 Textual 与 core 的只读数据，不访问网络或文件系统。

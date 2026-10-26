"""底部输入框与等待动画。

- :class:`PromptInput` 基于 ``TextArea``：支持多行输入 / 粘贴；
  ``Enter`` 发送，``Shift+Enter``（以及 ``Ctrl+J`` / ``Alt+Enter``）换行。
- :class:`Spinner` 是输入框左侧的状态指示：空闲时是一个提示符，
  等待模型回复时变成旋转动画。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual import events
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Static, TextArea


class PromptInput(TextArea):
    """多行输入框：Enter 发送，Shift+Enter 换行。"""

    MIN_LINES = 1
    MAX_LINES = 10
    NEWLINE_KEYS = frozenset({"shift+enter", "ctrl+j", "alt+enter"})

    @dataclass
    class Submitted(Message):
        """用户按 Enter 请求发送当前内容。"""

        input: PromptInput
        value: str

        @property
        def control(self) -> PromptInput:
            return self.input

    def __init__(self, placeholder: str = "", **kwargs: Any) -> None:
        kwargs.setdefault("compact", True)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("tab_behavior", "focus")
        kwargs.setdefault("id", "prompt")
        super().__init__(placeholder=placeholder, **kwargs)
        self._height_lines = self.MIN_LINES

    # ---- 兼容 Input 的常用接口（测试 / 外部调用） ---------------------
    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.load_text(text)

    def clear(self) -> None:
        self.load_text("")

    # ---- 键盘 ---------------------------------------------------------
    def _on_key(self, event: events.Key) -> None:
        """Enter 发送；换行组合键插入换行；其余按键交给 TextArea 默认处理。

        这里不调用 ``super()``：Textual 会沿 MRO 调用所有 ``_on_key``，
        再用 ``prevent_default()`` 阻止基类重复插入。
        """
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if event.key in self.NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self._insert_newline()

    def _insert_newline(self) -> None:
        start, end = self.selection
        result = self.replace("\n", start, end)
        self.move_cursor(result.end_location)

    # ---- 自适应高度 ---------------------------------------------------
    def on_mount(self) -> None:
        self.call_after_refresh(self._fit_height)

    def _on_resize(self) -> None:
        self.call_after_refresh(self._fit_height)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self:
            self.call_after_refresh(self._fit_height)

    def _fit_height(self) -> None:
        lines = max(self.MIN_LINES, min(self.wrapped_document.height, self.MAX_LINES))
        if lines != self._height_lines:
            self._height_lines = lines
            self.styles.height = lines


class Spinner(Static):
    """输入框前的等待指示：空闲 ``›``，生成中旋转。"""

    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    IDLE = "›"

    def __init__(self, id: str | None = "spinner") -> None:
        super().__init__(self.IDLE, id=id)
        self._active = False
        self._frame = 0
        self._timer: Timer | None = None

    @property
    def active(self) -> bool:
        return self._active

    def on_mount(self) -> None:
        self.update(self.IDLE)

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self.add_class("active")
        self._timer = self.set_interval(0.1, self._advance)
        self._advance()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._active = False
        self.remove_class("active")
        self.update(self.IDLE)

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(self.FRAMES)
        self.update(self.FRAMES[self._frame])


__all__ = ["PromptInput", "Spinner"]

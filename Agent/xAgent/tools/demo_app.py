#!/usr/bin/env python3
"""自动播放一段演示对话的 TUI（供预览截图使用，不参与发布逻辑）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import make_runtime  # noqa: E402
from xagent.core.application.events import TurnCompleted  # noqa: E402
from xagent.core.domain.events import StreamCompleted, TextDelta, ToolCallDelta  # noqa: E402
from xagent.tui.app import XAgentApp  # noqa: E402
from xagent.tui.widgets import ToolActivity  # noqa: E402

SCRIPTS = [
    [
        TextDelta("我先确认一下工作目录。\n"),
        ToolCallDelta(index=0, id="call_1", name="getCwd"),
        ToolCallDelta(index=0, arguments="{}"),
        StreamCompleted("tool_calls"),
    ],
    [
        TextDelta("当前工作目录就是 `Agent/xAgent`。\n\n"),
        TextDelta("**建议阅读顺序**\n\n"),
        TextDelta("1. `README.md` —— 先跑起来\n"),
        TextDelta("2. `xagent/core/domain/` —— 数据模型\n"),
        TextDelta("3. `xagent/core/application/service.py` —— 工具调用循环\n"),
        TextDelta("4. `xagent/tui/` —— Textual 界面\n\n"),
        TextDelta("> 依赖只能由外向内，`domain` 不 import 任何 I/O。"),
        StreamCompleted("stop"),
    ],
]


class PreviewApp(XAgentApp):
    """挂载后自动提问，完成一轮后展开工具卡片并退出。"""

    def on_mount(self) -> None:
        # Textual 8 会沿 MRO 自动调用基类的 on_mount，子类覆盖时不能再 super()，
        # 否则欢迎消息、定时器都会执行两次。
        self.set_timer(0.8, lambda: self._start_turn("帮我看看工作目录，并给出源码阅读顺序"))

    def _on_agent_event(self, event: object) -> None:
        super()._on_agent_event(event)  # type: ignore[arg-type]
        if isinstance(event, TurnCompleted):
            for child in self.chat.children:
                if isinstance(child, ToolActivity):
                    child.collapsed = False
            self.set_timer(0.5, self._scroll_to_bottom)
            self.set_timer(1.8, self._finish)

    def _scroll_to_bottom(self) -> None:
        self.chat.scroll_end(animate=False)

    def _finish(self) -> None:
        self.chat.scroll_end(animate=False)
        self.exit()


def main() -> None:
    PreviewApp(make_runtime(ROOT, SCRIPTS)).run()


if __name__ == "__main__":
    main()

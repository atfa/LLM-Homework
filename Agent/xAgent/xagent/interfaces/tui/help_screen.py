"""帮助弹窗。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

HELP_MARKDOWN = """
## 使用方式

- 在底部输入框输入问题，`Enter` 发送。
- 模型需要查看/修改文件时会自动调用工具，工具卡片可点击展开。
- `Esc` 中断当前生成；已完成的问答才会写入历史。

## 命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 打开本帮助 |
| `/clear` | 清空界面（保留历史） |
| `/new` | 新建会话（重置历史） |
| `/tools` | 列出已注册工具 |
| `/cwd` | 显示工作目录 |
| `/model` | 显示模型与接口 |
| `/quit` | 退出 |

## 快捷键

| 按键 | 作用 |
| --- | --- |
| `Ctrl+Q` | 退出 |
| `Ctrl+L` | 清屏 |
| `Ctrl+N` | 新会话 |
| `Ctrl+B` | 显示/隐藏侧栏 |
| `F1` | 帮助 |

## 安全边界

所有文件工具都限制在启动时的工作目录（沙箱根）内，
`../` 与指向外部的符号链接会被拒绝。
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_help", "关闭"),
        Binding("f1", "dismiss_help", "关闭", show=False),
        Binding("q", "dismiss_help", "关闭", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Static("xAgent 帮助", classes="help-title")
            yield Markdown(HELP_MARKDOWN, classes="help-body")
            yield Static("Esc / F1 关闭", classes="help-footer")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

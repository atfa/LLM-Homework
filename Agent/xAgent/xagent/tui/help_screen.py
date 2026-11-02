"""帮助弹窗。"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static

HELP_MARKDOWN = """
## 使用方式

- 在底部输入框输入问题，`Enter` 发送，`Shift+Enter` 换行（旧终端可用 `Ctrl+J`）；支持粘贴多行文本。
- 输入框左侧的旋转图标表示正在等待模型回复，`Esc` 可中断。
- 模型需要查看/修改文件时会自动调用工具，工具卡片可点击展开。
- `Esc` 中断当前生成；已完成的问答才会写入历史。
- 输入 `/` 弹出命令面板，↑↓ 选择，Tab 补全，Enter 执行，Esc 关闭。
- 会话完成、新建会话、退出时自动保存；持久化文件含对话和工具结果，请注意隐私。

## 命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 打开本帮助 |
| `/clear` | 清空界面（保留历史） |
| `/new` | 保存当前会话后新建会话 |
| `/save` | 保存完整会话到工作目录 `.agents/sessions/` |
| `/session` | 选择并恢复最近会话（Esc 取消，Enter 确认） |
| `/mcp` | 查看 MCP 服务器连接与工具 |
| `/compact` | 归档原始上下文并生成约 5,000 Tokens 摘要；达到 80% 后须先压缩 |
| `/tools` | 列出已注册工具 |
| `/cwd` | 显示工作目录 |
| `/model` | 显示模型与接口 |
| `/quit` | 退出 |

## 快捷键

| 按键 | 作用 |
| --- | --- |
| `Enter` | 发送 |
| `Shift+Enter` | 换行（多行输入） |
| `Esc` | 中断生成 |
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


class SetupApp(App[None]):
    DEFAULT_CSS = """
    SetupApp Center { height: 1fr; align: center middle; }
    #setup-dialog { width: 76; height: auto; padding: 1 2; border: round $accent; }
    #setup-dialog Static { height: auto; margin-bottom: 1; }
    """
    BINDINGS = [("escape", "quit", "退出")]

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="setup-dialog"):
                yield Static("首次启动：已创建配置文件", markup=False)
                message = f"{self.path}\n\n请将 fake API Key、接口和模型改为真实信息，保存后重新启动。"
                yield Static(message, markup=False)
                yield Button("退出并修改配置", id="close", variant="primary")

    def on_button_pressed(self) -> None:
        self.exit()

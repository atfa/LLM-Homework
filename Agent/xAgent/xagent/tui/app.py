"""Textual TUI：只负责渲染与输入，不含任何 Agent / HTTP 逻辑。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input
from textual.worker import Worker

from ..core.application.events import (
    AgentEvent,
    AssistantTextDelta,
    IterationCompleted,
    IterationStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
)
from ..core.application.runtime import AgentRuntime
from .help_screen import HelpScreen
from .widgets import ChatView, Sidebar, StatusBar

HELP_HINT = " 输入问题后 Enter 发送；/help 查看命令；Esc 中断生成"


class XAgentApp(App[None]):
    """xAgent 的 Textual 界面。"""

    # 绝对路径：即使被其它模块继承（预览/测试），也不会错误地去子类模块找 CSS
    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "xAgent"
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", show=False),
        Binding("ctrl+l", "clear_chat", "清屏"),
        Binding("ctrl+n", "new_session", "新会话"),
        Binding("ctrl+b", "toggle_sidebar", "侧栏"),
        Binding("f1", "help", "帮助"),
        Binding("escape", "cancel_turn", "中断", show=False),
    ]

    def __init__(self, runtime: AgentRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self._busy = False
        self._turn_worker: Worker[None] | None = None
        self.sub_title = f"{runtime.info.model} · {runtime.info.workspace}"

    # ---- 组件访问 -----------------------------------------------------
    @property
    def chat(self) -> ChatView:
        return self.query_one("#chat", ChatView)

    @property
    def sidebar(self) -> Sidebar:
        return self.query_one("#sidebar", Sidebar)

    @property
    def status(self) -> StatusBar:
        return self.query_one("#status", StatusBar)

    @property
    def prompt_input(self) -> Input:
        return self.query_one("#prompt", Input)

    # ---- 生命周期 -----------------------------------------------------
    def compose(self) -> ComposeResult:
        info = self.runtime.info
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield ChatView(id="chat")
            yield Sidebar(info, self.runtime.agent.settings)
        yield Input(placeholder=HELP_HINT, id="prompt")
        yield StatusBar(info.model)
        yield Footer()

    def on_mount(self) -> None:
        info = self.runtime.info
        self.chat.add_welcome(model=info.model, workspace=info.workspace, tool_count=len(info.tools))
        self.status.set_idle()
        self.sidebar.refresh_session(self.runtime.session)
        self.prompt_input.focus()

    # ---- 输入处理 -----------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        if text.startswith("/"):
            self._run_command(text)
            return
        if self._busy:
            self.chat.add_notice("上一轮仍在生成中，按 Esc 中断后再发送。", "warning")
            return
        self._start_turn(text)

    def _run_command(self, line: str) -> None:
        name, _, _rest = line.partition(" ")
        name = name.lower()
        if name in ("/quit", "/exit", "/q"):
            self.exit()
        elif name in ("/help", "/?"):
            self.action_help()
        elif name == "/clear":
            self.action_clear_chat()
        elif name in ("/new", "/reset"):
            self.action_new_session()
        elif name == "/tools":
            self._show_tools()
        elif name == "/cwd":
            self.chat.add_notice(f"工作目录：{self.runtime.info.workspace}")
        elif name == "/model":
            info = self.runtime.info
            self.chat.add_notice(f"模型：{info.model}    接口：{info.base_url}")
        else:
            self.chat.add_notice(f"未知命令 {name}，输入 /help 查看可用命令。", "warning")

    def _show_tools(self) -> None:
        lines = [f"• {definition.name}：{definition.description}" for definition in self.runtime.info.tools]
        self.chat.add_notice(f"已注册 {len(lines)} 个工具：\n" + "\n".join(lines))

    # ---- 一轮对话 -----------------------------------------------------
    def _start_turn(self, prompt: str) -> None:
        self._busy = True
        self.chat.add_user(prompt)
        self.status.set_thinking(1, self.runtime.agent.settings.max_iterations)
        self.sidebar.refresh_session(self.runtime.session, state="生成中")
        self._turn_worker = self.run_worker(
            self._drive_turn(prompt),
            name="turn",
            group="turn",
            exclusive=True,
            exit_on_error=False,
        )

    async def _drive_turn(self, prompt: str) -> None:
        try:
            async for event in self.runtime.agent.run_turn(self.runtime.session, prompt):
                self._on_agent_event(event)
        except asyncio.CancelledError:
            self.chat.finish_assistant()
            self.chat.add_notice("已中断本轮生成（历史未写入）。", "warning")
            self.status.set_cancelled()
            raise
        except Exception as exc:  # pragma: no cover - 防御性兜底
            self.chat.finish_assistant()
            self.chat.add_notice(f"运行时错误：{type(exc).__name__}: {exc}", "error")
            self.status.set_error(str(exc))
        finally:
            self.chat.finish_assistant()
            self._busy = False
            self._turn_worker = None
            self.sidebar.refresh_session(self.runtime.session)
            self.prompt_input.focus()

    def _on_agent_event(self, event: AgentEvent) -> None:
        if isinstance(event, IterationStarted):
            self.status.set_thinking(event.iteration, event.max_iterations)
        elif isinstance(event, AssistantTextDelta):
            self.chat.add_assistant_text(event.text)
        elif isinstance(event, IterationCompleted):
            self.chat.finish_assistant()
        elif isinstance(event, ToolCallStarted):
            self.chat.add_tool_started(event.call)
            self.status.set_tool(event.call.name)
        elif isinstance(event, ToolCallFinished):
            self.chat.add_tool_finished(event.call, event.outcome, event.elapsed_s)
            self.status.set_thinking()
        elif isinstance(event, TurnCompleted):
            self.chat.finish_assistant()
            self.status.set_idle(
                turns=self.runtime.session.turn_count,
                elapsed_s=event.elapsed_s,
                tools=event.tool_calls,
            )
            self.sidebar.refresh_session(self.runtime.session, state="就绪", elapsed_s=event.elapsed_s)
        elif isinstance(event, TurnFailed):
            self.chat.finish_assistant()
            self.chat.add_notice(event.message, "error")
            self.status.set_error(event.message)

    # ---- 动作 ---------------------------------------------------------
    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_clear_chat(self) -> None:
        self.chat.clear_messages()
        self.chat.add_notice("已清空当前界面（会话历史仍保留，Ctrl+N 可重置历史）。")

    def action_new_session(self) -> None:
        if self._busy:
            self.chat.add_notice("正在生成中，先按 Esc 中断再新建会话。", "warning")
            return
        self.runtime.session.reset()
        self.chat.clear_messages()
        info = self.runtime.info
        self.chat.add_welcome(model=info.model, workspace=info.workspace, tool_count=len(info.tools))
        self.chat.add_notice("已开始新会话，历史已重置。", "success")
        self.status.set_idle()
        self.sidebar.refresh_session(self.runtime.session)

    def action_toggle_sidebar(self) -> None:
        self.sidebar.toggle_class("hidden")

    def action_cancel_turn(self) -> None:
        if self._busy and self._turn_worker is not None:
            self._turn_worker.cancel()
        else:
            self.prompt_input.focus()


def run_tui(runtime: AgentRuntime) -> None:
    XAgentApp(runtime).run()

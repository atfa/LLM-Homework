"""Textual TUI：只负责渲染与输入，不含任何 Agent / HTTP 逻辑。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static, TextArea
from textual.worker import Worker

from ..core.application.events import (
    AgentEvent,
    AssistantTextDelta,
    ContextWarning,
    IterationCompleted,
    IterationStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
)
from ..core.application.runtime import AgentRuntime
from .help_screen import HelpScreen
from .session_screen import SessionScreen
from .widgets import ChatView, PromptInput, Sidebar, Spinner, StatusBar
from .widgets.prompt import COMMANDS

HELP_HINT = " Enter 发送 · Shift+Enter 换行 · /help 命令 · Esc 中断生成"


class XAgentApp(App[None]):
    """xAgent 的 Textual 界面。"""

    # 绝对路径：即使被其它模块继承（预览/测试），也不会错误地去子类模块找 CSS
    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "xAgent"
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", show=False, priority=True),
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
        self._completion_index = 0
        self._completion_matches: list[str] = []
        self._mcp_text = "加载中…"
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
    def prompt_input(self) -> PromptInput:
        return self.query_one("#prompt", PromptInput)

    @property
    def spinner(self) -> Spinner:
        return self.query_one("#spinner", Spinner)

    # ---- 生命周期 -----------------------------------------------------
    def compose(self) -> ComposeResult:
        info = self.runtime.info
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield ChatView(id="chat")
            yield Sidebar(info, self.runtime.agent.settings)
        yield Static("", id="command-completions", markup=False)
        with Horizontal(id="prompt-row"):
            yield Spinner()
            yield PromptInput(placeholder=HELP_HINT)
        yield StatusBar(info.model)
        yield Footer()

    def on_mount(self) -> None:
        info = self.runtime.info
        if info.theme in self.available_themes:
            self.theme = info.theme
        self.chat.add_welcome(model=info.model, workspace=info.workspace, tool_count=len(info.tools))
        self.status.set_idle()
        self.sidebar.refresh_session(self.runtime.session)
        self.sidebar.refresh_skills(self.runtime.skills())
        self._refresh_context()
        self.prompt_input.focus()
        self.query_one("#command-completions").display = False
        self.run_worker(self._load_mcp(), name="mcp", exit_on_error=False)

    async def _load_mcp(self) -> None:
        try:
            loader = self.runtime.mcp_loader
            self._mcp_text = await loader() if loader else "（未配置 MCP 服务器）"
        except Exception as exc:
            self._mcp_text = f"MCP 加载失败：{exc}"
        self.sidebar.refresh_mcp(self.runtime.info, self._mcp_text)

    @property
    def session_directory(self) -> Path:
        return Path(self.runtime.info.workspace) / ".agents" / "sessions"

    def _save_session(self) -> bool:
        try:
            self.runtime.session.save(self.session_directory)
            return True
        except (OSError, ValueError) as exc:
            self.chat.add_notice(f"保存会话失败：{exc}", "error")
            return False

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is not self.prompt_input:
            return
        text = self.prompt_input.text
        self._completion_matches = (
            [name for name in COMMANDS if name.startswith(text)]
            if (text.startswith("/") and " " not in text and "\n" not in text)
            else []
        )
        self._completion_index = 0
        self._render_completions()

    def _render_completions(self) -> None:
        panel = self.query_one("#command-completions", Static)
        panel.display = bool(self._completion_matches)
        body = Text("↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭\n", style="dim")
        start = max(0, self._completion_index - 3)
        for index in range(start, min(start + 6, len(self._completion_matches))):
            name = self._completion_matches[index]
            body.append(
                f"{'›' if index == self._completion_index else ' '} {name}  {COMMANDS[name]}\n",
                style="bold cyan" if index == self._completion_index else "",
            )
        panel.update(body)

    def on_prompt_input_completion(self, event: PromptInput.Completion) -> None:
        if event.key == "escape":
            if not self._completion_matches:
                self.action_cancel_turn()
            self._completion_matches = []
        elif self._completion_matches:
            if event.key == "tab":
                name = self._completion_matches[self._completion_index]
                self.prompt_input.value = name + " "
                self.prompt_input.move_cursor(self.prompt_input.document.end)
                self._completion_matches = []
            else:
                delta = 1 if event.key == "down" else -1
                self._completion_index = (self._completion_index + delta) % len(self._completion_matches)
        self._render_completions()

    def _refresh_context(self) -> None:
        settings = self.runtime.agent.settings
        self.status.set_context(
            self.runtime.session.context_tokens,
            settings.context_window,
            default=settings.context_window_default,
        )

    # ---- 输入处理 -----------------------------------------------------
    def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        text = event.value.strip()
        if not text:
            event.input.clear()
            return
        if self._busy:
            self.chat.add_notice("上一轮仍在生成中，按 Esc 中断后再发送（输入内容已保留）。", "warning")
            return
        if text not in COMMANDS and self._completion_matches and " " not in text:
            text = self._completion_matches[self._completion_index]
        event.input.clear()
        self._completion_matches = []
        self._render_completions()
        if text.startswith("/"):
            self._run_command(text)
            return
        if self.runtime.session.needs_compaction:
            event.input.value = text
            self.chat.add_notice("上下文已达到 80%，请先执行 /compact（输入已保留）。", "warning")
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
        elif name == "/save":
            if self._save_session():
                self.chat.add_notice(f"会话已保存：{self.runtime.session.id}", "success")
        elif name in ("/sessions", "/session", "/resume"):
            if _rest.strip():
                self._resume_session(_rest.strip())
            else:
                paths = sorted(self.session_directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                self.push_screen(SessionScreen(paths, self.runtime.session.id), self._session_selected)
        elif name == "/mcp":
            self.chat.add_notice(self._mcp_text)
        elif name == "/compact":
            self._busy = True
            self.spinner.start()
            self.chat.add_notice("正在归档并压缩上下文，期间不会执行工具或继续原任务。")
            self.sidebar.refresh_session(self.runtime.session, state="压缩中")
            self._turn_worker = self.run_worker(
                self._drive_compact(), name="compact", group="turn", exclusive=True, exit_on_error=False
            )
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
        self.spinner.start()
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
            self.chat.add_notice("已中断本轮生成，已保留可恢复的上下文。", "warning")
            self.status.set_cancelled()
            raise
        except Exception as exc:  # pragma: no cover - 防御性兜底
            self.chat.finish_assistant()
            self.chat.add_notice(f"运行时错误：{type(exc).__name__}: {exc}", "error")
            self.status.set_error(str(exc))
        finally:
            self.chat.finish_assistant()
            self._save_session()
            self.spinner.stop()
            self._busy = False
            self._turn_worker = None
            self.sidebar.refresh_session(self.runtime.session)
            self._refresh_context()
            self.sidebar.refresh_skills(self.runtime.skills())
            self.prompt_input.focus()

    async def _drive_compact(self) -> None:
        try:
            note = await self.runtime.agent.compact(self.runtime.session)
            self._save_session()
            self.chat.add_notice(note, "success")
            self.status.set_idle()
        except asyncio.CancelledError:
            self.chat.add_notice("压缩已取消，原上下文保留，可重新 /compact。", "warning")
            self.status.set_cancelled()
            raise
        except Exception as exc:
            self.chat.add_notice(f"压缩失败：{exc}；原上下文保留，可重新 /compact。", "error")
            self.status.set_error(str(exc))
        finally:
            self.spinner.stop()
            self._busy = False
            self._turn_worker = None
            self.sidebar.refresh_session(self.runtime.session)
            self._refresh_context()
            self.prompt_input.focus()

    def _on_agent_event(self, event: AgentEvent) -> None:
        self._refresh_context()
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
        elif isinstance(event, ContextWarning):
            self.chat.add_notice(f"{event.message}（{event.tokens:,}/{event.window:,} Tokens）", "warning")
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
        if not self._save_session():
            return
        self.runtime.session.reset()
        self.chat.clear_messages()
        info = self.runtime.info
        self.chat.add_welcome(model=info.model, workspace=info.workspace, tool_count=len(info.tools))
        self.chat.add_notice("已开始新会话，历史已重置。", "success")
        self._refresh_context()
        self.status.set_idle()
        self.sidebar.refresh_session(self.runtime.session)

    def _session_selected(self, session_id: str | None) -> None:
        if session_id and session_id != self.runtime.session.id:
            self._resume_session(session_id)
        self.prompt_input.focus()

    def _resume_session(self, session_id: str) -> None:
        if not self._save_session():
            return
        try:
            self.runtime.session.restore(self.session_directory, session_id)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.chat.add_notice(f"恢复失败：{exc}", "error")
            return
        self.chat.clear_messages()
        for message in self.runtime.session.conversation.history():
            if message.role.value == "user":
                self.chat.add_user(message.content)
            elif message.role.value == "assistant" and message.content:
                self.chat.add_assistant_text(message.content)
                self.chat.finish_assistant()
        self.chat.add_notice(f"已恢复会话：{self.runtime.session.id}", "success")
        self._refresh_context()
        self.sidebar.refresh_session(self.runtime.session)
        self.status.set_idle(turns=self.runtime.session.turn_count)

    def on_unmount(self) -> None:
        self._save_session()

    def action_toggle_sidebar(self) -> None:
        self.sidebar.toggle_class("hidden")

    def action_cancel_turn(self) -> None:
        if self._busy and self._turn_worker is not None:
            self._turn_worker.cancel()
        else:
            self.prompt_input.focus()


def run_tui(runtime: AgentRuntime) -> None:
    XAgentApp(runtime).run()

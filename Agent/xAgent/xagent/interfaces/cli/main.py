"""CLI 适配器：同一套 core 的无界面参考实现。

它证明 TUI 只是 ``AgentService`` 的一个消费者：
换成命令行、WebSocket 或桌面程序时，core 与事件流完全不用改。
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.markup import escape

from ...application.events import (
    AgentEvent,
    AssistantTextDelta,
    IterationCompleted,
    ToolCallFinished,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
)
from ...application.runtime import AgentRuntime
from ..formatting import first_lines, summarize_arguments

console = Console()


def _preview(text: str, limit: int = 3) -> str:
    return escape(first_lines(text or "（无输出）", limit))


def print_event(event: AgentEvent) -> None:
    if isinstance(event, AssistantTextDelta):
        console.print(event.text, end="", soft_wrap=True, highlight=False, markup=False)
    elif isinstance(event, IterationCompleted):
        if event.content:
            console.print()
        if event.tool_calls:
            names = "、".join(call.name for call in event.tool_calls)
            console.print(f"[dim]\\[计划] 调用 {names}[/dim]")
    elif isinstance(event, ToolCallStarted):
        name = escape(event.call.name)
        arguments = escape(summarize_arguments(event.call.arguments))
        console.print(f"[cyan]\\[工具] {name}({arguments})[/cyan]")
    elif isinstance(event, ToolCallFinished):
        status = "ok" if event.outcome.ok else "failed"
        color = "green" if event.outcome.ok else "red"
        console.print(
            f"[{color}]\\[结果 {status} {event.elapsed_s * 1000:.0f}ms][/{color}] {_preview(event.outcome.content)}"
        )
    elif isinstance(event, TurnCompleted):
        console.print(
            f"\n[dim]\\[完成] 模型调用 {event.iterations} 次、工具 {event.tool_calls} 次，"
            f"耗时 {event.elapsed_s:.1f}s[/dim]\n"
        )
    elif isinstance(event, TurnFailed):
        console.print(f"[red]\\[错误] {escape(event.message)}[/red]")


async def _run_turn(runtime: AgentRuntime, prompt: str) -> None:
    async for event in runtime.agent.run_turn(runtime.session, prompt):
        print_event(event)


async def _repl(runtime: AgentRuntime, prompt: str | None) -> int:
    info = runtime.info
    if prompt is not None:
        if not prompt.strip():
            console.print("[red]--prompt 不能为空[/red]")
            return 2
        await _run_turn(runtime, prompt)
        return 0

    console.print(f"[bold]xAgent[/bold] {info.version} · {escape(info.model)}")
    console.print(f"[dim]工作目录：{escape(info.workspace)} · 工具 {len(info.tools)} 个[/dim]")
    console.print("[dim]输入 /exit 退出，/tools 查看工具[/dim]\n")

    while True:
        try:
            line = (await asyncio.to_thread(input, "你 > ")).strip()
        except EOFError:
            console.print()
            return 0
        except KeyboardInterrupt:
            console.print()
            return 130

        if not line:
            continue
        if line in {"/exit", "/quit", "/q"}:
            return 0
        if line == "/tools":
            for definition in info.tools:
                console.print(f"  • [bold]{definition.name}[/bold]：{escape(definition.description)}")
            console.print()
            continue
        try:
            await _run_turn(runtime, line)
        except KeyboardInterrupt:
            console.print("\n[yellow]已中断本轮生成[/yellow]")
            continue


def run_cli(runtime: AgentRuntime, prompt: str | None = None) -> int:
    """运行 CLI。``prompt`` 非空时只执行一轮（适合脚本/CI 冒烟）。"""
    try:
        return asyncio.run(_repl(runtime, prompt))
    except KeyboardInterrupt:
        console.print()
        return 130


def main(runtime: AgentRuntime, prompt: str | None = None) -> int:
    """兼容直接调用；返回进程退出码。"""
    return run_cli(runtime, prompt)


if __name__ == "__main__":  # pragma: no cover
    console.print("[red]请通过项目根目录 main.py 或 python -m xagent 启动。[/red]")
    sys.exit(2)

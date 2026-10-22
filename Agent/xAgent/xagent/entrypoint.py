"""命令行入口：解析参数并启动 TUI。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .core.composition import build_runtime
from .core.domain.errors import AgentError
from .core.infrastructure.tools.catalog import build_tool_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xagent",
        description="xAgent：分层可扩展的本地工具调用 Agent（Textual TUI）",
    )
    parser.add_argument("-c", "--config", help="config.ini 路径（默认查找当前目录与项目根目录）")
    parser.add_argument("-w", "--workspace", help="沙箱工作目录（默认当前目录）")
    parser.add_argument("--list-tools", action="store_true", help="列出工具后退出（不需要 API Key）")
    parser.add_argument("--version", action="version", version=f"xAgent {__version__}")
    return parser


def _list_tools(workspace: str | None) -> int:
    try:
        registry = build_tool_registry(workspace or Path.cwd())
    except AgentError as exc:
        print(f"无法加载工具：{exc}", file=sys.stderr)
        return 2
    print(f"共 {len(registry)} 个工具：")
    for definition in registry.definitions():
        print(f"  {definition.name:<12} {definition.description}")
    print("提示：webSearch 需要在 config.ini 的 [search] 段配置 baidu_qianfan_api_key 后可用。")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_tools:
        return _list_tools(args.workspace)

    try:
        runtime = build_runtime(config_path=args.config, workspace=args.workspace)
    except AgentError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    try:
        from .tui.app import run_tui

        run_tui(runtime)
        return 0
    finally:
        try:
            asyncio.run(runtime.aclose())
        except RuntimeError:  # pragma: no cover - 事件循环已关闭
            pass


__all__ = ["build_parser", "main"]

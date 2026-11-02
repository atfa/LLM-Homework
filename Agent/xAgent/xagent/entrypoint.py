"""命令行入口：解析参数并启动 TUI。"""

from __future__ import annotations

import argparse
import asyncio
import os
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

    # 显式配置路径（含环境变量）保持优先；默认仅使用当前目录。
    config_path = Path(args.config or os.environ.get("XAGENT_CONFIG") or Path.cwd() / "config.ini")
    if not config_path.exists():
        from .core.infrastructure.config import DEFAULT_CONFIG

        try:
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
        except OSError as exc:
            print(f"无法创建配置文件：{exc}", file=sys.stderr)
            return 2
        from .tui.help_screen import SetupApp

        SetupApp(config_path.resolve()).run()
        return 0

    try:
        runtime = build_runtime(config_path=config_path, workspace=args.workspace)
    except AgentError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    try:
        try:
            from .tui.app import run_tui
        except ModuleNotFoundError as exc:
            if exc.name != "textual":
                raise
            print(
                "启动失败：当前 Python 解释器没有安装 textual。\n"
                f"  解释器：{sys.executable}\n"
                f"  请改用项目虚拟环境：.venv/bin/python main.py\n"
                f"  或安装依赖：{Path(sys.executable).name} -m pip install -e .",
                file=sys.stderr,
            )
            return 2

        run_tui(runtime)
        return 0
    finally:
        try:
            asyncio.run(runtime.aclose())
        except RuntimeError:  # pragma: no cover - 事件循环已关闭
            pass


__all__ = ["build_parser", "main"]

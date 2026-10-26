"""Textual TUI 适配器。"""

from . import compat  # noqa: F401  # 必须在加载 app / 输入组件前应用输入兼容补丁
from .app import XAgentApp, run_tui

__all__ = ["XAgentApp", "run_tui"]

"""工具目录：装配 12 个默认工具。"""

from __future__ import annotations

from pathlib import Path

from .datetime_tools import datetime_tools
from .file_tools import file_tools
from .registry import ToolRegistryImpl
from .sandbox import Sandbox


def build_tool_registry(workspace: str | Path) -> ToolRegistryImpl:
    """构建绑定到指定工作目录的默认工具集（时间 2 个 + 文件 10 个）。"""
    sandbox = Sandbox(workspace)
    registry = ToolRegistryImpl()
    for definition, handler in (*datetime_tools(), *file_tools(sandbox)):
        registry.register(definition, handler)
    return registry


__all__ = ["ToolRegistryImpl", "build_tool_registry"]

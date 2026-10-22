"""工具目录：装配 13 个默认工具（时间 2 + 文件 10 + 联网搜索 1）。"""

from __future__ import annotations

from pathlib import Path

from ..config import SearchSettings
from .date_time import datetime_tools
from .file_ops import Sandbox, file_tools
from .registry import ToolRegistryImpl
from .web_search import QianfanSearchClient, web_search_tools


def build_tool_registry(
    workspace: str | Path,
    *,
    search: SearchSettings | None = None,
) -> ToolRegistryImpl:
    """构建绑定到指定工作目录的默认工具集（时间 2 个 + 文件 10 个 + 联网搜索 1 个）。

    ``search`` 为空表示没有配置百度千帆搜索；此时 ``webSearch`` 仍会注册，
    但调用时会返回「未配置 API Key」的可读提示。
    """
    sandbox = Sandbox(workspace)
    settings = search or SearchSettings()
    client = QianfanSearchClient(
        api_key=settings.api_key,
        endpoint=settings.endpoint,
        search_source=settings.search_source,
        top_k=settings.top_k,
        timeout_s=settings.timeout_s,
        recency=settings.recency,
        model=settings.model,
    )
    registry = ToolRegistryImpl()
    for definition, handler in (*datetime_tools(), *file_tools(sandbox), *web_search_tools(client)):
        registry.register(definition, handler)
    return registry


__all__ = ["ToolRegistryImpl", "build_tool_registry"]

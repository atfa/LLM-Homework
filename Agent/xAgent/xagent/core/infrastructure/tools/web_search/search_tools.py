"""联网搜索工具：把百度千帆 AI 搜索包装成一个 function call。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ....domain.errors import ToolError
from ....domain.tools import ToolDefinition
from ....ports.tools import ToolHandler
from .qianfan_client import MAX_TOP_K, RECENCY_VALUES, QianfanSearchClient, SearchResult

WEB_SEARCH = ToolDefinition(
    name="webSearch",
    description="联网搜索全网实时信息（百度千帆 AI 搜索），返回结果的标题、链接、发布日期与摘要；"
    "适合查询最新资讯、时效性事实与官方公告",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或问题，用自然语言描述更精确，例如「2026 年个人所得税新政策」",
            },
            "top_k": {
                "type": "integer",
                "description": f"返回的结果条数，1~{MAX_TOP_K}；不传则使用配置中的默认值",
            },
            "recency": {
                "type": "string",
                "enum": list(RECENCY_VALUES),
                "description": "按网页发布时间过滤：week=最近 7 天、month=最近 30 天、"
                "semiyear=最近 180 天、year=最近 365 天；不传则不过滤",
            },
        },
        "required": ["query"],
    },
)

MAX_SNIPPET_CHARS = 600
"""单条摘要回传模型的最大字符数，避免结果过长挤占上下文。"""


def _optional_int(arguments: Mapping[str, Any], key: str) -> int | None:
    """读取可选整数参数；兼容模型偶尔给出的数字字符串。"""
    raw = arguments.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ToolError(f"{key} 必须是整数，收到 {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    raise ToolError(f"{key} 必须是整数，收到 {raw!r}")


def _optional_str(arguments: Mapping[str, Any], key: str) -> str | None:
    raw = arguments.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ToolError(f"{key} 必须是字符串，收到 {raw!r}")
    return raw.strip()


def format_search_result(result: SearchResult) -> str:
    """把搜索结果渲染成回传模型的纯文本。"""
    lines: list[str] = []

    if result.summary:
        lines.append("【AI 总结】")
        lines.append(result.summary)
        lines.append("")

    if not result.hits:
        lines.append("【搜索结果】没有找到相关内容。")
        return "\n".join(lines)

    lines.append(f"【搜索结果】共 {len(result.hits)} 条：")
    for index, hit in enumerate(result.hits, start=1):
        lines.append("")
        lines.append(f"[{index}] {hit.title or '(无标题)'}")
        if hit.url:
            lines.append(f"    链接：{hit.url}")
        meta = " | ".join(part for part in (hit.site, hit.date) if part)
        if meta:
            lines.append(f"    来源：{meta}")
        snippet = hit.content
        if len(snippet) > MAX_SNIPPET_CHARS:
            snippet = snippet[:MAX_SNIPPET_CHARS] + "…"
        if snippet:
            lines.append(f"    摘要：{snippet}")
    return "\n".join(lines)


def web_search(client: QianfanSearchClient, arguments: Mapping[str, Any]) -> str:
    """执行一次联网搜索并返回可读结果。"""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("query 必须是非空字符串")

    top_k = _optional_int(arguments, "top_k")
    recency = _optional_str(arguments, "recency")

    result = client.search(query.strip(), top_k=top_k, recency=recency)
    return format_search_result(result)


def web_search_tools(client: QianfanSearchClient | None = None) -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    """返回可直接注册的 (定义, 处理器) 列表。

    没有配置 API Key 时也照常注册：调用时给出可读的「未配置」提示，
    比让工具凭空消失更利于排查问题。
    """
    search_client = client or QianfanSearchClient(api_key="")

    def invoke(arguments: Mapping[str, Any]) -> str:
        return web_search(search_client, arguments)

    return ((WEB_SEARCH, invoke),)


__all__ = ["MAX_SNIPPET_CHARS", "WEB_SEARCH", "format_search_result", "web_search", "web_search_tools"]

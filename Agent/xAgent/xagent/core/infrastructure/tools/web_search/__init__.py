"""联网搜索类工具（webSearch）：调用百度千帆 AI 搜索。"""

from .qianfan_client import QianfanSearchClient, SearchHit, SearchResult
from .search_tools import WEB_SEARCH, format_search_result, web_search, web_search_tools

__all__ = [
    "WEB_SEARCH",
    "QianfanSearchClient",
    "SearchHit",
    "SearchResult",
    "format_search_result",
    "web_search",
    "web_search_tools",
]

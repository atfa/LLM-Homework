"""网页正文提取工具（fetchWeb）：通过 curl 获取 HTML，使用 trafilatura 提取主要内容并转换为 Markdown。"""

from .fetch_tools import FETCH_WEB, fetch_web, fetch_web_tools

__all__ = [
    "FETCH_WEB",
    "fetch_web",
    "fetch_web_tools",
]

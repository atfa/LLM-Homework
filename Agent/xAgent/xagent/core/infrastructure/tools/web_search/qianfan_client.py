"""百度千帆 AI 搜索客户端（同步）。

接口文档：https://cloud.baidu.com/doc/qianfan-api/s/Hmbu8m06u
端点：``POST https://qianfan.baidubce.com/v2/ai_search/chat/completions``

两种模式（由是否传 ``model`` 决定）：

- **百度搜索**（不传 model）：只返回 ``references``（网页结果列表）；
- **智能搜索生成**（传 model）：除 references 外，还会返回
  ``choices[].message.content``（模型基于搜索结果写出的总结）。

Agent 场景默认用前者：调用方自己的大模型负责阅读与归纳，
搜索服务只负责「把最新的全网事实捞回来」。

同步实现是刻意的：:class:`ToolRegistryImpl` 通过 ``asyncio.to_thread`` 执行工具，
不会阻塞事件循环。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import requests

from ....domain.errors import ToolError

DEFAULT_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
"""百度千帆 AI 搜索端点。"""

SEARCH_SOURCES = ("baidu_search_v1", "baidu_search_v2")
"""支持的搜索引擎版本；v2 数据更丰富，更适合配合大模型使用。"""

RECENCY_VALUES = ("week", "month", "semiyear", "year")
"""发布时间过滤：最近 7 天 / 30 天 / 180 天 / 365 天。"""

MAX_TOP_K = 20
"""单次搜索最多返回的网页结果条数（v2 上限）。"""


@dataclass(frozen=True, slots=True)
class SearchHit:
    """一条搜索结果。"""

    title: str
    url: str
    date: str
    site: str
    content: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一次搜索的完整结果。"""

    hits: tuple[SearchHit, ...] = ()
    summary: str = ""
    """智能搜索生成模式下模型写出的总结；百度搜索模式下为空。"""

    request_id: str = ""


class QianfanSearchClient:
    """百度千帆 AI 搜索的同步客户端。"""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        search_source: str = "baidu_search_v2",
        top_k: int = 5,
        timeout_s: float = 30.0,
        recency: str = "",
        model: str = "",
    ) -> None:
        self._api_key = api_key.strip()
        self._endpoint = endpoint.strip() or DEFAULT_ENDPOINT
        self._search_source = search_source.strip() or "baidu_search_v2"
        self._top_k = top_k
        self._timeout_s = timeout_s
        self._recency = recency.strip()
        self._model = model.strip()

    @property
    def configured(self) -> bool:
        """是否配置了可用的 API Key。"""
        return bool(self._api_key)

    # ------------------------------------------------------------------
    def _payload(self, query: str, top_k: int, recency: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": query}],
            "search_source": self._search_source,
            "resource_type_filter": [{"type": "web", "top_k": top_k}],
            "stream": False,
        }
        if recency:
            payload["search_recency_filter"] = recency
        if self._model:
            # 传 model = 智能搜索生成模式（返回模型总结）
            payload["model"] = self._model
        return payload

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        recency: str | None = None,
    ) -> SearchResult:
        """执行一次联网搜索，返回结果列表。

        失败一律抛 :class:`ToolError`，由工具注册表归一化成「失败的工具结果」
        回传给模型，而不是中断整轮对话。
        """
        if not self._api_key:
            raise ToolError(
                "未配置百度千帆搜索 API Key：请在 config.ini 的 [search] 段填写 "
                "baidu_qianfan_api_key（或设置环境变量 XAGENT_SEARCH_API_KEY）后重试。"
            )

        text = query.strip()
        if not text:
            raise ToolError("query 不能为空")

        limit = self._top_k if top_k is None else top_k
        if limit < 1 or limit > MAX_TOP_K:
            raise ToolError(f"top_k 必须在 1~{MAX_TOP_K} 之间，收到 {limit}")

        window = self._recency if recency is None else recency.strip()
        if window and window not in RECENCY_VALUES:
            raise ToolError(f"recency 只能是 {'/'.join(RECENCY_VALUES)} 之一，收到 {window!r}")

        try:
            response = requests.post(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=self._payload(text, limit, window),
                timeout=(10.0, self._timeout_s),
            )
        except requests.Timeout as exc:
            raise ToolError(f"百度千帆搜索超时（{self._timeout_s}s）：{exc}") from exc
        except requests.RequestException as exc:
            raise ToolError(f"百度千帆搜索请求失败：{exc}") from exc

        if response.status_code != 200:
            body = (response.text or "").strip()
            raise ToolError(f"百度千帆搜索返回 HTTP {response.status_code}：{body[:300] or '(空响应)'}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ToolError(f"百度千帆搜索返回的不是合法 JSON：{exc}") from exc
        if not isinstance(data, Mapping):
            raise ToolError("百度千帆搜索返回的 JSON 结构异常")

        self._raise_for_error(data)

        return SearchResult(
            hits=self._parse_hits(data.get("references")),
            summary=self._parse_summary(data.get("choices")),
            request_id=str(data.get("request_id") or ""),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _raise_for_error(data: Mapping[str, Any]) -> None:
        """把接口返回的错误统一转成 :class:`ToolError`。"""
        error = data.get("error")
        if isinstance(error, Mapping):
            message = error.get("message") or error.get("code") or "未知错误"
            raise ToolError(f"百度千帆搜索失败：{message}")

        code = data.get("code")
        if code not in (None, "", 0, "0"):
            detail = str(data.get("message") or "未知错误")
            raise ToolError(f"百度千帆搜索失败（code={code}）：{detail}")

        if data.get("is_safe") is False:
            raise ToolError("百度千帆搜索判定该查询存在安全风险，已被拒绝")

    @staticmethod
    def _parse_hits(raw: Any) -> tuple[SearchHit, ...]:
        """解析 references 数组。"""
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return ()

        hits: list[SearchHit] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            hits.append(
                SearchHit(
                    title=str(item.get("title") or "").strip(),
                    url=str(item.get("url") or "").strip(),
                    date=str(item.get("date") or "").strip(),
                    site=str(item.get("website") or item.get("web_anchor") or "").strip(),
                    content=str(item.get("content") or item.get("snippet") or "").strip(),
                )
            )
        return tuple(hits)

    @staticmethod
    def _parse_summary(raw: Any) -> str:
        """解析智能搜索生成模式下的模型总结。"""
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return ""
        for choice in raw:
            if not isinstance(choice, Mapping):
                continue
            message = choice.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return ""


__all__ = [
    "DEFAULT_ENDPOINT",
    "MAX_TOP_K",
    "RECENCY_VALUES",
    "SEARCH_SOURCES",
    "QianfanSearchClient",
    "SearchHit",
    "SearchResult",
]

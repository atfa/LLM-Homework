"""OpenAI 兼容 Chat Completions 的流式客户端。

设计要点：
- 网络 I/O 在后台线程执行，事件循环只消费队列，TUI 不会被阻塞；
- 厂商 SSE 在这里被解析成领域事件，应用层完全不知道 SSE 的存在；
- 取消时通过 ``stop`` 事件 + ``response.close()`` 尽快释放连接。
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import requests

from ...domain.errors import LLMError
from ...domain.events import LLMStreamEvent, StreamCompleted, StreamFailed, TextDelta, ToolCallDelta
from ...domain.messages import ChatMessage, messages_to_wire
from ...domain.tools import ToolDefinition


class ChatCompletionDecoder:
    """把一行 SSE 数据解析成领域事件（纯函数式，可单测）。"""

    def __init__(self) -> None:
        self._done = False
        self._finish_reason: str | None = None

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, raw: bytes | str) -> list[LLMStreamEvent]:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            return []
        payload = line[5:].strip()
        if payload == "[DONE]":
            return self.finish()

        try:
            obj = json.loads(payload)
        except ValueError:
            return []
        if not isinstance(obj, dict):
            return []

        error = obj.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            self._done = True
            return [StreamFailed(f"模型接口返回错误：{message}")]

        choices = obj.get("choices") or []
        if not choices:
            return []
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}

        events: list[LLMStreamEvent] = []
        text = delta.get("content")
        if isinstance(text, str) and text:
            events.append(TextDelta(text))

        for raw_call in delta.get("tool_calls") or []:
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function") or {}
            try:
                index = int(raw_call.get("index", 0) or 0)
            except (TypeError, ValueError):
                index = 0
            events.append(
                ToolCallDelta(
                    index=index,
                    id=raw_call.get("id") or None,
                    name=function.get("name") or None,
                    arguments=function.get("arguments") or "",
                )
            )

        finish_reason = choice.get("finish_reason")
        if finish_reason:
            self._finish_reason = str(finish_reason)
        return events

    def finish(self) -> list[LLMStreamEvent]:
        if self._done:
            return []
        self._done = True
        return [StreamCompleted(finish_reason=self._finish_reason)]


_END = object()


class OpenAICompatibleClient:
    """实现 :class:`xagent.core.ports.llm.LLMClient`。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 90.0,
        temperature: float | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._temperature = temperature

    # ------------------------------------------------------------------
    def _payload(self, messages: Sequence[ChatMessage], tools: Sequence[ToolDefinition]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages_to_wire(messages),
            "tools": [definition.to_wire() for definition in tools],
            "stream": True,
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[LLMStreamEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()
        stop = threading.Event()
        responses: list[requests.Response] = []

        def publish(event: object) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass  # 事件循环已关闭，丢弃

        def worker() -> None:
            decoder = ChatCompletionDecoder()
            try:
                response = requests.post(
                    self._url,
                    headers=self._headers(),
                    json=self._payload(messages, tools),
                    stream=True,
                    timeout=(10.0, self._timeout_s),
                )
                responses.append(response)
                try:
                    if response.status_code != 200:
                        body = response.text[:500]
                        raise LLMError(f"HTTP {response.status_code}：{body}")
                    # SSE 响应头不带 charset，requests 会按 ISO-8859-1 解码中文会乱码
                    response.encoding = "utf-8"
                    for raw in response.iter_lines(decode_unicode=False):
                        if stop.is_set():
                            return
                        for event in decoder.feed(raw):
                            publish(event)
                finally:
                    response.close()
                for event in decoder.finish():
                    publish(event)
            except requests.RequestException as exc:
                publish(StreamFailed(f"网络请求失败：{exc}"))
            except LLMError as exc:
                publish(StreamFailed(str(exc)))
            except Exception as exc:  # pragma: no cover - 防御性兜底
                publish(StreamFailed(f"读取模型响应失败：{type(exc).__name__}: {exc}"))
            finally:
                publish(_END)

        thread = threading.Thread(target=worker, name="xagent-llm-stream", daemon=True)
        thread.start()
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    break
                event = cast(LLMStreamEvent, item)
                yield event
                if isinstance(event, StreamFailed):
                    break
        finally:
            stop.set()
            for response in responses:
                try:
                    response.close()
                except Exception:  # pragma: no cover - 关闭失败无需处理
                    pass

    async def aclose(self) -> None:
        """当前实现每个请求独立建连，没有需要长期持有的资源。"""
        return None


__all__ = ["ChatCompletionDecoder", "OpenAICompatibleClient"]

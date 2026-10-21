"""模型客户端端口。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from ..domain.events import LLMStreamEvent
from ..domain.messages import ChatMessage
from ..domain.tools import ToolDefinition


@runtime_checkable
class LLMClient(Protocol):
    """流式对话客户端。

    实现方负责：
    - 把领域消息 / 工具定义翻译成厂商协议；
    - 把厂商 SSE 解析成 :class:`LLMStreamEvent`；
    - 网络错误转成 ``StreamFailed`` 事件（不向应用层抛裸网络异常）。
    """

    def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[LLMStreamEvent]:
        """返回一个异步生成器（因此实现为 ``async def`` 生成器）。"""
        ...

    async def aclose(self) -> None:
        """释放连接 / 线程资源。默认实现可为空。"""
        ...

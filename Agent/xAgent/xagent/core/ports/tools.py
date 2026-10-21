"""工具端口：应用层通过它列出工具、执行工具。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from ..domain.tools import ToolCall, ToolDefinition, ToolOutcome

# 工具处理器是同步函数（文件 I/O、子进程），由注册表统一放到线程池执行。
ToolHandler = Callable[[Mapping[str, Any]], str]


@runtime_checkable
class ToolRegistry(Protocol):
    """工具注册表。

    ``invoke`` 必须保证「不抛异常」：任何失败都转成 ``ToolOutcome.failed``，
    因为工具报错本身是给模型看的上下文，而不是要中断整轮对话。
    """

    def definitions(self) -> Sequence[ToolDefinition]: ...

    async def invoke(self, call: ToolCall) -> ToolOutcome: ...

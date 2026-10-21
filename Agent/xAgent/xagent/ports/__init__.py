"""端口层：应用层所依赖的抽象协议（依赖倒置的「接口」一侧）。

这里只放 ``typing.Protocol``，不包含任何实现。基础设施层提供实现，
应用层只面向协议编程，因此可以替换成假实现做测试，或替换成
WebSocket / 远程模型网关等其它实现。
"""

from .llm import LLMClient
from .tools import ToolHandler, ToolRegistry

__all__ = ["LLMClient", "ToolHandler", "ToolRegistry"]

"""领域层：纯业务模型与规则。

依赖规则：只允许使用 Python 标准库，禁止导入 ports / application /
infrastructure / interfaces，也禁止任何网络、文件、终端 I/O。
"""

from .accumulator import ToolCallAccumulator
from .conversation import Conversation
from .errors import AgentError, ConfigurationError, LLMError, SandboxViolation, ToolError
from .events import LLMStreamEvent, StreamCompleted, StreamFailed, TextDelta, ToolCallDelta
from .messages import ChatMessage, Role, Turn, messages_to_wire
from .tools import ToolCall, ToolDefinition, ToolOutcome

__all__ = [
    "AgentError",
    "ChatMessage",
    "ConfigurationError",
    "Conversation",
    "LLMError",
    "LLMStreamEvent",
    "Role",
    "SandboxViolation",
    "StreamCompleted",
    "StreamFailed",
    "TextDelta",
    "ToolCall",
    "ToolCallAccumulator",
    "ToolCallDelta",
    "ToolDefinition",
    "ToolError",
    "ToolOutcome",
    "Turn",
    "messages_to_wire",
]

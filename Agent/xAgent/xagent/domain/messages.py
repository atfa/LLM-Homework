"""会话消息模型（与具体模型厂商无关的 OpenAI Chat 形状）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .tools import ToolCall


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条对话消息。

    不变量：
    - ``tool`` 角色必须带 ``tool_call_id``；
    - 只有 ``assistant`` 可以携带 ``tool_calls``。
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            object.__setattr__(self, "role", Role(self.role))
        if not isinstance(self.tool_calls, tuple):
            object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        if self.role is Role.TOOL:
            if not self.tool_call_id:
                raise ValueError("tool 消息必须提供 tool_call_id")
            if self.tool_calls:
                raise ValueError("tool 消息不能携带 tool_calls")
        elif self.tool_call_id is not None:
            raise ValueError("只有 tool 消息可以设置 tool_call_id")
        if self.role is not Role.ASSISTANT and self.tool_calls:
            raise ValueError("只有 assistant 消息可以携带 tool_calls")

    # ---- 构造快捷方法 -------------------------------------------------
    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: tuple[ToolCall, ...] | list[ToolCall] = ()) -> ChatMessage:
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tuple(tool_calls))

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> ChatMessage:
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id)

    # ---- 协议序列化 ---------------------------------------------------
    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [call.to_wire() for call in self.tool_calls]
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass(frozen=True, slots=True)
class Turn:
    """已完成的问答对（只存最终问答，不存工具调用细节）。"""

    user: str
    assistant: str
    tool_calls: int = 0
    iterations: int = 1

    def as_messages(self) -> tuple[ChatMessage, ChatMessage]:
        return ChatMessage.user(self.user), ChatMessage.assistant(self.assistant)


def messages_to_wire(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    return [message.to_wire() for message in messages]

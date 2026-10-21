"""工具相关的领域模型：定义（给模型看）、调用（模型发起）、结果（回传模型）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """一个工具的元数据 + JSON Schema 参数描述。"""

    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("工具名不能为空")
        if "type" not in self.parameters:
            raise ValueError(f"工具 {self.name} 的 parameters 必须是 JSON Schema 对象")

    def to_wire(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 协议要求的形状。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求的一次工具调用。

    ``arguments`` 保留模型下发的原始 JSON 文本（可能为空串），
    由工具层负责解析与校验，避免在领域层做 I/O 相关假设。
    """

    id: str
    name: str
    arguments: str = "{}"

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """工具执行结果。``content`` 是回传给模型的文本，永不抛异常。"""

    ok: bool
    content: str
    error: str | None = None

    @classmethod
    def succeeded(cls, content: str) -> ToolOutcome:
        return cls(ok=True, content=content, error=None)

    @classmethod
    def failed(cls, message: str) -> ToolOutcome:
        return cls(ok=False, content=message, error=message)

    @property
    def summary(self) -> str:
        """单行摘要，供状态栏 / 日志使用。"""
        first_line = self.content.strip().splitlines()[0] if self.content.strip() else ""
        return first_line if len(first_line) <= 100 else first_line[:97] + "..."

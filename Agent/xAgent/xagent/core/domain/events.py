"""模型流式响应事件（provider-agnostic）。

基础设施层负责把厂商 SSE 解析成这些事件；应用层只依赖这些类型，
因此换模型厂商不需要改应用层和 UI。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextDelta:
    """一小段正文增量。"""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """工具调用的一个分块。

    OpenAI 兼容协议中，第一块通常带 ``id`` 和 ``name``，后续块只带
    ``arguments`` 的片段，通过 ``index`` 归并到同一个调用。
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    """流正常结束。"""

    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class StreamFailed:
    """流异常结束（错误会被转成文本，避免跨线程抛异常）。"""

    message: str


LLMStreamEvent = TextDelta | ToolCallDelta | StreamCompleted | StreamFailed

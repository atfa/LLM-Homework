"""应用层对外事件：UI 只需订阅这些事件即可渲染整个对话过程。

与 :mod:`xagent.domain.events` 的区别：
- 领域事件描述「模型流的一个分片」；
- 应用事件描述「一轮 Agent 执行中发生了什么」，是 UI 的稳定契约。

新增 UI（Web / Desktop）时只需消费这组事件，不需要了解 SSE 细节。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.tools import ToolCall, ToolOutcome


@dataclass(frozen=True, slots=True)
class TurnStarted:
    prompt: str


@dataclass(frozen=True, slots=True)
class IterationStarted:
    """第 N 次模型调用开始（一次用户提问可能触发多次模型调用）。"""

    iteration: int
    max_iterations: int


@dataclass(frozen=True, slots=True)
class AssistantTextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class IterationCompleted:
    """一次模型调用结束。``tool_calls`` 非空表示还要继续执行工具。"""

    iteration: int
    content: str
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolCallFinished:
    call: ToolCall
    outcome: ToolOutcome
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    """一轮问答成功结束，此时最终问答已写入会话历史。"""

    text: str
    iterations: int
    tool_calls: int
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class TurnFailed:
    message: str


AgentEvent = (
    TurnStarted
    | IterationStarted
    | AssistantTextDelta
    | IterationCompleted
    | ToolCallStarted
    | ToolCallFinished
    | TurnCompleted
    | TurnFailed
)

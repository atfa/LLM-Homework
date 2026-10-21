"""AgentService：一轮问答的完整编排（模型 ⇄ 工具循环）。

职责边界：
- 只依赖 ``LLMClient`` / ``ToolRegistry`` 两个端口；
- 不关心 UI 如何渲染，只产出 :class:`AgentEvent` 事件流；
- 不关心配置从哪里来，只接收 :class:`AgentSettings`。

这一层是「core」，TUI / CLI / 未来的 Web、Desktop 都复用它。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from ..domain.accumulator import ToolCallAccumulator
from ..domain.errors import LLMError
from ..domain.events import StreamFailed, TextDelta, ToolCallDelta
from ..domain.messages import ChatMessage
from ..domain.tools import ToolCall, ToolOutcome
from ..ports.llm import LLMClient
from ..ports.tools import ToolRegistry
from .events import (
    AgentEvent,
    AssistantTextDelta,
    IterationCompleted,
    IterationStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from .session import ChatSession
from .settings import AgentSettings


class AgentService:
    """无 UI 依赖的 Agent 用例服务。"""

    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: AgentSettings | None = None) -> None:
        self._llm = llm
        self._tools = tools
        self._settings = settings or AgentSettings()

    @property
    def settings(self) -> AgentSettings:
        return self._settings

    async def run_turn(self, session: ChatSession, prompt: str) -> AsyncIterator[AgentEvent]:
        """执行一轮「提问 → 模型 → 工具 → 模型 → … → 最终回答」。

        事件流以 ``TurnCompleted`` 或 ``TurnFailed`` 结束（取消时以
        ``CancelledError`` 结束）。只有最终问答会写入 ``session``。
        """
        prompt = prompt.strip()
        if not prompt:
            yield TurnFailed(message="输入为空")
            return

        started_at = time.perf_counter()
        yield TurnStarted(prompt=prompt)

        # 本轮工作消息：历史 + 本轮提问 + 工具调用中间结果。
        # 只有最终问答会在成功时写回 session.conversation。
        working: list[ChatMessage] = list(session.conversation.messages())
        working.append(ChatMessage.user(prompt))

        tool_call_count = 0

        try:
            for iteration in range(1, self._settings.max_iterations + 1):
                yield IterationStarted(iteration=iteration, max_iterations=self._settings.max_iterations)

                accumulator = ToolCallAccumulator()
                chunks: list[str] = []
                stream_error: str | None = None

                async for event in self._llm.stream_chat(working, self._tools.definitions()):
                    if isinstance(event, TextDelta):
                        if event.text:
                            chunks.append(event.text)
                            yield AssistantTextDelta(text=event.text)
                    elif isinstance(event, ToolCallDelta):
                        accumulator.feed(event)
                    elif isinstance(event, StreamFailed):
                        stream_error = event.message

                if stream_error:
                    raise LLMError(stream_error)

                content = "".join(chunks)
                calls = accumulator.build()
                yield IterationCompleted(iteration=iteration, content=content, tool_calls=calls)

                if not calls:
                    session.conversation.add_turn(prompt, content)
                    yield TurnCompleted(
                        text=content,
                        iterations=iteration,
                        tool_calls=tool_call_count,
                        elapsed_s=time.perf_counter() - started_at,
                    )
                    return

                # 先把「模型要调用工具」这一步写回工作消息
                working.append(ChatMessage.assistant(content, calls))

                for call in calls:
                    tool_call_count += 1
                    yield ToolCallStarted(call=call)
                    tool_started_at = time.perf_counter()
                    outcome = await self._invoke_tool(call)
                    working.append(ChatMessage.tool_result(call.id, self._limit_tool_output(outcome.content)))
                    yield ToolCallFinished(
                        call=call,
                        outcome=outcome,
                        elapsed_s=time.perf_counter() - tool_started_at,
                    )

            yield TurnFailed(
                message=(
                    f"已达到单轮最大模型调用次数（{self._settings.max_iterations}），"
                    "已停止以避免工具循环失控。可以继续追问或调大 max_iterations。"
                )
            )
        except asyncio.CancelledError:
            # 用户中断：不写历史，原样向上传递，让 UI 决定如何提示
            raise
        except LLMError as exc:
            yield TurnFailed(message=str(exc))
        except Exception as exc:  # pragma: no cover - 防御性兜底，避免 UI 卡死
            yield TurnFailed(message=f"未预期错误：{type(exc).__name__}: {exc}")

    async def _invoke_tool(self, call: ToolCall) -> ToolOutcome:
        """工具执行永不向上抛异常：失败信息本身就是给模型的上下文。"""
        try:
            return await self._tools.invoke(call)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 注册表通常已兜底
            return ToolOutcome.failed(f"工具 {call.name} 执行异常：{type(exc).__name__}: {exc}")

    def _limit_tool_output(self, content: str) -> str:
        limit = self._settings.max_tool_output_chars
        if limit <= 0 or len(content) <= limit:
            return content
        omitted = len(content) - limit
        return f"{content[:limit]}\n...（工具结果过长，已截断 {omitted} 个字符）"

    async def aclose(self) -> None:
        close = getattr(self._llm, "aclose", None)
        if close is not None:
            await close()

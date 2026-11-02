"""AgentService：一轮问答的完整编排（模型 ⇄ 工具循环）。

职责边界：
- 只依赖 ``LLMClient`` / ``ToolRegistry`` 两个端口；
- 不关心 UI 如何渲染，只产出 :class:`AgentEvent` 事件流；
- 不关心配置从哪里来，只接收 :class:`AgentSettings`。

这一层是「core」，TUI / CLI / 未来的 Web、Desktop 都复用它。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace

from ..domain.accumulator import ToolCallAccumulator
from ..domain.errors import LLMError
from ..domain.events import StreamCompleted, StreamFailed, TextDelta, TokenUsage, ToolCallDelta
from ..domain.messages import ChatMessage, Role
from ..domain.skills import merge_system_prompt
from ..domain.tools import ToolCall, ToolOutcome
from ..ports.llm import LLMClient
from ..ports.tools import ToolRegistry
from .context import COMPACT_PROMPT, ContextArchive, estimate_context, estimate_text
from .events import (
    AgentEvent,
    AssistantTextDelta,
    ContextWarning,
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

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        settings: AgentSettings | None = None,
        *,
        system_prompt_provider: Callable[[], str] | None = None,
        context_archive: ContextArchive | None = None,
    ) -> None:
        self._archive = context_archive
        self._llm = llm
        self._tools = tools
        self._settings = settings or AgentSettings()
        # 可选：每轮/每次模型调用前重新获取 System Prompt，用于 createSkill 等
        # 运行期会改变可用工具或技能列表的场景。
        self._system_prompt_provider = system_prompt_provider

    @property
    def settings(self) -> AgentSettings:
        return self._settings

    async def run_turn(self, session: ChatSession, prompt: str) -> AsyncIterator[AgentEvent]:
        """执行一轮「提问 → 模型 → 工具 → 模型 → … → 最终回答」。

        事件流以 ``TurnCompleted`` 或 ``TurnFailed`` 结束（取消时以
        ``CancelledError`` 结束）。中断时也保留有效进度，原始记录另存供归档；
        已加载技能的正文由 ``loadSkill`` 记录在会话中，跨轮复用。
        """
        prompt = prompt.strip()
        if not prompt:
            yield TurnFailed(message="输入为空")
            return

        if session.state != "idle":
            yield TurnFailed(message="会话正忙，不能执行其他任务")
            return
        if session.needs_compaction:
            yield TurnFailed(message="上下文已达到 80%，请先执行 /compact")
            return
        session.state = "running"
        started_at = time.perf_counter()

        # 本轮工作消息：历史 + 本轮提问 + 工具调用中间结果。
        # 成功轮次保留工具往返，原始结果另外记录（不受截断影响）。
        working: list[ChatMessage] = list(session.conversation.messages())
        working.append(ChatMessage.user(prompt))
        # 已加载技能在轮次开始时注入一次；轮内新加载的正文已经作为工具结果
        # 出现在 working 里，不再重复写进 System Prompt。
        loaded_skills = session.conversation.loaded_skills.render()

        tool_call_count = 0
        chunks: list[str] = []
        streaming = False
        active_tool_id: str | None = None
        completed = False
        if not session.archive_messages:
            session.archive_messages.extend(session.conversation.messages())
        session.archive_messages.append(ChatMessage.user(prompt))

        try:
            yield TurnStarted(prompt=prompt)
            for iteration in range(1, self._settings.max_iterations + 1):
                working = self._sync_system_prompt(session, working, loaded_skills)
                yield IterationStarted(iteration=iteration, max_iterations=self._settings.max_iterations)

                accumulator = ToolCallAccumulator()
                chunks = []
                streaming = True
                stream_error: str | None = None
                usage: TokenUsage | None = None

                async for event in self._llm.stream_chat(working, self._tools.definitions()):
                    if isinstance(event, TextDelta):
                        if event.text:
                            chunks.append(event.text)
                            yield AssistantTextDelta(text=event.text)
                    elif isinstance(event, ToolCallDelta):
                        accumulator.feed(event)
                    elif isinstance(event, TokenUsage):
                        usage = event
                    elif isinstance(event, StreamFailed):
                        stream_error = event.message

                if stream_error:
                    raise LLMError(stream_error)

                streaming = False
                content = "".join(chunks)
                calls = accumulator.build()
                yield IterationCompleted(iteration=iteration, content=content, tool_calls=calls)

                assistant = ChatMessage.assistant(content, calls)
                session.archive_messages.append(assistant)
                working.append(assistant)
                measured = (
                    usage.prompt_tokens + (usage.completion_tokens or estimate_context([assistant]))
                    if usage
                    else estimate_context(working, self._tools.definitions())
                )
                session.context_tokens = measured
                if measured >= self._settings.context_window * 0.8:
                    session.needs_compaction = True

                if session.needs_compaction and calls:
                    # 补齐工具协议但不执行工具；压缩后不自动恢复旧任务。
                    for call in calls:
                        skipped = ChatMessage.tool_result(call.id, "上下文达到阈值，未执行；请先 /compact")
                        working.append(skipped)
                        session.archive_messages.append(skipped)
                    session.conversation.replace_history([m for m in working if m.role is not Role.SYSTEM])
                    yield ContextWarning(tokens=measured, window=self._settings.context_window)
                    yield TurnFailed(message="上下文已达到 80%，已暂停工具循环，请执行 /compact")
                    return

                if not calls:
                    session.conversation.replace_history([m for m in working if m.role is not Role.SYSTEM])
                    if session.needs_compaction:
                        yield ContextWarning(tokens=measured, window=self._settings.context_window)
                    completed = True
                    yield TurnCompleted(
                        text=content,
                        iterations=iteration,
                        tool_calls=tool_call_count,
                        elapsed_s=time.perf_counter() - started_at,
                    )
                    return

                tool_messages_start = len(working)
                for call in calls:
                    tool_call_count += 1
                    yield ToolCallStarted(call=call)
                    tool_started_at = time.perf_counter()
                    active_tool_id = call.id
                    outcome = await self._invoke_tool(call)
                    working.append(ChatMessage.tool_result(call.id, self._limit_tool_output(outcome.content)))
                    session.archive_messages.append(ChatMessage.tool_result(call.id, outcome.content))
                    active_tool_id = None
                    yield ToolCallFinished(
                        call=call,
                        outcome=outcome,
                        elapsed_s=time.perf_counter() - tool_started_at,
                    )

                # 工具结果可能很大，在下一次请求之前再补计新增消息。
                session.context_tokens = measured + estimate_context(working[tool_messages_start:])
                if session.context_tokens >= self._settings.context_window * 0.8:
                    session.needs_compaction = True
                    session.conversation.replace_history([m for m in working if m.role is not Role.SYSTEM])
                    yield ContextWarning(tokens=session.context_tokens, window=self._settings.context_window)
                    yield TurnFailed(message="工具结果使上下文达到 80%，请执行 /compact 后继续")
                    return

            yield TurnFailed(
                message=(
                    f"已达到单轮最大模型调用次数（{self._settings.max_iterations}），"
                    "已停止以避免工具循环失控。可以继续追问或调大 max_iterations。"
                )
            )
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            yield TurnFailed(message=str(exc))
        except Exception as exc:  # pragma: no cover - 防御性兜底，避免 UI 卡死
            yield TurnFailed(message=f"未预期错误：{type(exc).__name__}: {exc}")
        finally:
            if streaming and chunks:
                partial = ChatMessage.assistant("".join(chunks) + "\n[回复因错误或中断而未完成]")
                working.append(partial)
                session.archive_messages.append(partial)
            # 可能在工具调用开始前、执行中或两个工具之间中断。
            for index in range(len(working) - 1, -1, -1):
                message = working[index]
                if message.role is Role.ASSISTANT and message.tool_calls:
                    answered = {m.tool_call_id for m in working[index + 1 :] if m.role is Role.TOOL}
                    for call in message.tool_calls:
                        if call.id not in answered:
                            text = (
                                "工具执行中断，结果未知，可能已有副作用；继续前请检查实际状态。"
                                if call.id == active_tool_id
                                else "工具未执行：本轮已中断。"
                            )
                            result = ChatMessage.tool_result(call.id, text)
                            working.append(result)
                            session.archive_messages.append(result)
                    break
            session.conversation.replace_history([m for m in working if m.role is not Role.SYSTEM])
            if not completed:
                session.context_tokens = estimate_context(working, self._tools.definitions())
                session.needs_compaction = (
                    session.needs_compaction or session.context_tokens >= self._settings.context_window * 0.8
                )
            session.state = "idle"

    async def compact(self, session: ChatSession) -> str:
        """归档 → 无工具摘要 → 原子替换。失败/取消保留历史和输入锁。"""
        if session.state != "idle":
            raise RuntimeError("会话正忙，不能压缩")
        if self._archive is None:
            raise RuntimeError("未配置上下文归档目录")
        session.state = "compacting"
        try:
            messages = list(session.archive_messages or session.conversation.messages())
            current_system = session.conversation.render_system_prompt()
            if current_system and (not messages or messages[0].content != current_system):
                messages.insert(0, ChatMessage.system(current_system))
            archive_id, timestamp = await asyncio.to_thread(self._archive.save, messages)
            # 原 System Prompt 也是待总结数据，不作为压缩调用的指令。
            history = "\n".join(json.dumps(message.to_wire(), ensure_ascii=False) for message in messages)
            chunks: list[str] = []
            finished = False
            async for event in self._llm.stream_chat(
                [ChatMessage.system(COMPACT_PROMPT), ChatMessage.user(history)], ()
            ):
                if isinstance(event, TextDelta):
                    chunks.append(event.text)
                elif isinstance(event, (ToolCallDelta, StreamFailed)):
                    raise LLMError(event.message if isinstance(event, StreamFailed) else "压缩响应包含工具调用")
                elif isinstance(event, StreamCompleted):
                    if event.finish_reason not in (None, "stop"):
                        raise LLMError(f"摘要未完整生成：{event.finish_reason}")
                    finished = True
            summary = "".join(chunks).strip()
            if not finished or not summary:
                raise LLMError("压缩未返回完整摘要")
            if estimate_text(summary) > 5500:
                raise LLMError("摘要超过约 5,000 Tokens 的预算，请重新 /compact")
            note = (
                f"此前上下文已于 {timestamp} 完成压缩，原始上下文保存在编号为 {archive_id} "
                "的归档文件中。如需查找历史信息，请使用 recallContext 工具。"
            )
            replacement = ChatMessage.assistant(summary + "\n\n" + note)
            prefix = [ChatMessage.system(current_system)] if current_system.strip() else []
            tokens = estimate_context([*prefix, replacement], self._tools.definitions())
            if tokens >= self._settings.context_window * 0.8:
                raise LLMError("摘要与 System Prompt/工具定义仍超过阈值，请检查模型窗口设置")
            session.conversation.replace_history([replacement])
            session.archive_messages = list(session.conversation.messages())
            session.context_tokens = tokens
            session.needs_compaction = False
            return note
        finally:
            session.state = "idle"

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

    def _sync_system_prompt(
        self,
        session: ChatSession,
        working: list[ChatMessage],
        loaded_skills: str = "",
    ) -> list[ChatMessage]:
        """刷新 System Prompt，使运行期新增的 Skill 在下一轮模型调用中立即可见。

        ``loaded_skills`` 是本轮开始时已加载技能的渲染文本：即使刷新了基础
        Prompt（例如 createSkill 之后），也要保留它，避免已加载正文被冲掉。
        """
        provider = self._system_prompt_provider
        if provider is None:
            return working

        prompt = provider()
        if prompt == session.conversation.system_prompt:
            return working

        session.conversation.system_prompt = prompt
        self._settings = replace(self._settings, system_prompt=prompt)

        effective = merge_system_prompt(prompt, loaded_skills)
        if working and working[0].role is Role.SYSTEM:
            if effective.strip():
                working[0] = ChatMessage.system(effective)
            else:
                del working[0]
        elif effective.strip():
            working.insert(0, ChatMessage.system(effective))
        return working

    async def aclose(self) -> None:
        close_tools = getattr(self._tools, "aclose", None)
        if close_tools is not None:
            await close_tools()
        close = getattr(self._llm, "aclose", None)
        if close is not None:
            await close()

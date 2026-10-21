"""测试替身：可编排的模型客户端与运行时工厂。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from xagent.application.runtime import AgentRuntime, RuntimeInfo
from xagent.application.service import AgentService
from xagent.application.session import ChatSession
from xagent.application.settings import AgentSettings
from xagent.domain.events import LLMStreamEvent, StreamCompleted, TextDelta
from xagent.domain.messages import ChatMessage
from xagent.domain.tools import ToolDefinition
from xagent.infrastructure.tools.catalog import build_tool_registry

EchoScript = Sequence[LLMStreamEvent]


class FakeLLM:
    """按脚本逐次返回事件的假模型。脚本用完后再次调用即报错。"""

    def __init__(self, scripts: Sequence[EchoScript]) -> None:
        self._scripts = [list(script) for script in scripts]
        self.calls: list[list[ChatMessage]] = []
        self.closed = False

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[LLMStreamEvent]:
        self.calls.append(list(messages))
        if not self._scripts:
            raise AssertionError("FakeLLM 脚本已耗尽，但 Agent 又发起了调用")
        for event in self._scripts.pop(0):
            yield event

    async def aclose(self) -> None:
        self.closed = True


class SlowLLM(FakeLLM):
    """先吐一个字，然后挂起，便于测试取消。"""

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[LLMStreamEvent]:
        self.calls.append(list(messages))
        yield TextDelta("思考中")
        await asyncio.sleep(30)
        yield StreamCompleted("stop")


def make_runtime(
    workspace: Path | str,
    scripts: Sequence[EchoScript],
    *,
    max_iterations: int = 8,
    max_tool_output_chars: int = 30_000,
    llm: FakeLLM | None = None,
) -> AgentRuntime:
    registry = build_tool_registry(workspace)
    client = llm or FakeLLM(scripts)
    settings = AgentSettings(
        system_prompt="你是测试助手",
        max_iterations=max_iterations,
        max_tool_output_chars=max_tool_output_chars,
    )
    agent = AgentService(llm=client, tools=registry, settings=settings)
    session = ChatSession.create(settings.system_prompt)
    info = RuntimeInfo(
        app_name="xAgent",
        version="test",
        model="fake-model",
        base_url="http://localhost:1/v1",
        workspace=str(workspace),
        theme="textual-dark",
        tools=tuple(registry.definitions()),
    )
    return AgentRuntime(agent=agent, session=session, info=info)

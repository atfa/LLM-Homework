"""测试替身：可编排的模型客户端与运行时工厂。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from xagent.core.application.runtime import AgentRuntime, RuntimeInfo, SkillInfo
from xagent.core.application.service import AgentService
from xagent.core.application.session import ChatSession
from xagent.core.application.settings import AgentSettings
from xagent.core.domain.events import LLMStreamEvent, StreamCompleted, TextDelta
from xagent.core.domain.messages import ChatMessage
from xagent.core.domain.skills import LoadedSkills
from xagent.core.domain.tools import ToolDefinition
from xagent.core.infrastructure.context import LocalContextArchive
from xagent.core.infrastructure.skills import SkillCatalog, compose_system_prompt
from xagent.core.infrastructure.tools.catalog import build_tool_registry

EchoScript = Sequence[LLMStreamEvent]


class FakeLLM:
    """按脚本逐次返回事件的假模型。脚本用完后再次调用即报错。"""

    def __init__(self, scripts: Sequence[EchoScript]) -> None:
        self._scripts = [list(script) for script in scripts]
        self.calls: list[list[ChatMessage]] = []
        self.closed = False
        self.tool_requests: list[list[ToolDefinition]] = []

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[LLMStreamEvent]:
        self.calls.append(list(messages))
        self.tool_requests.append(list(tools))
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
    max_iterations: int = 20,
    context_window: int = 128_000,
    max_tool_output_chars: int = 30_000,
    llm: FakeLLM | None = None,
    skills: SkillCatalog | None = None,
    system_prompt: str = "你是测试助手",
) -> AgentRuntime:
    catalog = skills if skills is not None else SkillCatalog.discover(workspace)
    loaded_skills = LoadedSkills()
    registry = build_tool_registry(workspace, skills=catalog, loaded_skills=loaded_skills)
    client = llm or FakeLLM(scripts)

    def render_system_prompt() -> str:
        return compose_system_prompt(system_prompt, catalog)

    prompt = render_system_prompt()
    settings = AgentSettings(
        system_prompt=prompt,
        max_iterations=max_iterations,
        context_window=context_window,
        max_tool_output_chars=max_tool_output_chars,
    )
    agent = AgentService(
        llm=client,
        tools=registry,
        settings=settings,
        system_prompt_provider=render_system_prompt,
        context_archive=LocalContextArchive(workspace),
    )
    session = ChatSession.create(prompt, loaded_skills=loaded_skills, project_root=workspace)

    def render_skills() -> tuple[SkillInfo, ...]:
        return tuple(SkillInfo(name=skill.name, description=skill.description) for skill in catalog.skills)

    info = RuntimeInfo(
        app_name="xAgent",
        version="test",
        model="fake-model",
        base_url="http://localhost:1/v1",
        workspace=str(workspace),
        theme="textual-dark",
        tools=tuple(registry.definitions()),
        skills=render_skills(),
    )
    return AgentRuntime(agent=agent, session=session, info=info, skills_provider=render_skills)

"""AGENTS.md：会话首次加载时注入一次，并在后续轮次与恢复后复用。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.fakes import FakeLLM, make_runtime
from xagent.core.application.session import ChatSession
from xagent.core.composition import build_runtime
from xagent.core.domain.events import StreamCompleted, TextDelta
from xagent.core.domain.messages import Role
from xagent.core.infrastructure.config import AppConfig, LLMSettings


def _agents_content() -> str:
    return "# 项目约定\n\n- 始终运行 pytest\n- 使用中文提交信息"


def test_agents_md_injected_once_and_reused_across_turns(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(_agents_content(), encoding="utf-8")
    llm = FakeLLM(
        [
            [TextDelta("第一轮"), StreamCompleted("stop")],
            [TextDelta("第二轮"), StreamCompleted("stop")],
        ]
    )
    runtime = make_runtime(tmp_path, [], llm=llm)

    # 会话创建时已经注入到历史最前面，且不计入用户问答轮次。
    history = runtime.session.conversation.history()
    assert [message.role for message in history] == [Role.USER]
    assert history[0].content == _agents_content()
    assert runtime.session.turn_count == 0

    async def run() -> None:
        events = [event async for event in runtime.agent.run_turn(runtime.session, "问题一")]
        assert events[-1].text == "第一轮"  # type: ignore[union-attr]
        events = [event async for event in runtime.agent.run_turn(runtime.session, "问题二")]
        assert events[-1].text == "第二轮"  # type: ignore[union-attr]

    asyncio.run(run())

    # 两次模型请求都复用同一条历史消息，没有重复注入。
    assert llm.calls[0][1].role is Role.USER
    assert llm.calls[0][1].content == _agents_content()
    assert llm.calls[1][1].content == _agents_content()
    contents = [message.content for message in runtime.session.conversation.history()]
    assert contents.count(_agents_content()) == 1
    assert runtime.session.turn_count == 2


def test_agents_md_persisted_and_not_reinjected_after_restore(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(_agents_content(), encoding="utf-8")
    session = ChatSession.create("system", project_root=tmp_path)
    session.save(tmp_path)
    session_id = session.id

    # 即使源文件在会话期间发生变化，恢复的会话也复用已持久化的那一份。
    (tmp_path / "AGENTS.md").write_text("changed", encoding="utf-8")
    restored = ChatSession.create("system", project_instructions="")
    restored.restore(tmp_path, session_id)

    history = restored.conversation.history()
    assert [message.content for message in history] == [_agents_content()]
    assert restored.project_instructions == _agents_content()
    assert restored.turn_count == 0

    # 新会话（reset）重新注入本项目指令，仍然只有一条。
    restored.reset()
    assert [message.content for message in restored.conversation.history()] == [_agents_content()]


def test_build_runtime_reads_agents_md_from_workspace(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(_agents_content(), encoding="utf-8")
    config = AppConfig(LLMSettings("https://example.com/v1", "test-key", "test"), tmp_path)
    runtime = build_runtime(config, project_root=tmp_path)
    history = runtime.session.conversation.history()
    assert [message.content for message in history] == [_agents_content()]


def test_missing_or_empty_agents_md_injects_nothing(tmp_path: Path) -> None:
    assert ChatSession.create("system", project_root=tmp_path).conversation.history() == ()

    (tmp_path / "AGENTS.md").write_text(" \n\t\n", encoding="utf-8")
    assert ChatSession.create("system", project_root=tmp_path).conversation.history() == ()


def test_agents_md_wins_utf8_bom_and_does_not_count_as_turn(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes("\ufeff规则：只用只读命令\n".encode("utf-8"))
    session = ChatSession.create("system", project_root=tmp_path)
    history = session.conversation.history()
    assert history[0].content == "规则：只用只读命令\n"
    assert session.turn_count == 0

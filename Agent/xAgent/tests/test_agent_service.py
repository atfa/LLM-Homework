"""AgentService 编排测试：工具循环、历史上限、错误与取消。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.fakes import FakeLLM, SlowLLM, make_runtime
from xagent.application.events import (
    AssistantTextDelta,
    IterationCompleted,
    ToolCallFinished,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from xagent.application.service import AgentService
from xagent.domain.events import StreamCompleted, StreamFailed, TextDelta, ToolCallDelta
from xagent.domain.messages import Role
from xagent.domain.tools import ToolCall
from xagent.infrastructure.tools.catalog import build_tool_registry


def tool_call_chunks(index: int, call_id: str, name: str, arguments: str) -> list[object]:
    """模拟 OpenAI 把一次工具调用拆成名字块 + 多个参数块。"""
    return [
        ToolCallDelta(index=index, id=call_id, name=name),
        ToolCallDelta(index=index, arguments=arguments[: len(arguments) // 2]),
        ToolCallDelta(index=index, arguments=arguments[len(arguments) // 2 :]),
    ]


def test_tool_loop_and_history(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
    first = [
        TextDelta("我先看看"),
        *tool_call_chunks(0, "call_1", "listDir", '{"path": "."}'),
        StreamCompleted("tool_calls"),
    ]
    second = [TextDelta("目录里有 hello.txt"), StreamCompleted("stop")]
    llm = FakeLLM([first, second])
    runtime = make_runtime(tmp_path, [], llm=llm)

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "看看目录")]

    events = asyncio.run(run())

    assert isinstance(events[0], TurnStarted)
    assert any(isinstance(event, ToolCallStarted) for event in events)
    tool_finished = [event for event in events if isinstance(event, ToolCallFinished)]
    assert len(tool_finished) == 1
    assert tool_finished[0].outcome.ok
    assert "hello.txt" in tool_finished[0].outcome.content

    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert completed.text == "目录里有 hello.txt"
    assert completed.tool_calls == 1
    assert completed.iterations == 2

    # 历史里只有最终问答，没有工具细节
    history = runtime.session.conversation.history()
    assert [message.role for message in history] == [Role.USER, Role.ASSISTANT]
    assert history[1].content == "目录里有 hello.txt"
    assert history[1].tool_calls == ()

    # 第二次请求确实带上了 assistant.tool_calls 与 tool 结果
    second_call = llm.calls[1]
    assert second_call[-2].role is Role.ASSISTANT
    assert second_call[-2].tool_calls == (ToolCall(id="call_1", name="listDir", arguments='{"path": "."}'),)
    assert second_call[-1].role is Role.TOOL
    assert second_call[-1].tool_call_id == "call_1"
    assert "hello.txt" in second_call[-1].content


def test_max_iterations_guard(tmp_path: Path) -> None:
    endless = [[*tool_call_chunks(0, "call_1", "getCwd", "{}"), StreamCompleted("tool_calls")] for _ in range(5)]
    runtime = make_runtime(tmp_path, endless, max_iterations=2)

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "循环")]

    events = asyncio.run(run())
    assert isinstance(events[-1], TurnFailed)
    assert "最大模型调用次数" in events[-1].message
    assert runtime.session.conversation.history() == ()


def test_stream_failure_becomes_turn_failed(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [[TextDelta("半句"), StreamFailed("HTTP 500")]])

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "hi")]

    events = asyncio.run(run())
    assert isinstance(events[-1], TurnFailed)
    assert "HTTP 500" in events[-1].message
    assert runtime.session.conversation.history() == ()


def test_tool_failure_is_fed_back_to_model(tmp_path: Path) -> None:
    first = [
        *tool_call_chunks(0, "call_1", "readFile", '{"path": "missing.txt"}'),
        StreamCompleted("tool_calls"),
    ]
    second = [TextDelta("文件不存在，需要先创建"), StreamCompleted("stop")]
    llm = FakeLLM([first, second])
    runtime = make_runtime(tmp_path, [], llm=llm)

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "读文件")]

    events = asyncio.run(run())
    failed = [event for event in events if isinstance(event, ToolCallFinished)]
    assert failed and failed[0].outcome.ok is False
    assert isinstance(events[-1], TurnCompleted)
    assert "不存在" in llm.calls[1][-1].content


def test_iteration_events_expose_partial_content(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [[TextDelta("a"), TextDelta("b"), StreamCompleted("stop")]])

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "hi")]

    events = asyncio.run(run())
    deltas = [event.text for event in events if isinstance(event, AssistantTextDelta)]
    iteration_done = next(event for event in events if isinstance(event, IterationCompleted))
    assert deltas == ["a", "b"]
    assert iteration_done.content == "ab"
    assert iteration_done.tool_calls == ()


def test_empty_prompt_is_rejected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [])

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "   ")]

    events = asyncio.run(run())
    assert isinstance(events[0], TurnFailed)


def test_cancellation_propagates_and_keeps_history_clean(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [], llm=SlowLLM([]))

    async def run() -> None:
        stream = runtime.agent.run_turn(runtime.session, "hi")

        async def consume() -> None:
            async for _event in stream:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert runtime.session.conversation.history() == ()


def test_tool_output_is_truncated(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 5000, encoding="utf-8")
    first = [
        *tool_call_chunks(0, "call_1", "readFile", '{"path": "big.txt"}'),
        StreamCompleted("tool_calls"),
    ]
    second = [TextDelta("done"), StreamCompleted("stop")]

    llm = FakeLLM([first, second])
    runtime = make_runtime(tmp_path, [], max_tool_output_chars=100, llm=llm)

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "读")]

    asyncio.run(run())
    tool_message = llm.calls[1][-1]
    assert "已截断" in tool_message.content
    assert len(tool_message.content) < 500


def test_ac_close_delegates(tmp_path: Path) -> None:
    llm = FakeLLM([])
    agent = AgentService(llm=llm, tools=build_tool_registry(tmp_path))
    asyncio.run(agent.aclose())
    assert llm.closed is True

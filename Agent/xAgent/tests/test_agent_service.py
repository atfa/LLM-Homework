"""AgentService 编排测试：工具循环、历史上限、错误与取消。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.fakes import FakeLLM, SlowLLM, make_runtime
from xagent.core.application.events import (
    AssistantTextDelta,
    IterationCompleted,
    ToolCallFinished,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from xagent.core.application.service import AgentService
from xagent.core.domain.events import StreamCompleted, StreamFailed, TextDelta, ToolCallDelta
from xagent.core.domain.messages import Role
from xagent.core.domain.tools import ToolCall
from xagent.core.infrastructure.tools.catalog import build_tool_registry


def tool_call_chunks(index: int, call_id: str, name: str, arguments: str) -> list[ToolCallDelta]:
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

    # 成功轮次的历史保留完整工具往返。
    history = runtime.session.conversation.history()
    assert [message.role for message in history] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert history[-1].content == "目录里有 hello.txt"
    assert history[1].tool_calls[0].name == "listDir"

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
    assert [m.role for m in runtime.session.conversation.history()] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
        Role.TOOL,
    ]


def test_stream_failure_becomes_turn_failed(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [[TextDelta("半句"), StreamFailed("HTTP 500")]])

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "hi")]

    events = asyncio.run(run())
    assert isinstance(events[-1], TurnFailed)
    assert "HTTP 500" in events[-1].message
    history = runtime.session.conversation.history()
    assert [m.role for m in history] == [Role.USER, Role.ASSISTANT]
    assert "半句" in history[-1].content and "未完成" in history[-1].content


def test_continue_after_failure_preserves_tool_progress_and_saved_session(tmp_path: Path) -> None:
    first = [*tool_call_chunks(0, "call_1", "getCwd", "{}"), StreamCompleted("tool_calls")]
    llm = FakeLLM(
        [first, [TextDelta("处理中"), StreamFailed("HTTP 500")], [TextDelta("继续完成"), StreamCompleted("stop")]]
    )
    runtime = make_runtime(tmp_path, [], llm=llm)

    async def run() -> None:
        events = [event async for event in runtime.agent.run_turn(runtime.session, "原始任务")]
        assert isinstance(events[-1], TurnFailed)
        runtime.session.save(tmp_path)
        runtime.session.restore(tmp_path, runtime.session.id)
        events = [event async for event in runtime.agent.run_turn(runtime.session, "continue")]
        assert isinstance(events[-1], TurnCompleted)

    asyncio.run(run())
    request = llm.calls[-1]
    assert [m.role for m in request[-5:]] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT, Role.USER]
    assert request[-5].content == "原始任务"
    assert request[-3].tool_call_id == "call_1"
    assert "处理中" in request[-2].content and "未完成" in request[-2].content


def test_cancel_during_tool_marks_unknown_and_unstarted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = [
        *tool_call_chunks(0, "one", "getCwd", "{}"),
        *tool_call_chunks(1, "two", "getCwd", "{}"),
        StreamCompleted("tool_calls"),
    ]
    llm = FakeLLM([calls, [TextDelta("继续"), StreamCompleted("stop")]])
    runtime = make_runtime(tmp_path, [], llm=llm)
    started = asyncio.Event()

    async def slow_tool(call: ToolCall) -> object:
        started.set()
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    monkeypatch.setattr(runtime.agent, "_invoke_tool", slow_tool)

    async def run() -> None:
        async def consume() -> None:
            async for _ in runtime.agent.run_turn(runtime.session, "执行任务"):
                pass

        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        history = runtime.session.conversation.history()
        assert [m.role for m in history] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.TOOL]
        assert history[2].tool_call_id == "one" and "结果未知" in history[2].content
        assert history[3].tool_call_id == "two" and "未执行" in history[3].content
        assert runtime.session.state == "idle"
        assert isinstance([e async for e in runtime.agent.run_turn(runtime.session, "continue")][-1], TurnCompleted)
        assert llm.calls[-1][-3:-1] == list(history[-2:])

    asyncio.run(run())


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


def test_cancellation_propagates_and_preserves_partial_history(tmp_path: Path) -> None:
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
    history = runtime.session.conversation.history()
    assert [m.role for m in history] == [Role.USER, Role.ASSISTANT]
    assert "思考中" in history[-1].content and "未完成" in history[-1].content


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

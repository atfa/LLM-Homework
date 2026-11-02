"""无需真实 API 的上下文监控、压缩事务与本地检索测试。"""

import asyncio
import json
from pathlib import Path

import pytest

from tests.fakes import FakeLLM, SlowLLM, make_runtime
from tests.test_agent_service import tool_call_chunks
from xagent.core.application.context import estimate_context
from xagent.core.application.events import ContextWarning, ToolCallStarted, TurnFailed
from xagent.core.domain.errors import LLMError
from xagent.core.domain.events import StreamCompleted, StreamFailed, TextDelta, TokenUsage, ToolCallDelta
from xagent.core.domain.messages import ChatMessage, Role
from xagent.core.domain.tools import ToolCall
from xagent.core.infrastructure.context import LocalContextArchive
from xagent.core.infrastructure.llm.openai_compatible import ChatCompletionDecoder, OpenAICompatibleClient


def run_turn(runtime, prompt="继续"):
    async def run():
        return [event async for event in runtime.agent.run_turn(runtime.session, prompt)]

    return asyncio.run(run())


def test_api_usage_threshold_and_lock(tmp_path: Path):
    llm = FakeLLM([[TextDelta("答"), TokenUsage(79_990, 10), StreamCompleted("stop")]])
    runtime = make_runtime(tmp_path, [], llm=llm, context_window=100_000)
    events = run_turn(runtime)
    assert runtime.session.context_tokens == 80_000
    assert runtime.session.needs_compaction
    assert any(isinstance(event, ContextWarning) for event in events)
    assert isinstance(run_turn(runtime)[0], TurnFailed)
    assert len(llm.calls) == 1


def test_fallback_counts_system_history_and_tool_definitions(tmp_path: Path):
    runtime = make_runtime(tmp_path, [[TextDelta("完成"), StreamCompleted("stop")]])
    run_turn(runtime)
    assert runtime.session.context_tokens == estimate_context(
        runtime.session.conversation.messages(), runtime.info.tools
    )


def test_threshold_stops_function_calling(tmp_path: Path):
    runtime = make_runtime(
        tmp_path,
        [[*tool_call_chunks(0, "c1", "getCwd", "{}"), TokenUsage(80_000, 100), StreamCompleted("tool_calls")]],
        context_window=100_000,
    )
    events = run_turn(runtime)
    assert not any(isinstance(event, ToolCallStarted) for event in events)
    assert runtime.session.needs_compaction
    assert runtime.session.conversation.history()[-1].role is Role.TOOL


def test_compact_archives_full_tool_result_and_replaces_history(tmp_path: Path):
    original = "important " + "x" * 10_000 + " 历史关键细节"
    (tmp_path / "big.txt").write_text(original, encoding="utf-8")
    runtime = make_runtime(
        tmp_path,
        [
            [*tool_call_chunks(0, "c1", "readFile", '{"path":"big.txt"}'), StreamCompleted("tool_calls")],
            [TextDelta("done"), StreamCompleted("stop")],
            [TextDelta("# 工作摘要\n已完成读取；接下来验证。"), StreamCompleted("stop")],
            [TextDelta("继续完成"), StreamCompleted("stop")],
        ],
        max_tool_output_chars=100,
    )
    run_turn(runtime)
    prompt = runtime.session.conversation.system_prompt
    runtime.session.needs_compaction = True
    note = asyncio.run(runtime.agent.compact(runtime.session))
    archive = next((tmp_path / ".agents/context").glob("*.jsonl"))
    records = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert [item["role"] for item in records] == ["system", "user", "assistant", "tool", "assistant"]
    assert records[2]["tool_calls"][0]["function"]["arguments"] == '{"path":"big.txt"}'
    assert original in records[3]["content"]
    assert runtime.session.conversation.system_prompt == prompt
    assert len(runtime.session.conversation.history()) == 1
    assert archive.stem in note
    assert "recallContext" in runtime.session.conversation.history()[0].content
    assert not runtime.session.needs_compaction
    assert runtime.session.state == "idle"
    llm = runtime.agent._llm
    assert isinstance(llm, FakeLLM)
    assert llm.tool_requests[2] == []
    run_turn(runtime)
    assert "# 工作摘要" in llm.calls[3][1].content
    results = LocalContextArchive(tmp_path).recall({"text": "历史关键细节", "id": archive.stem})
    assert archive.stem in results and "历史关键细节" in results


@pytest.mark.parametrize(
    "script",
    [
        [StreamFailed("HTTP 500")],
        [StreamCompleted("stop")],
        [TextDelta("不完整"), StreamCompleted("length")],
        [ToolCallDelta(index=0, id="bad", name="getCwd"), StreamCompleted("tool_calls")],
        [TextDelta("中" * 5600), StreamCompleted("stop")],
    ],
)
def test_compact_failure_keeps_history_and_lock(tmp_path: Path, script):
    runtime = make_runtime(tmp_path, [script])
    runtime.session.conversation.add_turn("目标", "进度")
    before = runtime.session.conversation.messages()
    runtime.session.needs_compaction = True
    with pytest.raises(LLMError):
        asyncio.run(runtime.agent.compact(runtime.session))
    assert runtime.session.conversation.messages() == before
    assert runtime.session.needs_compaction
    assert runtime.session.state == "idle"
    assert len(list((tmp_path / ".agents/context").glob("*.jsonl"))) == 1


def test_archive_failure_prevents_llm_call(tmp_path: Path, monkeypatch):
    runtime = make_runtime(tmp_path, [])
    runtime.session.conversation.add_turn("目标", "进度")
    before = runtime.session.conversation.messages()

    def fail(_self, _messages):
        raise OSError("disk full")

    monkeypatch.setattr(LocalContextArchive, "save", fail)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(runtime.agent.compact(runtime.session))
    assert runtime.session.conversation.messages() == before
    assert isinstance(runtime.agent._llm, FakeLLM)
    assert runtime.agent._llm.calls == []


def test_compacting_blocks_turns_and_cancellation_preserves_history(tmp_path: Path):
    llm = SlowLLM([])
    runtime = make_runtime(tmp_path, [], llm=llm)
    runtime.session.conversation.add_turn("目标", "进度")
    before = runtime.session.conversation.messages()
    runtime.session.needs_compaction = True

    async def run():
        task = asyncio.create_task(runtime.agent.compact(runtime.session))
        for _ in range(100):
            if llm.calls:
                break
            await asyncio.sleep(0.01)
        assert runtime.session.state == "compacting"
        events = [event async for event in runtime.agent.run_turn(runtime.session, "不要处理")]
        assert isinstance(events[0], TurnFailed)
        with pytest.raises(RuntimeError):
            await runtime.agent.compact(runtime.session)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert runtime.session.conversation.messages() == before
    assert runtime.session.state == "idle"
    assert runtime.session.needs_compaction


def test_recall_keyword_ranking_bounds_and_validation(tmp_path: Path):
    store = LocalContextArchive(tmp_path)
    first, _ = store.save([ChatMessage.user("旧的 alpha 信息")])
    second, _ = store.save([ChatMessage.assistant("x" * 20_000 + " alpha beta 文件路径") for _ in range(20)])
    assert first != second
    result = json.loads(store.recall({"text": "alpha beta"}))
    assert result["results"][0]["id"] == second
    assert len(result["results"]) == 6
    assert all(len(hit["snippet"]) <= 1100 for hit in result["results"])
    assert "alpha beta" in result["results"][0]["snippet"]
    assert json.loads(store.recall({"id": first, "text": "alpha"}))["results"][0]["line"] == 1
    assert json.loads(store.recall({"text": "不存在"}))["results"] == []
    for arguments in ({"text": ""}, {"text": "a", "id": "../secret"}, {"text": "a", "id": 1}):
        with pytest.raises(ValueError):
            store.recall(arguments)


def test_recall_is_registered_function_tool(tmp_path: Path):
    store = LocalContextArchive(tmp_path)
    archive_id, _ = store.save([ChatMessage.user("关键接口 helloWorld")])
    runtime = make_runtime(tmp_path, [])
    call = ToolCall("c1", "recallContext", json.dumps({"id": archive_id, "text": "helloWorld"}))
    outcome = asyncio.run(runtime.agent._tools.invoke(call))
    assert outcome.ok and "helloWorld" in outcome.content


def test_usage_only_sse_chunk_and_no_tools_payload():
    decoder = ChatCompletionDecoder()
    events = decoder.feed('data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":20}}')
    assert events == [TokenUsage(100, 20)]
    client = OpenAICompatibleClient(base_url="http://localhost", api_key="test", model="test")
    payload = client._payload([ChatMessage.user("摘要")], [])
    assert "tools" not in payload
    assert payload["stream_options"] == {"include_usage": True}

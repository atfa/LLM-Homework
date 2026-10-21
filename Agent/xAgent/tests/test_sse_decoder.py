"""OpenAI 兼容 SSE 解析测试。"""

from __future__ import annotations

import json

from xagent.domain import ToolCallAccumulator
from xagent.domain.events import StreamCompleted, StreamFailed, TextDelta, ToolCallDelta
from xagent.infrastructure.llm.openai_compatible import ChatCompletionDecoder


def data(payload: object) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


def test_text_delta_and_unicode() -> None:
    decoder = ChatCompletionDecoder()
    events = decoder.feed(data(chunk({"content": "你好"})))
    assert events == [TextDelta("你好")]

    # bytes 输入（requests iter_lines 可能返回 bytes）
    assert decoder.feed(data(chunk({"content": "世界"})).encode()) == [TextDelta("世界")]


def test_empty_and_non_data_lines_are_ignored() -> None:
    decoder = ChatCompletionDecoder()
    for line in ["", ": keep-alive", "event: message", "id: 1", "retry: 100", "data: not-json"]:
        assert decoder.feed(line) == []


def test_usage_only_chunk_is_ignored() -> None:
    decoder = ChatCompletionDecoder()
    assert decoder.feed(data({"choices": [], "usage": {"total_tokens": 1}})) == []


def test_tool_call_fragments_are_emitted_and_can_be_accumulated() -> None:
    decoder = ChatCompletionDecoder()
    accumulator = ToolCallAccumulator()

    first = decoder.feed(
        data(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "listDir", "arguments": ""},
                        }
                    ]
                }
            )
        )
    )
    second = decoder.feed(data(chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"path":'}}]})))
    third = decoder.feed(data(chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "."}'}}]})))

    for event in (*first, *second, *third):
        assert isinstance(event, ToolCallDelta)
        accumulator.feed(event)

    (call,) = accumulator.build()
    assert call.id == "call_1"
    assert call.name == "listDir"
    assert call.arguments == '{"path": "."}'


def test_finish_reason_and_done() -> None:
    decoder = ChatCompletionDecoder()
    assert decoder.feed(data(chunk({"content": "hi"}, finish_reason="stop"))) == [TextDelta("hi")]
    assert decoder.feed("data: [DONE]") == [StreamCompleted("stop")]
    # 只能结束一次
    assert decoder.finish() == []


def test_done_without_finish_reason() -> None:
    decoder = ChatCompletionDecoder()
    assert decoder.feed("data: [DONE]") == [StreamCompleted(None)]


def test_stream_end_without_done_marker() -> None:
    decoder = ChatCompletionDecoder()
    decoder.feed(data(chunk({"content": "hi"})))
    assert decoder.finish() == [StreamCompleted(None)]


def test_error_payload_becomes_stream_failed() -> None:
    decoder = ChatCompletionDecoder()
    events = decoder.feed(data({"error": {"message": "quota exceeded"}}))
    assert len(events) == 1
    assert isinstance(events[0], StreamFailed)
    assert "quota exceeded" in events[0].message
    assert decoder.finish() == []


def test_malformed_tool_call_is_tolerated() -> None:
    decoder = ChatCompletionDecoder()
    events = decoder.feed(data(chunk({"tool_calls": [{"function": {"name": "getCwd"}}, "not-a-dict"]})))
    assert events == [ToolCallDelta(index=0, id=None, name="getCwd", arguments="")]

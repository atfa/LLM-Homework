"""领域层纯逻辑测试。"""

from __future__ import annotations

import dataclasses

import pytest

from xagent.core.domain import (
    ChatMessage,
    Conversation,
    Role,
    StreamCompleted,
    TextDelta,
    ToolCall,
    ToolCallAccumulator,
    ToolCallDelta,
    Turn,
)


def test_message_wire_format_for_tool_roundtrip() -> None:
    call = ToolCall(id="call_1", name="listDir", arguments='{"path": "."}')
    assistant = ChatMessage.assistant("我看看", [call])
    tool = ChatMessage.tool_result("call_1", "[DIR] src")

    assert assistant.to_wire() == {
        "role": "assistant",
        "content": "我看看",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "listDir", "arguments": '{"path": "."}'},
            }
        ],
    }
    assert tool.to_wire() == {"role": "tool", "content": "[DIR] src", "tool_call_id": "call_1"}


def test_message_invariants() -> None:
    with pytest.raises(ValueError):
        ChatMessage(role=Role.TOOL, content="x")
    with pytest.raises(ValueError):
        ChatMessage(role=Role.USER, content="x", tool_call_id="call_1")
    with pytest.raises(ValueError):
        ChatMessage(role=Role.USER, content="x", tool_calls=(ToolCall("1", "getCwd"),))


def test_empty_arguments_normalized_to_object() -> None:
    call = ToolCall(id="1", name="getCwd", arguments="")
    assert call.to_wire()["function"]["arguments"] == "{}"


def test_accumulator_merges_fragmented_tool_calls_and_sorts() -> None:
    acc = ToolCallAccumulator()
    acc.feed(ToolCallDelta(index=1, id="b", name="readFile"))
    acc.feed(ToolCallDelta(index=1, arguments='{"path":'))
    acc.feed(ToolCallDelta(index=1, arguments=' "a.txt"}'))
    acc.feed(ToolCallDelta(index=0, id="a", name="getCwd"))
    # 没有名字的残块被丢弃
    acc.feed(ToolCallDelta(index=2, arguments="{}"))

    calls = acc.build()
    assert [call.name for call in calls] == ["getCwd", "readFile"]
    assert calls[0].arguments == "{}"
    assert calls[1].arguments == '{"path": "a.txt"}'
    assert calls[1].id == "b"


def test_accumulator_generates_id_when_provider_omits_it() -> None:
    acc = ToolCallAccumulator()
    acc.feed(ToolCallDelta(index=3, name="getCwd"))
    assert acc.build()[0].id == "call_3"


def test_conversation_keeps_only_final_turns() -> None:
    conversation = Conversation(system_prompt="系统提示")
    conversation.add_turn("你好", "在的")
    conversation.add_turns([Turn(user="看目录", assistant="有 3 个文件", tool_calls=1, iterations=2)])

    messages = conversation.messages()
    assert messages[0].role is Role.SYSTEM
    assert [message.role for message in messages[1:]] == [
        Role.USER,
        Role.ASSISTANT,
        Role.USER,
        Role.ASSISTANT,
    ]
    assert conversation.turn_count == 2
    assert len(conversation) == 4

    conversation.clear()
    assert conversation.messages() == (ChatMessage.system("系统提示"),)


def test_conversation_rejects_system_append() -> None:
    with pytest.raises(ValueError):
        Conversation().append(ChatMessage.system("x"))


def test_domain_events_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        TextDelta("x").text = "y"  # type: ignore[misc]
    assert StreamCompleted("stop").finish_reason == "stop"

"""Textual TUI 无头冒烟测试：渲染、工具卡片、取消、帮助与斜杠命令。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from textual._xterm_parser import XTermParser
from textual.events import Key, Paste
from textual.widget import Widget

from tests.fakes import SlowLLM, make_runtime
from xagent.core.domain.events import StreamCompleted, TextDelta, ToolCallDelta
from xagent.core.domain.tools import ToolCall, ToolOutcome
from xagent.tui.app import XAgentApp
from xagent.tui.help_screen import HelpScreen
from xagent.tui.widgets import AssistantMessage, NoticeMessage, ToolActivity, UserMessage


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("等待 TUI 状态超时")
        await asyncio.sleep(0.02)


def chat_widgets(tui: XAgentApp, kind: type[Widget]) -> list[Widget]:
    """直接遍历 ChatView 子节点，避免在测试里依赖 Textual 查询 API。"""
    return [child for child in tui.chat.children if isinstance(child, kind)]


def test_tui_turn_renders_messages(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        [[TextDelta("你好，"), TextDelta("我是 xAgent"), StreamCompleted("stop")]],
    )

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tui.prompt_input.value = "你好"
            await pilot.press("enter")
            await wait_until(lambda: runtime.session.turn_count == 1)
            tui.chat.finish_assistant()
            await pilot.pause()

            assert len(chat_widgets(tui, UserMessage)) == 1
            assistants = chat_widgets(tui, AssistantMessage)
            assert len(assistants) == 1
            assert isinstance(assistants[0], AssistantMessage)
            assert assistants[0].text == "你好，我是 xAgent"
            assert tui.status.state == "idle"
            assert chat_widgets(tui, NoticeMessage) == []
            assert runtime.session.conversation.history()[-1].content == "你好，我是 xAgent"

    asyncio.run(scenario())


def test_tui_renders_tool_activity(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
    first = [
        ToolCallDelta(index=0, id="call_1", name="listDir"),
        ToolCallDelta(index=0, arguments='{"path": "."}'),
        StreamCompleted("tool_calls"),
    ]
    second = [TextDelta("目录里有 hello.txt"), StreamCompleted("stop")]
    runtime = make_runtime(tmp_path, [first, second])

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tui.prompt_input.value = "看看目录"
            await pilot.press("enter")
            await wait_until(lambda: runtime.session.turn_count == 1)
            await pilot.pause()

            cards = chat_widgets(tui, ToolActivity)
            assert len(cards) == 1
            card = cards[0]
            assert isinstance(card, ToolActivity)
            assert card.has_class("ok")
            assert "listDir" in str(card.title)
            assert not card.has_class("running")

    asyncio.run(scenario())


def test_tui_tool_card_treats_brackets_as_literal_text(tmp_path: Path) -> None:
    """工具参数可能含未闭合方括号，不能交给 Textual 的 markup 解析。"""
    runtime = make_runtime(tmp_path, [])

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            call = ToolCall(id="call_markup", name="listDir", arguments='{"path": "[a=b=120)"}')
            tui.chat.add_tool_started(call)
            await pilot.pause()
            tui.chat.add_tool_finished(call, ToolOutcome.succeeded("结果 [a=b=120) 仍可显示"), 0.1)
            await pilot.pause()

            cards = chat_widgets(tui, ToolActivity)
            assert len(cards) == 1
            card = cards[0]
            assert isinstance(card, ToolActivity)
            title = str(card.title)
            assert "[a=b=120)" in title
            assert "listDir" in title
            assert card.has_class("ok")

    asyncio.run(scenario())


def test_tui_escape_cancels_generation(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [], llm=SlowLLM([]))

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tui.prompt_input.value = "hi"
            await pilot.press("enter")
            await wait_until(lambda: len(chat_widgets(tui, AssistantMessage)) == 1)
            await pilot.press("escape")
            await wait_until(lambda: tui.status.state == "idle")
            await pilot.pause()

            assert runtime.session.turn_count == 0
            notices = [notice for notice in chat_widgets(tui, NoticeMessage) if isinstance(notice, NoticeMessage)]
            assert any("已中断" in notice.message for notice in notices)

    asyncio.run(scenario())


def test_tui_shift_enter_supports_multiline_input(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [[TextDelta("收到"), StreamCompleted("stop")]])

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press(*"第一行")
            await pilot.press("shift+enter")
            await pilot.press(*"第二行")
            await pilot.pause()

            assert tui.prompt_input.value == "第一行\n第二行"
            assert runtime.session.turn_count == 0
            assert tui.prompt_input.styles.height.value == 2

            await pilot.press("enter")
            await wait_until(lambda: runtime.session.turn_count == 1)
            await pilot.pause()
            assert runtime.session.conversation.history()[-2].content == "第一行\n第二行"

    asyncio.run(scenario())


def test_tui_accepts_chinese_input(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [[TextDelta("你好"), StreamCompleted("stop")]])

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("你", "好")
            await pilot.pause()
            assert tui.prompt_input.value == "你好"

            await pilot.press("enter")
            await wait_until(lambda: runtime.session.turn_count == 1)
            await pilot.pause()
            assert runtime.session.conversation.history()[-2].content == "你好"

    asyncio.run(scenario())


def test_long_ime_sequence_is_parsed_as_text() -> None:
    """快速输入中文时，CSI-u 序列可能超过 Textual 默认的 32 字符上限。"""

    sequence = "\x1b[32;;25104:37117:19996:36719:22030:85:78u"
    events = list(XTermParser().feed(sequence))
    text = "".join(event.character or "" for event in events if isinstance(event, Key))
    assert text == "成都东软嘎UN"


def test_tui_paste_keeps_multiple_lines(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [[TextDelta("ok"), StreamCompleted("stop")]])

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tui.post_message(Paste("粘贴第一行\n粘贴第二行"))
            await pilot.pause()

            assert tui.prompt_input.value == "粘贴第一行\n粘贴第二行"
            assert tui.prompt_input.styles.height.value == 2

    asyncio.run(scenario())


def test_tui_spinner_runs_while_waiting(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [], llm=SlowLLM([]))

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert not tui.spinner.active

            tui.prompt_input.value = "hi"
            await pilot.press("enter")
            await wait_until(lambda: tui.spinner.active)
            assert tui.spinner.has_class("active")

            await pilot.press("escape")
            await wait_until(lambda: not tui.spinner.active)
            assert not tui.spinner.has_class("active")
            assert tui.status.state == "idle"

    asyncio.run(scenario())


def test_tui_help_and_slash_commands(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [])

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(tui.screen, HelpScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(tui.screen, HelpScreen)

            tui.prompt_input.value = "/tools"
            await pilot.press("enter")
            await pilot.pause()
            notices = [notice for notice in chat_widgets(tui, NoticeMessage) if isinstance(notice, NoticeMessage)]
            assert notices and "getCwd" in notices[-1].message

    asyncio.run(scenario())


def test_tui_new_session_resets_history(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, [[TextDelta("ok"), StreamCompleted("stop")]])

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tui.prompt_input.value = "hi"
            await pilot.press("enter")
            await wait_until(lambda: runtime.session.turn_count == 1)
            await pilot.pause()

            await pilot.press("ctrl+n")
            await pilot.pause()
            assert runtime.session.turn_count == 0
            assert chat_widgets(tui, UserMessage) == []

    asyncio.run(scenario())

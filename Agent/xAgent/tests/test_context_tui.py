"""压缩命令独立于普通任务；失败可重试、阈值锁不丢输入。"""

import asyncio
from pathlib import Path

from tests.fakes import make_runtime
from tests.test_tui import wait_until
from xagent.core.domain.events import StreamCompleted, StreamFailed, TextDelta
from xagent.tui.app import XAgentApp


def test_compact_command_retry_and_input_lock(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        [
            [StreamFailed("摘要暂不可用")],
            [TextDelta("目标和已完成事项"), StreamCompleted("stop")],
            [TextDelta("继续工作"), StreamCompleted("stop")],
        ],
    )
    runtime.session.conversation.add_turn("原始问题", "原始回答")
    before = runtime.session.conversation.history()
    runtime.session.needs_compaction = True

    async def scenario() -> None:
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tui.prompt_input.value = "不能直接发送"
            await pilot.press("enter")
            await pilot.pause()
            assert tui.prompt_input.value == "不能直接发送"
            assert runtime.session.conversation.history() == before

            tui.prompt_input.value = "/compact"
            await pilot.press("enter")
            await wait_until(lambda: not tui._busy)
            assert runtime.session.needs_compaction
            assert runtime.session.conversation.history() == before

            tui.prompt_input.value = "/compact"
            await pilot.press("enter")
            await wait_until(lambda: not runtime.session.needs_compaction)
            await wait_until(lambda: not tui._busy)
            assert len(runtime.session.conversation.history()) == 1
            assert runtime.session.state == "idle"

            tui.prompt_input.value = "继续"
            await pilot.press("enter")
            await wait_until(lambda: not tui._busy)
            assert runtime.session.conversation.history()[-1].content == "继续工作"

    asyncio.run(scenario())
    assert len(list((tmp_path / ".agents/context").glob("*.jsonl"))) == 2

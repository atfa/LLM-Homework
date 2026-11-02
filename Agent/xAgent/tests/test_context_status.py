"""常驻上下文用量与窗口来源。"""

import asyncio
from pathlib import Path

from tests.fakes import make_runtime
from tests.test_config import INI, write_config
from xagent.core.infrastructure.config import load_config
from xagent.tui.app import XAgentApp


def test_window_source(tmp_path, monkeypatch):
    monkeypatch.delenv("XAGENT_CONTEXT_WINDOW", raising=False)
    path = write_config(tmp_path)
    settings = load_config(path, workspace=tmp_path).llm
    assert settings.context_window == 128_000
    assert settings.context_window_default
    path = write_config(tmp_path, INI.replace("[llm]", "[llm]\ncontext_window = 128000"))
    assert not load_config(path, workspace=tmp_path).llm.context_window_default
    monkeypatch.setenv("XAGENT_CONTEXT_WINDOW", "256000")
    settings = load_config(path, workspace=tmp_path).llm
    assert settings.context_window == 256_000
    assert not settings.context_window_default


def test_context_status_updates_and_reset(tmp_path: Path):
    runtime = make_runtime(tmp_path, [])

    async def scenario():
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "上下文 0k / 128k（默认） Tokens (0.00%)" in str(tui.status.content)
            runtime.session.context_tokens = 12345
            tui._refresh_context()
            assert "12.345k / 128k（默认） Tokens (9.64%)" in str(tui.status.content)
            tui.status.set_context(102399, 128000)
            assert "(79.99%)" in str(tui.status.content)
            tui.status.set_context(102400, 128000)
            assert "(80.00%)" in str(tui.status.content)
            tui.status.set_context(12345, 256000, default=False)
            assert "256k Tokens" in str(tui.status.content)
            assert "默认" not in str(tui.status.content)
            tui.action_toggle_sidebar()
            assert tui.status.display
            tui.action_new_session()
            assert "上下文 0k / 128k" in str(tui.status.content)

    asyncio.run(scenario())

"""首次启动、会话持久化、补全及异步 MCP 回归测试。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Static

from tests.fakes import make_runtime
from xagent.core.application.session import ChatSession
from xagent.core.composition import build_runtime
from xagent.core.domain.errors import ConfigurationError
from xagent.core.domain.messages import ChatMessage
from xagent.core.domain.tools import ToolCall
from xagent.core.infrastructure.config import AppConfig, LLMSettings, load_config
from xagent.core.infrastructure.mcp.client import McpServerStatus, McpToolInfo
from xagent.entrypoint import main
from xagent.tui.app import XAgentApp


def test_first_start_creates_config_without_building_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XAGENT_CONFIG", raising=False)
    with patch("xagent.tui.help_screen.SetupApp.run"), patch("xagent.entrypoint.build_runtime") as build:
        assert main([]) == 0
        build.assert_not_called()
    config = tmp_path / "config.ini"
    text = config.read_text()
    assert "fake-api-key" in text and "[tools]" in text and "[search]" in text
    monkeypatch.delenv("XAGENT_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="占位"):
        load_config(config)
    config.write_text(text.replace("fake-api-key-replace-me", "test-real-key"))
    settings = load_config(config)
    assert settings.tools.cli_timeout_s == 30
    assert settings.tools.fetch_connect_timeout_s == 10


def test_session_roundtrip_including_tool_history(tmp_path):
    session = ChatSession.create("system")
    session.conversation.append(
        ChatMessage.user("hello"),
        ChatMessage.assistant(tool_calls=[ToolCall("c1", "readFile", '{"path":"x"}')]),
        ChatMessage.tool_result("c1", "contents"),
        ChatMessage.assistant("done"),
    )
    session.archive_messages = list(session.conversation.history())
    session.conversation.loaded_skills.add("test", "skill body")
    session.context_tokens = 42
    session.needs_compaction = True
    session.save(tmp_path)
    old_id = session.id
    session.reset()
    session.restore(tmp_path, old_id)
    assert session.id == old_id
    assert session.conversation.history()[1].tool_calls[0].name == "readFile"
    assert session.archive_messages == list(session.conversation.history())
    assert session.conversation.loaded_skills.get("test") == "skill body"
    assert session.context_tokens == 42 and session.needs_compaction
    with pytest.raises(ValueError):
        session.restore(tmp_path, "../../etc/passwd")


def test_bad_session_does_not_replace_current(tmp_path):
    session = ChatSession.create("system")
    session.conversation.add_turn("keep", "me")
    before = session.conversation.history()
    (tmp_path / "12345678.json").write_text(json.dumps({"messages": [{"role": "invalid"}]}))
    with pytest.raises(ValueError):
        session.restore(tmp_path, "12345678")
    assert session.conversation.history() == before


def test_build_runtime_does_not_connect_mcp(tmp_path):
    config = AppConfig(LLMSettings("https://example.com/v1", "test-key", "test"), tmp_path)
    with patch("xagent.core.composition.McpClientManager") as manager:
        runtime = build_runtime(config, project_root=tmp_path)
        manager.assert_not_called()
        assert runtime.mcp_loader is not None
        asyncio.run(runtime.aclose())


def test_async_mcp_registers_tools_and_cleans_up(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"demo": {"url": "http://localhost:9999/mcp", "type": "streamable_http"}}})
    )
    config = AppConfig(LLMSettings("https://example.com/v1", "test-key", "test"), tmp_path)

    async def scenario():
        with patch("xagent.core.composition.McpClientManager") as factory:
            manager = factory.return_value
            manager.connect.return_value = (McpServerStatus("demo", "localhost", True, 1),)
            manager.tools = (McpToolInfo("demo", "remoteSearch", "Search", {"type": "object"}),)
            runtime = build_runtime(config, project_root=tmp_path)
            assert "remoteSearch" not in [tool.name for tool in runtime.info.tools]
            assert runtime.mcp_loader is not None
            text = await runtime.mcp_loader()
            assert "remoteSearch" in text and "已连接" in text
            assert "remoteSearch" in [tool.name for tool in runtime.info.tools]
            await runtime.aclose()
            manager.close.assert_called_once()

    asyncio.run(scenario())


def test_tool_timeout_config(tmp_path):
    config = tmp_path / "config.ini"
    config.write_text(
        "[llm]\nbase_url=https://example.com/v1\napi_key=test\nmodel=test\n"
        "[tools]\ncli_timeout=15\nfetch_timeout=20\nfetch_connect_timeout=3\n"
    )
    settings = load_config(config)
    assert settings.tools.cli_timeout_s == 15
    assert settings.tools.fetch_timeout_s == 20
    assert settings.tools.fetch_connect_timeout_s == 3
    config.write_text(config.read_text().replace("cli_timeout=15", "cli_timeout=0"))
    with pytest.raises(ConfigurationError):
        load_config(config)


def test_completion_resume_and_delayed_mcp(tmp_path: Path):
    runtime = make_runtime(tmp_path, [])

    async def scenario():
        connected = asyncio.Event()

        async def load():
            await connected.wait()
            return "● demo · 已连接\n  • search"

        runtime.mcp_loader = load
        tui = XAgentApp(runtime)
        async with tui.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert tui.prompt_input.has_focus
            assert tui._mcp_text == "加载中…"
            tui.prompt_input.value = "/sess"
            await pilot.pause()
            assert tui.query_one("#command-completions", Static).display
            await pilot.press("tab")
            await pilot.pause()
            assert tui.prompt_input.value == "/session "
            assert not tui.query_one("#command-completions", Static).display
            runtime.session.conversation.add_turn("hello", "world")
            old_id = runtime.session.id
            tui._run_command("/new")
            assert runtime.session.id != old_id
            tui._run_command("/session")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert runtime.session.id != old_id
            tui._run_command("/session")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert runtime.session.id == old_id
            assert runtime.session.conversation.history()[-1].content == "world"
            connected.set()
            await pilot.pause()
            assert "demo" in tui._mcp_text
            assert "search" in str(tui.sidebar._mcp_body.render())

    asyncio.run(scenario())

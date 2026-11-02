"""MCP 配置、工具桥接与结果格式化的单元测试（不依赖真实网络）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from xagent.core.domain.errors import ToolError
from xagent.core.domain.tools import ToolCall
from xagent.core.infrastructure.mcp import (
    McpClientManager,
    McpServerSettings,
    McpToolInfo,
    format_mcp_result,
    load_mcp_config,
)
from xagent.core.infrastructure.mcp.tools import McpToolBridge
from xagent.core.infrastructure.tools.catalog import build_tool_registry

VALID_CONFIG = {
    "mcpServers": {
        "Bazi-MCP": {
            "type": "streamable_http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer token"},
            "timeout": 12,
        }
    }
}


def write_mcp_config(root: Path, payload: object) -> Path:
    path = root / ".mcp.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_mcp_config_reads_streamable_http_server(tmp_path: Path) -> None:
    path = write_mcp_config(tmp_path, VALID_CONFIG)

    result = load_mcp_config(tmp_path)

    assert result.path == path.resolve()
    assert result.errors == ()
    assert len(result.servers) == 1
    server = result.servers[0]
    assert server.name == "Bazi-MCP"
    assert server.url == "https://example.com/mcp"
    assert server.transport == "streamable_http"
    assert dict(server.headers) == {"Authorization": "Bearer token"}
    assert server.timeout_s == 12


def test_load_mcp_config_reads_stdio_server_without_type(tmp_path: Path) -> None:
    write_mcp_config(
        tmp_path,
        {
            "mcpServers": {
                "pet-hospital": {
                    "command": "/opt/petmcp",
                    "args": ["-auto", "-v"],
                }
            }
        },
    )

    result = load_mcp_config(tmp_path)

    assert result.errors == ()
    server = result.servers[0]
    assert server.transport == "stdio"
    assert server.command == "/opt/petmcp"
    assert server.args == ("-auto", "-v")
    assert server.url == ""
    assert server.target == "/opt/petmcp -auto -v"


def test_load_mcp_config_reads_stdio_env_and_cwd(tmp_path: Path) -> None:
    write_mcp_config(
        tmp_path,
        {
            "mcpServers": {
                "pet-hospital": {
                    "type": "stdio",
                    "command": "petmcp",
                    "args": ["-auto"],
                    "env": {"PET_MODE": "test"},
                    "cwd": "/tmp/pet",
                }
            }
        },
    )

    result = load_mcp_config(tmp_path)

    server = result.servers[0]
    assert server.transport == "stdio"
    assert dict(server.env) == {"PET_MODE": "test"}
    assert server.cwd == "/tmp/pet"


def test_load_mcp_config_rejects_stdio_without_command(tmp_path: Path) -> None:
    write_mcp_config(tmp_path, {"mcpServers": {"broken": {"type": "stdio"}}})

    result = load_mcp_config(tmp_path)

    assert result.servers == ()
    assert any("缺少 command" in message for message in result.errors)


def test_load_mcp_config_warns_when_server_is_outside_mcp_servers(tmp_path: Path) -> None:
    write_mcp_config(
        tmp_path,
        {
            "mcpServers": {"Bazi-MCP": {"type": "streamable_http", "url": "https://example.com/mcp"}},
            "pet-hospital": {
                "command": "/opt/petmcp",
                "args": ["-auto", "-v"],
            },
        },
    )

    result = load_mcp_config(tmp_path)

    assert [server.name for server in result.servers] == ["Bazi-MCP"]
    assert any("pet-hospital" in message and "mcpServers" in message for message in result.errors)


def test_load_mcp_config_missing_file_is_not_an_error(tmp_path: Path) -> None:
    result = load_mcp_config(tmp_path)
    assert result.path is None
    assert result.servers == ()
    assert result.errors == ()


def test_load_mcp_config_reports_bad_entries_but_keeps_good_ones(tmp_path: Path) -> None:
    write_mcp_config(
        tmp_path,
        {
            "mcpServers": {
                "good": {"type": "streamable_http", "url": "https://example.com/mcp"},
                "bad-type": {"type": "sse", "url": "https://example.com/mcp"},
                "bad-url": {"url": "ftp://example.com/mcp"},
            }
        },
    )

    result = load_mcp_config(tmp_path)

    assert [server.name for server in result.servers] == ["good"]
    assert len(result.errors) == 2
    assert any("bad-type" in message for message in result.errors)
    assert any("bad-url" in message for message in result.errors)


def test_load_mcp_config_reports_invalid_json(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{ not json", encoding="utf-8")

    result = load_mcp_config(tmp_path)

    assert result.servers == ()
    assert len(result.errors) == 1
    assert "不是合法 JSON" in result.errors[0]


class FakeManager:
    def __init__(self, tools: tuple[McpToolInfo, ...]) -> None:
        self.tools = tools
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def call_tool(self, server: str, tool: str, arguments: dict[str, object]) -> str:
        self.calls.append((server, tool, dict(arguments)))
        return f"{server}/{tool} 的结果"

    def close(self) -> None:
        self.closed = True


def test_bridge_prefixes_only_when_names_collide() -> None:
    manager = FakeManager(
        (
            McpToolInfo("A", "weather", "查天气", {"type": "object", "properties": {}}),
            McpToolInfo("B", "weather", "查天气", {"type": "object", "properties": {}}),
            McpToolInfo("A", "getDate", "日期", {"type": "object", "properties": {}}),
            McpToolInfo("A", "foo.bar", "点号", {"type": "object", "properties": {}}),
        )
    )

    bridge = McpToolBridge(manager, reserved_names={"getDate"})

    names = [binding.exposed_name for binding in bridge.bindings]
    assert names[0] == "weather"
    assert names[1] == "B__weather"
    assert names[2] == "A__getDate"
    assert names[3] == "foo_bar"
    assert all(len(name) <= 64 for name in names)
    assert bridge.get("B__weather").server == "B"  # type: ignore[union-attr]
    assert bridge.get("B__weather").remote_name == "weather"  # type: ignore[union-attr]


def test_bridge_handler_forwards_to_the_mapped_server_and_tool() -> None:
    manager = FakeManager((McpToolInfo("Bazi-MCP", "getBaziDetail", "八字", {"type": "object", "properties": {}}),))
    bridge = McpToolBridge(manager)
    definition, handler = bridge.entries()[0]

    assert handler({"gender": 1}) == "Bazi-MCP/getBaziDetail 的结果"
    assert manager.calls == [("Bazi-MCP", "getBaziDetail", {"gender": 1})]
    assert definition.description.startswith("[MCP:Bazi-MCP]")


def test_registry_exposes_and_invokes_mcp_tool(tmp_path: Path) -> None:
    manager = FakeManager(
        (McpToolInfo("Bazi-MCP", "getChineseCalendar", "黄历", {"type": "object", "properties": {}}),)
    )
    registry = build_tool_registry(tmp_path, mcp=manager)  # type: ignore[arg-type]

    assert "getChineseCalendar" in registry.names()
    outcome = asyncio.run(
        registry.invoke(
            ToolCall(
                id="call-1",
                name="getChineseCalendar",
                arguments='{"solarDatetime": "2026-11-02T12:00:00+08:00"}',
            )
        )
    )

    assert outcome.ok
    assert outcome.content == "Bazi-MCP/getChineseCalendar 的结果"
    assert manager.calls == [("Bazi-MCP", "getChineseCalendar", {"solarDatetime": "2026-11-02T12:00:00+08:00"})]

    asyncio.run(registry.aclose())
    assert manager.closed


def test_format_mcp_result_handles_text_structured_and_errors() -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="普通文本"),
            SimpleNamespace(type="image", mime_type="image/png", data="aGVsbG8="),
        ],
        structured_content={"ok": True},
        is_error=False,
    )

    text, is_error = format_mcp_result(result)

    assert is_error is False
    assert "普通文本" in text
    assert "image/png" in text
    assert '"ok": true' in text

    error_text, is_error = format_mcp_result(
        SimpleNamespace(content=[SimpleNamespace(type="text", text="参数错误")], is_error=True)
    )
    assert is_error is True
    assert error_text == "参数错误"


def test_manager_rejects_unknown_server_without_network() -> None:
    manager = McpClientManager([McpServerSettings(name="demo", url="https://example.com/mcp")])
    with pytest.raises(ToolError, match="未知的 MCP Server"):
        manager.call_tool("missing", "tool", {})

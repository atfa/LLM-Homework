"""MCP 子进程不得通过继承 stderr 覆盖 TUI。"""

import asyncio
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager

from xagent.core.infrastructure.mcp import McpClientManager, McpServerSettings, client


def test_stdio_stderr_is_isolated_and_closed_after_transport(monkeypatch):
    captured = []
    streams = (object(), object())

    @asynccontextmanager
    async def fake_stdio(parameters, *, errlog):
        assert errlog is not sys.stderr
        os.write(errlog.fileno(), b"server startup log\n")
        captured.append(errlog)
        try:
            yield streams
        finally:
            assert not errlog.closed

    monkeypatch.setattr(client, "stdio_client", fake_stdio)
    server = McpServerSettings(name="test", transport="stdio", command="unused")
    manager = McpClientManager([server])

    async def run():
        async with AsyncExitStack() as stack:
            assert await manager._open_stream(server, stack) == streams
        assert captured[0].closed

    asyncio.run(run())

"""MCP Client：使用官方 Python SDK 连接 Streamable HTTP Server。

实现要点：

- 每个 MCP Server 在自己的长生命周期 asyncio 任务中完成
  ``initialize() → list_tools() → 等待关闭``，确保任何 anyio/AsyncExitStack
  的进入与退出都发生在同一个任务里；
- 所有网络请求跑在独立的后台事件循环线程上，因此同步的 ``ToolHandler``
  （以及现有的 ``asyncio.to_thread`` 调用路径）不需要改成 async；
- 单个 Server 连接失败只影响它自己，错误通过 :class:`McpServerStatus`
  返回给上层展示。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import threading
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from mcp import ClientSession  # type: ignore[import-not-found]
from mcp.client.stdio import StdioServerParameters, stdio_client  # type: ignore[import-not-found]

from ...domain.errors import ToolError
from .config import McpServerSettings

try:  # MCP Python SDK 2.x
    from mcp.client.streamable_http import streamable_http_client as _streamable_http
except ImportError:  # pragma: no cover - 兼容 MCP Python SDK 1.x
    from mcp.client.streamable_http import streamablehttp_client as _streamable_http  # type: ignore[no-redef]


@dataclass(frozen=True, slots=True)
class McpToolInfo:
    """从 ``list_tools()`` 得到的一个 MCP 工具。"""

    server: str
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class McpServerStatus:
    """一个 MCP Server 的启动 / 发现结果，供日志与 UI 展示。"""

    name: str
    target: str
    connected: bool
    tool_count: int = 0
    error: str | None = None


def _root_exception(exc: BaseException) -> BaseException:
    """anyio / TaskGroup 会把真正错误包进 ExceptionGroup，取出最内层。"""
    current = exc
    while True:
        children = getattr(current, "exceptions", None)
        if not children:
            return current
        current = children[0]


def _describe_exception(exc: BaseException) -> str:
    root = _root_exception(exc)
    message = str(root).strip() or repr(root)
    return f"{type(root).__name__}: {message}"


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _format_content_item(item: Any) -> str:
    """把 MCP 的 TextContent / ImageContent / EmbeddedResource 等转成可读文本。"""
    item_type = getattr(item, "type", None)

    if item_type == "text":
        return str(getattr(item, "text", ""))

    if item_type == "image":
        mime = getattr(item, "mime_type", None) or getattr(item, "mimeType", None) or "未知类型"
        data = str(getattr(item, "data", "") or "")
        return f"[图片内容：{mime}，base64 长度 {len(data)}。xAgent 暂不以图片形式回传模型]"

    if item_type == "audio":
        mime = getattr(item, "mime_type", None) or getattr(item, "mimeType", None) or "未知类型"
        data = str(getattr(item, "data", "") or "")
        return f"[音频内容：{mime}，base64 长度 {len(data)}。xAgent 暂不以音频形式回传模型]"

    if item_type == "resource":
        resource = getattr(item, "resource", None)
        if resource is None:
            return _json_text(item)
        uri = getattr(resource, "uri", "")
        text = getattr(resource, "text", None)
        if text is not None:
            return f"[资源 {uri}]\n{text}"
        blob = getattr(resource, "blob", None)
        mime = getattr(resource, "mime_type", None) or getattr(resource, "mimeType", None) or "未知类型"
        return f"[二进制资源 {uri}：{mime}，base64 长度 {len(str(blob or ''))}]"

    if item_type == "resource_link":
        name = getattr(item, "name", "") or getattr(item, "title", "")
        uri = getattr(item, "uri", "")
        mime = getattr(item, "mime_type", None) or getattr(item, "mimeType", None) or ""
        return f"[资源链接 {name or uri}：{uri}{f' ({mime})' if mime else ''}]"

    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return _json_text(model_dump(mode="json"))
    return str(item)


def format_mcp_result(result: Any) -> tuple[str, bool]:
    """把 ``CallToolResult`` 转为回传模型的文本，并返回 ``isError``。"""
    parts: list[str] = []

    for item in list(getattr(result, "content", None) or []):
        text = _format_content_item(item)
        if text:
            parts.append(text)

    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if structured is not None:
        parts.append(f"结构化数据：\n{_json_text(structured)}")

    text = "\n\n".join(parts).strip()
    if not text:
        text = "（MCP 工具执行成功，但没有返回文本或结构化内容）"
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    return text, is_error


class McpClientManager:
    """管理一组 Streamable HTTP MCP Server 的连接、发现与工具调用。"""

    def __init__(
        self,
        servers: Sequence[McpServerSettings],
        *,
        call_timeout_s: float | None = None,
    ) -> None:
        self._servers = {server.name: server for server in servers}
        self._call_timeout_s = call_timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._lock = threading.Lock()
        self._closed = False

        self._sessions: dict[str, Any] = {}
        self._workers: dict[str, tuple[asyncio.Task[None], asyncio.Event]] = {}
        self._tools: dict[str, tuple[McpToolInfo, ...]] = {}
        self._errors: dict[str, str] = {}
        self._statuses: tuple[McpServerStatus, ...] = ()

    # ---- 生命周期 -----------------------------------------------------
    @property
    def statuses(self) -> tuple[McpServerStatus, ...]:
        return self._statuses

    @property
    def tools(self) -> tuple[McpToolInfo, ...]:
        """按配置顺序展开所有已连接 Server 的工具。"""
        entries: list[McpToolInfo] = []
        for name in self._servers:
            entries.extend(self._tools.get(name, ()))
        return tuple(entries)

    def connect(self) -> tuple[McpServerStatus, ...]:
        """连接所有 Server 并发现工具；失败仅记录在 status 中，不向上抛异常。"""
        if not self._servers:
            self._statuses = ()
            return self._statuses
        if self._closed:
            raise ToolError("MCP Client 已关闭，无法重新连接")

        self._start_loop()
        total_timeout = sum(server.timeout_s for server in self._servers.values()) + 30.0
        try:
            self._submit(self._connect_all(), timeout=total_timeout)
        finally:
            self._statuses = tuple(
                McpServerStatus(
                    name=server.name,
                    target=server.target,
                    connected=server.name in self._sessions,
                    tool_count=len(self._tools.get(server.name, ())),
                    error=self._errors.get(server.name),
                )
                for server in self._servers.values()
            )
        return self._statuses

    def close(self) -> None:
        """停止后台事件循环并关闭全部 MCP 会话。可重复调用。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            thread = self._thread

        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._stop_all(), loop)
                future.result(timeout=20.0)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        if loop is not None and not loop.is_closed():
            try:
                loop.close()
            except RuntimeError:
                pass
        self._loop = None
        self._thread = None

    def _start_loop(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._loop_ready.clear()
            self._thread = threading.Thread(target=self._loop_main, name="xagent-mcp", daemon=True)
            self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise ToolError("MCP Client 后台事件循环启动超时")

    def _loop_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def _submit(self, coro: Any, *, timeout: float) -> Any:
        loop = self._loop
        if loop is None or not loop.is_running():
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise ToolError("MCP Client 后台事件循环未运行")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"MCP 操作超时（>{timeout:g}s）") from exc

    async def _stop_all(self) -> None:
        for _task, stop in self._workers.values():
            stop.set()
        tasks = [task for task, _stop in self._workers.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 连接与发现 ---------------------------------------------------
    async def _connect_all(self) -> None:
        await asyncio.gather(*(self._connect_one(server) for server in self._servers.values()))

    async def _connect_one(self, server: McpServerSettings) -> None:
        ready: asyncio.Future[tuple[Any, ...]] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(self._serve(server, ready, stop), name=f"mcp:{server.name}")
        self._workers[server.name] = (task, stop)

        try:
            sdk_tools = await asyncio.wait_for(ready, timeout=server.timeout_s)
        except asyncio.TimeoutError:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._errors[server.name] = f"连接超时（>{server.timeout_s:g}s）"
            return
        except asyncio.CancelledError:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except BaseException as exc:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)
            self._errors[server.name] = _describe_exception(exc)
            return

        infos: list[McpToolInfo] = []
        for tool in sdk_tools:
            schema = getattr(tool, "input_schema", None)
            if schema is None:
                schema = getattr(tool, "inputSchema", None)
            if not isinstance(schema, Mapping) or "type" not in schema:
                schema = {"type": "object", "properties": {}, "additionalProperties": True}
            description = str(getattr(tool, "description", None) or getattr(tool, "title", None) or "")
            infos.append(
                McpToolInfo(
                    server=server.name,
                    name=str(tool.name),
                    description=description,
                    input_schema=dict(schema),
                )
            )
        self._tools[server.name] = tuple(infos)
        self._errors.pop(server.name, None)

    async def _serve(
        self,
        server: McpServerSettings,
        ready: asyncio.Future[tuple[Any, ...]],
        stop: asyncio.Event,
    ) -> None:
        """一个 Server 的完整会话生命周期都留在这个任务里（anyio 要求）。"""
        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream = await self._open_stream(server, stack)
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=server.timeout_s,
                    )
                )
                await session.initialize()
                listed = await session.list_tools()
                self._sessions[server.name] = session
                if not ready.done():
                    ready.set_result(tuple(listed.tools))
                await stop.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                self._errors[server.name] = _describe_exception(exc)
        finally:
            self._sessions.pop(server.name, None)

    async def _open_stream(
        self,
        server: McpServerSettings,
        stack: AsyncExitStack,
    ) -> tuple[Any, Any]:
        """按 transport 打开 MCP 标准传输（stdio 或 Streamable HTTP）。"""
        if server.transport == "stdio":
            parameters = StdioServerParameters(
                command=server.command,
                args=list(server.args),
                env=dict(server.env) or None,
                cwd=server.cwd or None,
            )
            streams = await stack.enter_async_context(stdio_client(parameters))
            return streams[0], streams[1]

        parameters = inspect.signature(_streamable_http).parameters

        if "http_client" in parameters:
            # MCP Python SDK 2.x：headers / timeout 通过 httpx2 客户端配置。
            from httpx2 import AsyncClient, Timeout  # type: ignore[import-not-found]

            client = AsyncClient(
                headers=dict(server.headers) or None,
                timeout=Timeout(server.timeout_s),
            )
            await stack.enter_async_context(client)
            streams = await stack.enter_async_context(_streamable_http(server.url, http_client=client))
        else:  # pragma: no cover - MCP Python SDK 1.x
            kwargs: dict[str, Any] = {"timeout": timedelta(seconds=server.timeout_s)}
            if server.headers:
                kwargs["headers"] = dict(server.headers)
            streams = await stack.enter_async_context(_streamable_http(server.url, **kwargs))

        return streams[0], streams[1]

    # ---- 工具调用 -----------------------------------------------------
    def call_tool(self, server_name: str, tool_name: str, arguments: Mapping[str, Any]) -> str:
        """同步转发给后台事件循环中的 MCP 会话；失败统一抛 :class:`ToolError`。"""
        server = self._servers.get(server_name)
        if server is None:
            raise ToolError(f"未知的 MCP Server：{server_name}")

        session = self._sessions.get(server_name)
        if session is None:
            reason = self._errors.get(server_name) or "尚未连接"
            raise ToolError(f"MCP Server「{server_name}」不可用：{reason}")

        timeout = self._call_timeout_s or (server.timeout_s + 10.0)
        try:
            result = self._submit(
                session.call_tool(tool_name, dict(arguments)),
                timeout=timeout,
            )
        except ToolError:
            raise
        except BaseException as exc:
            raise ToolError(
                f"调用 MCP Server「{server_name}」的工具 {tool_name} 失败：{_describe_exception(exc)}"
            ) from exc

        text, is_error = format_mcp_result(result)
        if is_error:
            raise ToolError(f"MCP Server「{server_name}」的工具 {tool_name} 返回错误：\n{text}")
        return text


__all__ = [
    "McpClientManager",
    "McpServerStatus",
    "McpToolInfo",
    "format_mcp_result",
]

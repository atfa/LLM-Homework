"""把发现的 MCP 工具适配成 xAgent 的 Function Calling 工具。

MCP 工具在 LLM 侧使用「暴露名」，内部维护
``暴露名 → (MCP Server, 原始工具名)`` 的映射。只有发生名称冲突时才加
``<server>__`` 前缀，避免污染本来就不冲突的工具名。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ...domain.tools import ToolDefinition
from ...ports.tools import ToolHandler
from .client import McpToolInfo

_MAX_TOOL_NAME = 64
_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _sanitize(name: str) -> str:
    return _INVALID_NAME_CHARS.sub("_", name).strip("_")


def _clip(name: str, *, seed: str = "") -> str:
    if len(name) <= _MAX_TOOL_NAME:
        return name or "tool"
    digest = hashlib.sha1((seed or name).encode()).hexdigest()[:8]
    return f"{name[: _MAX_TOOL_NAME - 9]}_{digest}"


def _allocate_name(server: str, tool: str, used: set[str]) -> str:
    base = _clip(_sanitize(tool) or "tool", seed=f"{server}\0{tool}")
    if base not in used:
        return base

    prefix = _sanitize(server) or "mcp"
    prefixed = _clip(f"{prefix}__{base}", seed=f"{server}\0{tool}")
    if prefixed not in used:
        return prefixed

    digest = hashlib.sha1(f"{server}\0{tool}".encode()).hexdigest()[:8]
    for index in range(1, 10_000):
        candidate = _clip(f"{prefix}_{digest}_{index}")
        if candidate not in used:
            return candidate
    raise ValueError(f"无法为 MCP 工具 {server}/{tool} 分配唯一名称")


@dataclass(frozen=True, slots=True)
class McpToolBinding:
    """一个 MCP 工具在 xAgent 内部的完整映射。"""

    exposed_name: str
    server: str
    remote_name: str
    definition: ToolDefinition
    handler: ToolHandler


class McpToolBridge:
    """持有 MCP 工具到本地工具注册表的桥接映射。"""

    def __init__(
        self,
        manager: Any,
        *,
        reserved_names: Iterable[str] = (),
    ) -> None:
        self._manager = manager
        used = set(reserved_names)
        bindings: list[McpToolBinding] = []
        by_name: dict[str, McpToolBinding] = {}

        for info in manager.tools:
            exposed = _allocate_name(info.server, info.name, used)
            used.add(exposed)
            definition = ToolDefinition(
                name=exposed,
                description=_describe(info),
                parameters=dict(info.input_schema),
            )
            binding = McpToolBinding(
                exposed_name=exposed,
                server=info.server,
                remote_name=info.name,
                definition=definition,
                handler=self._make_handler(info.server, info.name),
            )
            bindings.append(binding)
            by_name[exposed] = binding

        self._bindings = tuple(bindings)
        self._by_name = by_name

    def _make_handler(self, server: str, remote_name: str) -> ToolHandler:
        def handler(arguments: Mapping[str, Any]) -> str:
            return self._manager.call_tool(server, remote_name, arguments)

        return handler

    @property
    def bindings(self) -> tuple[McpToolBinding, ...]:
        return self._bindings

    def get(self, exposed_name: str) -> McpToolBinding | None:
        return self._by_name.get(exposed_name)

    def entries(self) -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
        return tuple((binding.definition, binding.handler) for binding in self._bindings)


def _describe(info: McpToolInfo) -> str:
    description = info.description.strip() or f"MCP Server「{info.server}」提供的工具"
    return f"[MCP:{info.server}] {description}"


__all__ = ["McpToolBinding", "McpToolBridge"]

"""内存工具注册表：负责参数解析、线程执行与错误归一化。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...domain.errors import ToolError
from ...domain.tools import ToolCall, ToolDefinition, ToolOutcome
from ...ports.tools import ToolHandler


@dataclass(frozen=True, slots=True)
class _Entry:
    definition: ToolDefinition
    handler: ToolHandler


class ToolRegistryImpl:
    """实现 :class:`xagent.ports.tools.ToolRegistry`。"""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    # ---- 注册 ---------------------------------------------------------
    def register(self, definition: ToolDefinition, handler: ToolHandler, *, overwrite: bool = False) -> None:
        if not overwrite and definition.name in self._entries:
            raise ValueError(f"工具重复注册：{definition.name}")
        self._entries[definition.name] = _Entry(definition=definition, handler=handler)

    def unregister(self, name: str) -> None:
        self._entries.pop(name, None)

    # ---- 查询 ---------------------------------------------------------
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(entry.definition for entry in self._entries.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return name in self._entries

    # ---- 执行 ---------------------------------------------------------
    async def invoke(self, call: ToolCall) -> ToolOutcome:
        entry = self._entries.get(call.name)
        if entry is None:
            available = "、".join(sorted(self._entries)) or "（无）"
            return ToolOutcome.failed(f"未知工具：{call.name}。可用工具：{available}")

        try:
            arguments = json.loads(call.arguments or "{}")
        except ValueError as exc:
            return ToolOutcome.failed(f"参数不是合法 JSON：{call.arguments}（{exc}）")
        if not isinstance(arguments, Mapping):
            return ToolOutcome.failed(f"参数必须是 JSON 对象：{call.arguments}")

        try:
            result = await asyncio.to_thread(entry.handler, arguments)
        except asyncio.CancelledError:
            raise
        except ToolError as exc:
            return ToolOutcome.failed(f"工具执行失败：{exc}")
        except (OSError, ValueError) as exc:
            return ToolOutcome.failed(f"工具执行失败：{exc}")
        except Exception as exc:  # pragma: no cover - 防御性兜底
            return ToolOutcome.failed(f"工具 {call.name} 执行异常：{type(exc).__name__}: {exc}")

        return ToolOutcome.succeeded(result if isinstance(result, str) else str(result))


def as_dict(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """统一把 Mapping 转成普通 dict（便于序列化/测试）。"""
    return dict(arguments)

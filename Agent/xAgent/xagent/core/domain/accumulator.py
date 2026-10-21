"""把流式 ``ToolCallDelta`` 归并成完整 ``ToolCall`` 的纯逻辑。

这一层不做 I/O、不依赖网络，便于单测覆盖「参数被拆成多块」的边界情况。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .events import ToolCallDelta
from .tools import ToolCall


@dataclass
class _Slot:
    id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)


class ToolCallAccumulator:
    """按 ``index`` 累积分块下发的工具调用。"""

    def __init__(self) -> None:
        self._slots: dict[int, _Slot] = {}

    def feed(self, delta: ToolCallDelta) -> None:
        slot = self._slots.setdefault(delta.index, _Slot())
        if delta.id:
            slot.id = delta.id
        if delta.name:
            slot.name = delta.name
        if delta.arguments:
            slot.arguments.append(delta.arguments)

    def build(self) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        for index in sorted(self._slots):
            slot = self._slots[index]
            if not slot.name:
                # 没有名字的残块无法执行，直接丢弃，避免污染消息列表
                continue
            calls.append(
                ToolCall(
                    id=slot.id or f"call_{index}",
                    name=slot.name,
                    arguments="".join(slot.arguments) or "{}",
                )
            )
        return tuple(calls)

    def __bool__(self) -> bool:
        return bool(self._slots)

    def __len__(self) -> int:
        return len(self._slots)

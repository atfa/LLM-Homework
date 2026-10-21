"""core：Agent 的全部核心逻辑，与任何界面无关。

内部继续保持四层（依赖只能由外向内）：

    tui / 未来的 Web、Desktop 界面
        ↓（只消费 AgentRuntime 与事件流）
    core.composition（依赖装配）
        ↓
    core.application（用例编排：AgentService）
        ↓
    core.ports（抽象协议） ← core.infrastructure（实现）
        ↓
    core.domain（纯业务模型，零 I/O）

界面替换规则：新增 Web / Desktop 前端时只新增界面层代码，
core 一行都不需要改。具体见仓库根目录 ARCHITECTURE.md。
"""

from __future__ import annotations

__all__: list[str] = []

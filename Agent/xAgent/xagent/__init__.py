"""xAgent：分层可扩展的本地工具调用 Agent。

顶层只有两个可替换边界：

    core（Agent 全部核心，与界面无关）
        domain / ports / application / infrastructure / composition
        ↓
    tui（Textual 界面，只消费 AgentRuntime 与事件流）

新增 Web / Desktop 前端时，只新增界面层包（如 ``xagent.web``），
core 一行都不需要改。具体见仓库根目录 ARCHITECTURE.md。
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]

"""xAgent：分层可扩展的本地工具调用 Agent。

分层（依赖只能由外向内）：

    interfaces (TUI / CLI / 未来的 Web、Desktop)
        ↓
    composition (依赖装配)
        ↓
    application (用例编排：AgentService)
        ↓
    ports (抽象协议) ← infrastructure (实现)
        ↓
    domain (纯业务模型，零 I/O)

具体见仓库根目录 ARCHITECTURE.md。
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]

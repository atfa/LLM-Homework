#!/usr/bin/env python3
"""xAgent 启动脚本（等价于 ``python -m xagent``）。

TUI：    python main.py
工具：   python main.py --list-tools
"""

from __future__ import annotations

from xagent.entrypoint import main

if __name__ == "__main__":
    raise SystemExit(main())

"""跨 UI 复用的展示格式化工具。

TUI / CLI / 未来的 Web、Desktop 共用这些纯函数，
避免每个界面各写一套参数摘要与截断逻辑。
"""

from __future__ import annotations

import json


def summarize_arguments(arguments: str, *, limit: int = 64) -> str:
    """把工具参数压成单行摘要，用于标题/日志。"""
    raw = (arguments or "{}").strip()
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        text = raw.replace("\n", " ")
    else:
        if isinstance(value, dict):
            parts: list[str] = []
            for key, item in value.items():
                rendered = item.replace("\n", " ") if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                if len(rendered) > 24:
                    rendered = rendered[:21] + "..."
                parts.append(f"{key}={rendered}")
            text = ", ".join(parts)
        else:
            text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def pretty_arguments(arguments: str) -> str:
    try:
        return json.dumps(json.loads(arguments or "{}"), ensure_ascii=False, indent=2)
    except ValueError:
        return arguments or "{}"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...（已省略 {len(text) - limit} 字符）"


def first_lines(text: str, limit: int = 3) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join(lines[:limit] + ["..."])

"""时间与闰年工具。"""

from __future__ import annotations

import calendar
import os
import subprocess
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ....domain.errors import ToolError
from ....domain.tools import ToolDefinition
from ....ports.tools import ToolHandler

GET_DATE = ToolDefinition(
    name="getDate",
    description="获取当前电脑的日期和时间（通过执行本地 date 命令获得）",
    parameters={"type": "object", "properties": {}, "required": []},
)

IS_LEAP_YEAR = ToolDefinition(
    name="isLeapYear",
    description="判断指定年份是否为闰年，返回判断结果及依据（用本地 Python 代码精确计算，不靠猜测）",
    parameters={
        "type": "object",
        "properties": {
            "year": {
                "type": "integer",
                "description": "要判断的年份，例如 2024；问“今年”时先调用 getDate 取当前年份",
            }
        },
        "required": ["year"],
    },
)


def get_date(_: Mapping[str, Any]) -> str:
    """优先调用系统 date 命令；命令不可用时回退到 Python 时钟。"""
    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'",
        ]
    else:
        command = ["date", "+%Y-%m-%d %H:%M:%S %Z (%A)"]

    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError:
        completed = None
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(f"执行 date 命令失败：{exc}") from exc

    if completed is not None and completed.returncode == 0:
        output = (completed.stdout or "").strip()
        if output:
            return output

    # 兜底：命令缺失/失败时仍然返回可用的本机时间
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z (%A)")


def is_leap_year(arguments: Mapping[str, Any]) -> str:
    raw_year = arguments.get("year")
    if isinstance(raw_year, bool) or raw_year is None:
        raise ToolError(f"year 必须是整数，收到 {raw_year!r}")
    try:
        year = int(raw_year)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"year 必须是整数，收到 {raw_year!r}") from exc
    if year < 1:
        raise ToolError("year 必须是正整数")

    leap = calendar.isleap(year)
    if year % 400 == 0:
        reason = "能被 400 整除"
    elif year % 100 == 0:
        reason = "能被 100 整除但不能被 400 整除"
    elif year % 4 == 0:
        reason = "能被 4 整除且不能被 100 整除"
    else:
        reason = "不能被 4 整除"
    return f"{year} 年{'是' if leap else '不是'}闰年（{reason}）。"


def datetime_tools() -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    return ((GET_DATE, get_date), (IS_LEAP_YEAR, is_leap_year))

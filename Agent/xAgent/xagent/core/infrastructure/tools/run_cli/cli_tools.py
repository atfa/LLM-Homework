"""命令行工具（runCli）：通过 ``subprocess`` 调用本机已安装的任意 CLI 程序。

教学用途：让学生看到 Agent 的完整闭环——提出工具调用 → 在本机执行 →
读取 stdout / stderr / 退出码 → 判断结果 → 决定下一步。

因此本阶段**故意不实现**命令白名单、黑名单或审批机制：命令以运行 xAgent 的
当前用户权限执行，请仅在受控的教学实验环境中使用。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ....domain.errors import ToolError
from ....domain.tools import ToolDefinition
from ....ports.tools import ToolHandler

#: 单条命令的默认超时（秒）。
DEFAULT_TIMEOUT_S = 30
#: 允许模型请求的最大超时（秒），避免一条命令长期占用一轮对话。
MAX_TIMEOUT_S = 600
#: stdout / stderr 各自回传模型的最大字符数，超出部分保留首尾并标明省略量。
MAX_STREAM_CHARS = 20_000

RUN_CLI = ToolDefinition(
    name="runCli",
    description=(
        "在本机执行一个命令行程序，并返回标准输出（stdout）、标准错误（stderr）和退出码，"
        '供你判断执行结果并决定下一步。command 是程序名或可执行文件路径（如 "git"、'
        '"python3"、"/bin/ls"），args 是传给它的参数列表（不含程序名本身），'
        '例如 command="git"、args=["status", "--short"]。'
        "命令以参数列表方式直接执行（shell=False），因此管道 |、重定向 >、通配符 *、"
        "环境变量 $VAR 等 shell 语法不会被解释，需要时请显式调用 sh -c 并自行传入完整命令字符串。"
        "工作目录为 Agent 的工作目录。非零退出码不会被当作工具失败，而是原样返回，请结合 stderr 判断。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": '要执行的程序名或路径，例如 "git"、"python3"、"/bin/ls"',
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": '传给程序的参数列表，不含程序名本身，例如 ["status", "--short"]；无参数时传 []',
                "default": [],
            },
            "timeout_seconds": {
                "type": "integer",
                "description": f"执行超时（秒），默认 {DEFAULT_TIMEOUT_S}，最大 {MAX_TIMEOUT_S}；"
                "超时会终止命令并返回超时前已产生的输出",
                "default": DEFAULT_TIMEOUT_S,
            },
        },
        "required": ["command"],
    },
)


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------
def _require_command(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError('command 必须是非空字符串，例如 "git" 或 "/bin/ls"')
    return value.strip()


def _require_args(value: Any) -> list[str]:
    """把模型给的参数列表统一成字符串列表。

    规范做法是字符串数组；为容错，数字（int / float）会被转成字符串，
    布尔值、null、对象、数组等一律拒绝。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolError(f"args 必须是字符串数组，收到 {type(value).__name__}")

    args: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            args.append(item)
        elif isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ToolError(f"args[{index}] 必须是字符串，收到 {item!r}")
        else:
            args.append(str(item))
    return args


def _require_timeout(value: Any) -> int:
    if value is None:
        return DEFAULT_TIMEOUT_S
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"timeout_seconds 必须是数字，收到 {value!r}")
    seconds = float(value)
    if seconds < 1:
        raise ToolError("timeout_seconds 必须至少为 1 秒")
    if seconds > MAX_TIMEOUT_S:
        raise ToolError(f"timeout_seconds 不能超过 {MAX_TIMEOUT_S} 秒，收到 {seconds:g}")
    return int(seconds)


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------
def _coerce_text(value: Any) -> str:
    """把 subprocess 返回的 str / bytes / None 统一成字符串。

    ``subprocess.run`` 正常返回时是 str；超时抛出的 ``TimeoutExpired`` 在 POSIX 上
    携带的却是 bytes，因此两种都要兼容。
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate_stream(text: str) -> str:
    """空输出给出明确标记；超长输出保留首尾并注明省略了多少字符。"""
    if not text.strip():
        return "（空）"
    if len(text) <= MAX_STREAM_CHARS:
        return text

    head_len = MAX_STREAM_CHARS * 2 // 3
    tail_len = MAX_STREAM_CHARS - head_len
    omitted = len(text) - MAX_STREAM_CHARS
    return (
        f"{text[:head_len]}\n"
        f"...（输出过长，中间省略 {omitted} 个字符，共 {len(text)} 字符；"
        f"如需完整内容请缩小命令的输出范围）...\n"
        f"{text[-tail_len:]}"
    )


def _format_command(argv: list[str]) -> str:
    """把参数列表渲染成一行便于阅读的命令。

    这里只追求可读性，不追求「可复制回 shell 执行」：含空白或为空的参数
    用双引号包起来，其余原样显示。注意它**不是** shell 命令，``|``、``>``
    等符号都不会被解释。
    """
    return " ".join(_quote_arg(part) for part in argv)


def _quote_arg(part: str) -> str:
    if part and not any(character.isspace() for character in part):
        return part
    return json.dumps(part, ensure_ascii=False)


def _format_result(argv: list[str], returncode: int, stdout: str, stderr: str) -> str:
    return "\n".join(
        [
            f"命令：{_format_command(argv)}",
            f"退出码：{returncode}",
            "",
            "标准输出（stdout）：",
            _truncate_stream(stdout),
            "",
            "标准错误（stderr）：",
            _truncate_stream(stderr),
        ]
    )


def _format_timeout(argv: list[str], timeout: int, exc: subprocess.TimeoutExpired) -> str:
    stdout = _coerce_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
    stderr = _coerce_text(getattr(exc, "stderr", None))
    return "\n".join(
        [
            f"命令：{_format_command(argv)}",
            f"执行超时：超过 {timeout} 秒仍未结束，已终止该命令（无退出码）。",
            "",
            "标准输出（stdout，超时前已产生的部分）：",
            _truncate_stream(stdout),
            "",
            "标准错误（stderr，超时前已产生的部分）：",
            _truncate_stream(stderr),
        ]
    )


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
def run_cli(arguments: Mapping[str, Any], cwd: str | Path | None = None) -> str:
    """执行 ``command`` + ``args``，返回可读的 stdout / stderr / 退出码。

    非零退出码与超时都作为正常结果返回（模型据此判断），只有参数非法、
    程序不存在或无法启动时才抛 :class:`ToolError`。
    """
    command = _require_command(arguments.get("command"))
    args = _require_args(arguments.get("args"))
    timeout = _require_timeout(arguments.get("timeout_seconds"))
    argv = [command, *args]

    try:
        completed = subprocess.run(
            argv,
            shell=False,  # 参数列表直接执行，不经过 shell，避免注入与意外解释
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"找不到可执行程序：{command}（请确认它已安装并在 PATH 中）") from exc
    except PermissionError as exc:
        raise ToolError(f"没有权限执行：{command}（{exc}）") from exc
    except subprocess.TimeoutExpired as exc:
        return _format_timeout(argv, timeout, exc)
    except OSError as exc:
        raise ToolError(f"启动命令失败：{command}（{exc}）") from exc

    return _format_result(argv, completed.returncode, completed.stdout or "", completed.stderr or "")


def run_cli_tools(workspace: str | Path) -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    """把工作目录绑定为命令的 cwd，返回可直接注册的 (定义, 处理器) 列表。"""
    root = str(Path(workspace).expanduser())

    def invoke(arguments: Mapping[str, Any]) -> str:
        return run_cli(arguments, cwd=root)

    return ((RUN_CLI, invoke),)


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_STREAM_CHARS",
    "MAX_TIMEOUT_S",
    "RUN_CLI",
    "run_cli",
    "run_cli_tools",
]

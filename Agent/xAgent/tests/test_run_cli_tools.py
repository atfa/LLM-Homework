"""命令行工具（runCli）测试：参数校验、非 shell 执行、结果格式、超时与截断。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from xagent.core.domain.errors import ToolError
from xagent.core.domain.tools import ToolCall, ToolOutcome
from xagent.core.infrastructure.tools.catalog import build_tool_registry
from xagent.core.infrastructure.tools.registry import ToolRegistryImpl
from xagent.core.infrastructure.tools.run_cli import cli_tools as cli_module
from xagent.core.infrastructure.tools.run_cli.cli_tools import (
    DEFAULT_TIMEOUT_S,
    MAX_STREAM_CHARS,
    MAX_TIMEOUT_S,
    run_cli,
)


class FakeCompleted:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Recorder:
    """记录 subprocess.run 的调用参数，并返回预设结果。"""

    def __init__(self, result: FakeCompleted | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeCompleted:
        self.calls.append({"command": command, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def invoke(registry: ToolRegistryImpl, name: str, **arguments: object) -> ToolOutcome:
    call = ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments, ensure_ascii=False))
    return asyncio.run(registry.invoke(call))


# ---------------------------------------------------------------------------
# 参数拼装与执行方式
# ---------------------------------------------------------------------------
def test_run_cli_builds_argument_list_without_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder = Recorder(FakeCompleted(stdout="hello\n"))
    monkeypatch.setattr(cli_module.subprocess, "run", recorder)

    text = run_cli({"command": "echo", "args": ["hello", "world"]}, cwd=tmp_path)

    call = recorder.calls[0]
    assert call["command"] == ["echo", "hello", "world"]
    assert call["shell"] is False
    assert call["cwd"] == str(tmp_path)
    assert call["timeout"] == DEFAULT_TIMEOUT_S
    assert call["capture_output"] is True
    assert call["text"] is True
    assert "退出码：0" in text
    assert "hello" in text


def test_run_cli_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeCompleted())
    monkeypatch.setattr(cli_module.subprocess, "run", recorder)

    run_cli({"command": "sleep", "args": ["1"], "timeout_seconds": 5})

    assert recorder.calls[0]["timeout"] == 5
    assert recorder.calls[0]["cwd"] is None


def test_run_cli_accepts_numeric_args(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeCompleted())
    monkeypatch.setattr(cli_module.subprocess, "run", recorder)

    run_cli({"command": "head", "args": ["-n", 2]})

    assert recorder.calls[0]["command"] == ["head", "-n", "2"]


def test_run_cli_allows_missing_args(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeCompleted())
    monkeypatch.setattr(cli_module.subprocess, "run", recorder)

    run_cli({"command": "pwd"})

    assert recorder.calls[0]["command"] == ["pwd"]


# ---------------------------------------------------------------------------
# 结果格式
# ---------------------------------------------------------------------------
def test_run_cli_reports_stdout_stderr_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeCompleted(returncode=3, stdout="out-line\n", stderr="err-line\n"))
    monkeypatch.setattr(cli_module.subprocess, "run", recorder)

    text = run_cli({"command": "mytool", "args": ["--flag"]})

    assert "mytool --flag" in text
    assert "退出码：3" in text
    assert "out-line" in text
    assert "err-line" in text


def test_run_cli_marks_empty_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(FakeCompleted()))

    text = run_cli({"command": "true"})

    assert text.count("（空）") == 2


def test_run_cli_formats_args_readably(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(FakeCompleted()))

    text = run_cli({"command": "python3", "args": ["-c", "print('hi there')"]})

    # 含空白的参数加引号，其余原样，便于阅读
    assert "python3 -c \"print('hi there')\"" in text


def test_run_cli_nonzero_exit_is_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module.subprocess,
        "run",
        Recorder(FakeCompleted(returncode=127, stderr="command not found")),
    )

    text = run_cli({"command": "nope"})

    assert "退出码：127" in text
    assert "command not found" in text


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"command": ""},
        {"command": "   "},
        {"command": 123},
        {"command": "ls", "args": "not-a-list"},
        {"command": "ls", "args": [{"a": 1}]},
        {"command": "ls", "args": [True]},
        {"command": "ls", "args": [None]},
        {"command": "ls", "timeout_seconds": 0},
        {"command": "ls", "timeout_seconds": -1},
        {"command": "ls", "timeout_seconds": 0.5},
        {"command": "ls", "timeout_seconds": MAX_TIMEOUT_S + 1},
        {"command": "ls", "timeout_seconds": "10"},
        {"command": "ls", "timeout_seconds": True},
    ],
)
def test_run_cli_rejects_invalid_arguments(monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any]) -> None:
    recorder = Recorder(FakeCompleted())
    monkeypatch.setattr(cli_module.subprocess, "run", recorder)

    with pytest.raises(ToolError):
        run_cli(arguments)

    assert recorder.calls == []


# ---------------------------------------------------------------------------
# 启动失败
# ---------------------------------------------------------------------------
def test_run_cli_handles_missing_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(FileNotFoundError("nope")))

    with pytest.raises(ToolError) as excinfo:
        run_cli({"command": "definitely-not-a-real-program"})

    assert "找不到" in str(excinfo.value)


def test_run_cli_handles_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(PermissionError("denied")))

    with pytest.raises(ToolError) as excinfo:
        run_cli({"command": "secret"})

    assert "权限" in str(excinfo.value)


def test_run_cli_handles_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(OSError("boom")))

    with pytest.raises(ToolError) as excinfo:
        run_cli({"command": "whatever"})

    assert "启动命令失败" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 超时
# ---------------------------------------------------------------------------
def test_run_cli_reports_timeout_with_partial_output(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = subprocess.TimeoutExpired(
        cmd=["sleep", "10"], timeout=1, output=b"partial-out\n", stderr=b"partial-err\n"
    )
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(expired))

    text = run_cli({"command": "sleep", "args": ["10"], "timeout_seconds": 1})

    assert "执行超时" in text
    assert "超过 1 秒" in text
    assert "partial-out" in text
    assert "partial-err" in text


def test_run_cli_timeout_accepts_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = subprocess.TimeoutExpired(cmd="x", timeout=2, output="str-out", stderr="str-err")
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(expired))

    text = run_cli({"command": "x"})

    assert "str-out" in text
    assert "str-err" in text


# ---------------------------------------------------------------------------
# 截断
# ---------------------------------------------------------------------------
def test_run_cli_truncates_long_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    long_stdout = "A" * (MAX_STREAM_CHARS + 500)
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(FakeCompleted(stdout=long_stdout)))

    text = run_cli({"command": "cat", "args": ["big"]})

    assert "中间省略 500 个字符" in text
    assert f"共 {len(long_stdout)} 字符" in text
    # 首尾都保留：渲染出的正文长度恰好等于上限
    assert text.count("A") == MAX_STREAM_CHARS


def test_run_cli_truncates_stderr_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    long_stderr = "B" * (MAX_STREAM_CHARS + 1234)
    monkeypatch.setattr(cli_module.subprocess, "run", Recorder(FakeCompleted(stderr=long_stderr)))

    text = run_cli({"command": "noisy"})

    assert "中间省略 1234 个字符" in text
    assert text.count("B") == MAX_STREAM_CHARS


# ---------------------------------------------------------------------------
# 注册表集成（真实子进程）
# ---------------------------------------------------------------------------
def test_registry_registers_run_cli(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    assert "runCli" in registry.names()
    definition = next(item for item in registry.definitions() if item.name == "runCli")
    assert definition.parameters["required"] == ["command"]
    assert definition.parameters["properties"]["args"]["type"] == "array"
    assert definition.parameters["properties"]["command"]["type"] == "string"


def test_run_cli_runs_real_command_in_workspace(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    outcome = invoke(
        registry,
        "runCli",
        command=sys.executable,
        args=["-c", "import pathlib; pathlib.Path('marker.txt').write_text('ok')"],
    )

    assert outcome.ok, outcome.content
    assert "退出码：0" in outcome.content
    # 子进程的工作目录应当是工作目录本身
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "ok"


def test_run_cli_returns_real_exit_code(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    outcome = invoke(
        registry,
        "runCli",
        command=sys.executable,
        args=["-c", "import sys; sys.exit(4)"],
    )

    assert outcome.ok, outcome.content
    assert "退出码：4" in outcome.content


def test_run_cli_does_not_interpret_shell_syntax(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    outcome = invoke(
        registry,
        "runCli",
        command=sys.executable,
        args=["-c", "import sys; print(sys.argv[1])", "a; echo b"],
    )

    assert outcome.ok, outcome.content
    # 参数原样传给程序，没有被 shell 拆分或解释
    assert "a; echo b" in outcome.content


def test_run_cli_unknown_program_is_failed_outcome(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    outcome = invoke(registry, "runCli", command="xagent-definitely-missing-binary")

    assert outcome.ok is False
    assert "找不到" in outcome.content

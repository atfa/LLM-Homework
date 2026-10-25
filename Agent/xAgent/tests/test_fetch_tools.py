"""网页抓取工具（fetchWeb）测试：参数校验、curl 调用、正文提取与错误归一化。"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from xagent.core.domain.errors import ToolError
from xagent.core.domain.tools import ToolCall, ToolOutcome
from xagent.core.infrastructure.tools.catalog import build_tool_registry
from xagent.core.infrastructure.tools.registry import ToolRegistryImpl
from xagent.core.infrastructure.tools.web_fetch import fetch_tools as fetch_module
from xagent.core.infrastructure.tools.web_fetch.fetch_tools import fetch_web

SAMPLE_HTML = """
<!doctype html>
<html>
  <head><title>示例文章</title></head>
  <body>
    <nav>首页 | 关于我们</nav>
    <article>
      <h1>示例标题</h1>
      <p>这是第一段正文内容，介绍文章主题。</p>
      <p>这是第二段正文内容，包含更多细节。</p>
      <ul><li>要点一</li><li>要点二</li></ul>
      <p><a href="https://example.com/more">延伸阅读</a></p>
    </article>
    <footer>版权所有 © 2026</footer>
  </body>
</html>
"""


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


def test_fetch_web_extracts_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeCompleted(stdout=SAMPLE_HTML))
    monkeypatch.setattr(fetch_module.subprocess, "run", recorder)

    text = fetch_web({"url": "https://example.com/article"})

    assert "https://example.com/article" in text
    assert "示例标题" in text
    assert "第一段正文内容" in text
    # 导航栏与页脚应被剔除
    assert "关于我们" not in text
    assert "版权所有" not in text

    call = recorder.calls[0]
    assert isinstance(call["command"], list)
    assert call["command"][0] == "curl"
    assert "https://example.com/article" in call["command"]
    assert call.get("shell", False) is False


def test_fetch_web_rejects_bad_url(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeCompleted(stdout=SAMPLE_HTML))
    monkeypatch.setattr(fetch_module.subprocess, "run", recorder)

    for bad in ("", "   ", "ftp://example.com", "example.com"):
        with pytest.raises(ToolError):
            fetch_web({"url": bad})
    with pytest.raises(ToolError):
        fetch_web({})
    with pytest.raises(ToolError):
        fetch_web({"url": 123})  # type: ignore[dict-item]

    assert recorder.calls == []


def test_fetch_web_handles_curl_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fetch_module.subprocess,
        "run",
        Recorder(FakeCompleted(returncode=6, stderr="Could not resolve host")),
    )
    with pytest.raises(ToolError) as excinfo:
        fetch_web({"url": "https://example.com"})
    assert "curl" in str(excinfo.value)


def test_fetch_web_handles_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module.subprocess, "run", Recorder(FakeCompleted(stdout="   ")))
    with pytest.raises(ToolError) as excinfo:
        fetch_web({"url": "https://example.com"})
    assert "空" in str(excinfo.value)


def test_fetch_web_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fetch_module.subprocess,
        "run",
        Recorder(subprocess.TimeoutExpired(cmd="curl", timeout=1)),
    )
    with pytest.raises(ToolError) as excinfo:
        fetch_web({"url": "https://example.com"})
    assert "超时" in str(excinfo.value)


def test_fetch_web_handles_missing_curl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module.subprocess, "run", Recorder(FileNotFoundError("curl")))
    with pytest.raises(ToolError) as excinfo:
        fetch_web({"url": "https://example.com"})
    assert "curl" in str(excinfo.value)


def test_fetch_web_handles_unparsable_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module.subprocess, "run", Recorder(FakeCompleted(stdout="<html></html>")))

    class Boom:
        @staticmethod
        def extract(*_: Any, **__: Any) -> str:
            raise ValueError("parse boom")

    monkeypatch.setattr(fetch_module, "trafilatura", Boom)
    with pytest.raises(ToolError) as excinfo:
        fetch_web({"url": "https://example.com"})
    assert "解析" in str(excinfo.value)


def test_fetch_web_handles_no_extractable_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module.subprocess, "run", Recorder(FakeCompleted(stdout="<html><body></body></html>")))

    class Empty:
        @staticmethod
        def extract(*_: Any, **__: Any) -> None:
            return None

    monkeypatch.setattr(fetch_module, "trafilatura", Empty)
    with pytest.raises(ToolError) as excinfo:
        fetch_web({"url": "https://example.com"})
    assert "提取" in str(excinfo.value)


def test_registry_registers_fetch_web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_tool_registry(tmp_path)
    assert "fetchWeb" in registry.names()

    monkeypatch.setattr(fetch_module.subprocess, "run", Recorder(FakeCompleted(stdout=SAMPLE_HTML)))
    outcome = invoke(registry, "fetchWeb", url="https://example.com/article")
    assert outcome.ok, outcome.content
    assert "示例标题" in outcome.content

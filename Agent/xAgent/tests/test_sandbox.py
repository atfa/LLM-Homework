"""沙箱与文本探测测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from xagent.core.domain.errors import SandboxViolation, ToolError
from xagent.core.infrastructure.tools.file_ops.sandbox import Sandbox, decode_text, format_size


def test_sandbox_resolves_inside_paths(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path)
    assert sandbox.resolve(".") == tmp_path.resolve()
    assert sandbox.resolve("a/b") == tmp_path.resolve() / "a" / "b"
    assert sandbox.display(sandbox.resolve("a/../b")) == "b"
    assert sandbox.display(tmp_path) == "."


@pytest.mark.parametrize("bad", ["../secret.txt", "../../etc/passwd", "/etc/passwd"])
def test_sandbox_rejects_escapes(tmp_path: Path, bad: str) -> None:
    sandbox = Sandbox(tmp_path)
    with pytest.raises(SandboxViolation):
        sandbox.resolve(bad)


def test_sandbox_rejects_external_symlink(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(outside, link, target_is_directory=True)

    sandbox = Sandbox(tmp_path)
    with pytest.raises(SandboxViolation):
        sandbox.resolve("link/secret.txt")


def test_sandbox_accepts_internal_symlink(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "alias", target_is_directory=True)
    sandbox = Sandbox(tmp_path)
    assert sandbox.resolve("alias") == (tmp_path / "real").resolve()


def test_sandbox_rejects_non_string_and_nul(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path)
    with pytest.raises(ToolError):
        sandbox.resolve(123)  # type: ignore[arg-type]
    with pytest.raises(SandboxViolation):
        sandbox.resolve("a\x00b")


def test_sandbox_root_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        Sandbox(tmp_path / "missing")


def test_decode_text_encodings() -> None:
    assert decode_text("你好".encode()) == ("你好", "utf-8")
    assert decode_text("你好".encode("gb18030")) == ("你好", "gb18030")
    assert decode_text(b"a\x00b") is None
    assert decode_text(b"\xff\xfe\xfd") is None


def test_format_size() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024 * 3 // 2) == "1.5 MB"

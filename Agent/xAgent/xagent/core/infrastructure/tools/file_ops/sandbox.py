"""路径沙箱与文本探测工具。

安全边界：所有路径先 ``expanduser`` 再 ``realpath``（解析符号链接），
最后必须落在工作目录内。这样 ``../`` 和指向外部的软链接都会被拒绝。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ....domain.errors import SandboxViolation, ToolError

MAX_READ_BYTES = 256 * 1024
"""单次读取上限，避免把巨大文件塞进模型上下文。"""

MAX_LIST_ENTRIES = 200
"""单次列目录上限。"""

SNIFF_BYTES = 64 * 1024
"""判断文本/二进制时最多读取的字节数。"""


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{size} B"  # pragma: no cover


def decode_text(data: bytes) -> tuple[str, str] | None:
    """尝试按 UTF-8 / GB18030 解码；含 NUL 或都失败则视为二进制。"""
    if b"\x00" in data:
        return None
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def read_head(path: Path, limit: int = SNIFF_BYTES) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


class Sandbox:
    """把模型给出的相对路径解析成受限的绝对路径。"""

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        resolved = Path(os.path.realpath(candidate))
        if not resolved.is_dir():
            raise ToolError(f"沙箱根目录不存在或不是目录：{resolved}")
        self.root = resolved

    def resolve(self, raw: Any = None) -> Path:
        if raw is None:
            text = "."
        elif isinstance(raw, str):
            text = raw.strip() or "."
        else:
            raise ToolError(f"路径必须是字符串，收到 {type(raw).__name__}")
        if "\x00" in text:
            raise SandboxViolation("路径包含非法字符 NUL")
        candidate = Path(os.path.expanduser(text))
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = Path(os.path.realpath(candidate))
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise SandboxViolation(f"拒绝访问工作目录之外的路径：{text}（沙箱根：{self.root}）") from None
        return resolved

    def display(self, target: Path) -> str:
        try:
            relative = target.relative_to(self.root).as_posix()
        except ValueError:  # pragma: no cover - resolve() 已保证在沙箱内
            return str(target)
        return "." if relative in ("", ".") else relative

    def is_root(self, target: Path) -> bool:
        return Path(os.path.realpath(target)) == self.root

    def ensure_text_file(self, target: Path) -> None:
        if not os.path.lexists(target):
            raise ToolError(f"文件不存在：{self.display(target)}")
        if target.is_dir():
            raise ToolError(f"是目录，不是文件：{self.display(target)}")
        if not target.is_file():
            raise ToolError(f"不是普通文件：{self.display(target)}")
        head = read_head(target)
        if decode_text(head) is None:
            raise ToolError(f"不是纯文本文件（UTF-8/GB18030）：{self.display(target)}")

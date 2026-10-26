"""极简 YAML Frontmatter 解析器（只依赖标准库）。

Agent Skills 规范要求 ``SKILL.md`` 以 ``---`` 包裹的 YAML Frontmatter 开头，
其中 ``name`` 与 ``description`` 是必需字段。这里只解析顶层标量键值，
覆盖技能元数据最常见的写法：

- 普通标量：``name: pdf``
- 引号标量：``description: "处理 PDF 文件"``
- 块标量：``description: |`` / ``|-`` / ``>``

初始化阶段只需要元数据，因此 :func:`read_frontmatter_file` 读到结束分隔线
就停止，不会把 Skill 正文读进内存。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_BOM = "\ufeff"
_BOM_BYTES = b"\xef\xbb\xbf"
_DELIMITERS = frozenset({"---", "..."})
_DELIMITER_BYTES = frozenset(token.encode("ascii") for token in _DELIMITERS)
_MAX_FRONTMATTER_BYTES = 256 * 1024
_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)[ \t]*:[ \t]*(.*)$")


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """把文本拆成 ``(frontmatter, body)``；没有 Frontmatter 时返回 None。"""
    if text.startswith(_BOM):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() in _DELIMITERS:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """解析文本中的 Frontmatter；不存在或格式不完整时返回 None。"""
    parts = split_frontmatter(text)
    if parts is None:
        return None
    try:
        return _parse_mapping(parts[0])
    except ValueError:
        return None


def read_frontmatter_file(path: Path) -> dict[str, str] | None:
    """只读取文件的 Frontmatter 部分，不读取正文。

    以二进制逐行读取，遇到结束分隔线立即停止并只解码 Frontmatter 本身，
    因此即使正文很大或不是 UTF-8，也不影响初始化阶段拿到元数据。
    找不到起始/结束分隔线、编码失败或格式不完整时返回 None。
    """
    try:
        with path.open("rb") as handle:
            first = handle.readline()
            if first.startswith(_BOM_BYTES):
                first = first[len(_BOM_BYTES) :]
            if first.strip() != b"---":
                return None

            block = bytearray()
            for line in handle:
                if line.strip() in _DELIMITER_BYTES:
                    return _parse_mapping(block.decode("utf-8"))
                block.extend(line)
                if len(block) > _MAX_FRONTMATTER_BYTES:
                    return None
    except (OSError, UnicodeError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------
# 最小 YAML 子集
# ---------------------------------------------------------------------------
def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _parse_mapping(block: str) -> dict[str, str]:
    lines = block.splitlines()
    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line[:1].isspace():
            index += 1
            continue
        match = _KEY_RE.match(line)
        if match is None:
            index += 1
            continue
        key, rest = match.group(1), match.group(2)
        value, index = _parse_value(lines, index, rest, _indent(line))
        # 只保留技能元数据，其它键（license、metadata 等）仍然跳过其值
        if key in {"name", "description"}:
            values[key] = value
    return values


def _parse_value(lines: list[str], index: int, rest: str, parent_indent: int) -> tuple[str, int]:
    text = rest.strip()
    if text.startswith(("|", ">")):
        return _parse_block_scalar(lines, index, text, parent_indent)
    if text.startswith(('"', "'")):
        return _parse_quoted_scalar(lines, index, text)
    return _parse_plain_scalar(lines, index, text, parent_indent)


def _parse_plain_scalar(
    lines: list[str],
    index: int,
    text: str,
    parent_indent: int,
) -> tuple[str, int]:
    parts: list[str] = []
    initial = _strip_comment(text)
    if initial:
        parts.append(initial)

    cursor = index + 1
    while cursor < len(lines):
        line = lines[cursor]
        if not line.strip():
            # 空行只有在后面还跟着更深缩进的内容时，才属于当前标量
            lookahead = cursor + 1
            while lookahead < len(lines) and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < len(lines) and _indent(lines[lookahead]) > parent_indent:
                parts.append("")
                cursor = lookahead
                continue
            break
        if _indent(line) <= parent_indent:
            break
        continuation = _strip_comment(line.strip())
        if continuation:
            parts.append(continuation)
        cursor += 1

    folded = ""
    for part in parts:
        if part == "":
            folded = folded.rstrip(" ") + "\n"
        else:
            if folded and not folded.endswith(("\n", " ")):
                folded += " "
            folded += part
    return folded.strip(), cursor


def _parse_quoted_scalar(lines: list[str], index: int, text: str) -> tuple[str, int]:
    quote = text[0]
    remainder = text[1:]
    closing = _find_closing_quote(remainder, quote)
    if closing is not None:
        return _decode_quoted(remainder[:closing], quote), index + 1

    chunks = [remainder]
    cursor = index + 1
    while cursor < len(lines):
        line = lines[cursor]
        closing = _find_closing_quote(line, quote)
        if closing is not None:
            chunks.append(line[:closing])
            cursor += 1
            break
        chunks.append(line)
        cursor += 1
    raw = " ".join(chunk.strip() for chunk in chunks).strip()
    return _decode_quoted(raw, quote), cursor


def _find_closing_quote(text: str, quote: str) -> int | None:
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if char == quote:
            if quote == "'" and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            return index
        index += 1
    return None


def _decode_quoted(raw: str, quote: str) -> str:
    if quote == "'":
        return raw.replace("''", "'")
    try:
        value = json.loads(f'"{raw}"')
    except ValueError:
        return raw.replace('\\"', '"')
    return value if isinstance(value, str) else raw


def _parse_block_scalar(
    lines: list[str],
    index: int,
    header: str,
    parent_indent: int,
) -> tuple[str, int]:
    style = header[0]
    chomp = ""
    explicit_indent: int | None = None
    for char in header[1:]:
        if char in "+-":
            chomp = char
        elif char.isdigit():
            explicit_indent = int(char)

    block: list[str] = []
    cursor = index + 1
    while cursor < len(lines):
        line = lines[cursor]
        if line.strip():
            if _indent(line) <= parent_indent:
                break
            block.append(line)
        else:
            block.append("")
        cursor += 1

    trailing_blank = 0
    while block and not block[-1].strip():
        block.pop()
        trailing_blank += 1
    if not block:
        return "", cursor

    if explicit_indent is not None:
        indent = parent_indent + explicit_indent
    else:
        indent = min(_indent(line) for line in block if line.strip())
    content = [line[indent:] if len(line) >= indent else line.lstrip() for line in block]

    if style == "|":
        value = "\n".join(content)
    else:
        paragraphs: list[str] = []
        current: list[str] = []
        for line in content:
            if not line.strip():
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
            else:
                current.append(line.strip())
        if current:
            paragraphs.append(" ".join(current))
        value = "\n".join(paragraphs)

    value = value.rstrip("\n")
    if chomp == "-":
        return value, cursor
    if chomp == "+":
        return value + "\n" + "\n" * trailing_blank, cursor
    return value + "\n", cursor


def _strip_comment(value: str) -> str:
    """去掉普通标量中的 YAML 行内注释（``#`` 前必须有空白）。"""
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
        index += 1
    return value.strip()


__all__ = [
    "parse_frontmatter",
    "read_frontmatter_file",
    "split_frontmatter",
]

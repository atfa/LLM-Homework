"""10 个文件系统工具（全部限制在 :class:`Sandbox` 内）与 getCwd。"""

from __future__ import annotations

import mimetypes
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ....domain.errors import ToolError
from ....domain.tools import ToolDefinition
from ....ports.tools import ToolHandler
from .sandbox import MAX_LIST_ENTRIES, MAX_READ_BYTES, Sandbox, decode_text, format_size, read_head

# ---------------------------------------------------------------------------
# 工具定义（JSON Schema，给模型看）
# ---------------------------------------------------------------------------
GET_CWD = ToolDefinition(
    name="getCwd",
    description="获取当前工作目录（也是所有文件工具允许访问的根目录）",
    parameters={"type": "object", "properties": {}, "required": []},
)

LIST_DIR = ToolDefinition(
    name="listDir",
    description="列出指定目录下的所有文件和子目录（含隐藏项、类型和大小），不会递归",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": '相对工作目录的路径，默认为当前目录 "."；如 "docs" 或 "./src"',
            }
        },
        "required": [],
    },
)

GET_FILE_TYPE = ToolDefinition(
    name="getFileType",
    description="获取指定文件的类型：扩展名、MIME 类型、内容属于纯文本还是二进制，以及大小",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": '文件路径，例如 "note.txt"'}},
        "required": ["path"],
    },
)

READ_FILE = ToolDefinition(
    name="readFile",
    description="读取指定纯文本文件的完整内容（UTF-8/GB18030）。二进制文件或超过 256KB 的文件会被拒绝或截断",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": '纯文本文件路径，例如 "note.txt"'}},
        "required": ["path"],
    },
)

CREATE_FILE = ToolDefinition(
    name="createFile",
    description="在指定目录下按文件名新建一个纯文本文件；文件已存在时报错，不会覆盖",
    parameters={
        "type": "object",
        "properties": {
            "directory": {"type": "string", "description": '目标目录，必须已存在，如 "." 或 "notes"'},
            "filename": {"type": "string", "description": '新文件名（不含路径分隔符），如 "todo.txt"'},
            "content": {"type": "string", "description": "初始内容，可为空", "default": ""},
        },
        "required": ["directory", "filename"],
    },
)

EDIT_FILE = ToolDefinition(
    name="editFile",
    description="编辑已存在的纯文本文件：overwrite 整体覆盖，append 追加到末尾。不会创建新文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": '要编辑的纯文本文件路径，如 "note.txt"'},
            "content": {"type": "string", "description": "写入的完整内容（overwrite）或追加内容（append）"},
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "写入方式，默认 overwrite（整体替换）",
                "default": "overwrite",
            },
        },
        "required": ["path", "content"],
    },
)

CREATE_DIR = ToolDefinition(
    name="createDir",
    description="创建新目录；路径可以是多级（如 a/b/c），中间目录会自动创建。已存在时报错",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "要创建的目录路径，相对工作目录，如 notes/2026"}},
        "required": ["path"],
    },
)

DELETE_DIR = ToolDefinition(
    name="deleteDir",
    description="删除目录，支持非空目录（需 recursive=true 才会连内容一起删）；不可恢复，不能删工作目录本身",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的目录路径，相对工作目录"},
            "recursive": {
                "type": "boolean",
                "description": "目录非空时必须设为 true，表示连同内部所有文件和子目录一起删除",
                "default": False,
            },
        },
        "required": ["path"],
    },
)

DELETE_FILE = ToolDefinition(
    name="deleteFile",
    description="删除指定的单个文件（不可恢复）；目录请用 deleteDir",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "要删除的文件路径，相对工作目录"}},
        "required": ["path"],
    },
)

RENAME_PATH = ToolDefinition(
    name="renamePath",
    description="重命名文件或目录（原地改名，不移动位置，也不支持移动跨目录）；目标名已存在时报错，不会覆盖",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要重命名的文件或目录路径，如 old.txt 或 docs/old"},
            "new_name": {
                "type": "string",
                "description": "新名称，只能是名字本身，不能包含路径分隔符（不会改变所在目录）",
            },
        },
        "required": ["path", "new_name"],
    },
)


# ---------------------------------------------------------------------------
# 参数校验辅助
# ---------------------------------------------------------------------------
def _require_path(arguments: Mapping[str, Any], key: str = "path") -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} 必须是非空字符串")
    return value


def _optional_path(arguments: Mapping[str, Any], key: str = "path") -> str:
    value = arguments.get(key)
    if value is None:
        return "."
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} 必须是字符串")
    return value


def _require_content(arguments: Mapping[str, Any]) -> str:
    value = arguments.get("content", "")
    if not isinstance(value, str):
        raise ToolError("content 必须是字符串")
    return value


def _require_filename(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError("filename 必须是非空字符串")
    name = value.strip()
    if name in {".", ".."} or "/" in name or "\\" in name or (os.sep in name) or (os.altsep and os.altsep in name):
        raise ToolError(f"filename 只能是名称本身，不能包含路径分隔符：{name}")
    return name


def _count_entries(path: Path) -> int:
    total = 0
    for _root, dirs, files in os.walk(path):
        total += len(dirs) + len(files)
    return total


def _write_text(target: Path, content: str, *, mode: str) -> int:
    try:
        with target.open(mode, encoding="utf-8", newline="") as handle:
            written = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ToolError(f"写入失败 {target.name}：{exc}") from exc
    return written


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
def get_cwd(sandbox: Sandbox, _: Mapping[str, Any]) -> str:
    return f"当前工作目录（沙箱根）：{sandbox.root}"


def list_dir(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    target = sandbox.resolve(_optional_path(arguments))
    display = sandbox.display(target)
    if not os.path.lexists(target):
        raise ToolError(f"目录不存在：{display}")
    if not target.is_dir():
        raise ToolError(f"不是目录：{display}（查看文件请用 getFileType / readFile）")

    try:
        with os.scandir(target) as iterator:
            entries = list(iterator)
    except OSError as exc:
        raise ToolError(f"读取目录失败 {display}：{exc}") from exc

    def sort_key(entry: os.DirEntry[str]) -> tuple[bool, str]:
        return (not entry.is_dir(follow_symlinks=False), entry.name.lower())

    entries.sort(key=sort_key)
    total = len(entries)
    visible = entries[:MAX_LIST_ENTRIES]

    lines = [f"目录：{display}（共 {total} 项）"]
    for entry in visible:
        try:
            if entry.is_symlink():
                lines.append(f"  [LINK] {entry.name} -> {os.readlink(entry.path)}")
            elif entry.is_dir(follow_symlinks=False):
                lines.append(f"  [DIR ] {entry.name}/")
            else:
                size = entry.stat(follow_symlinks=False).st_size
                lines.append(f"  [FILE] {format_size(size):>9}  {entry.name}")
        except OSError as exc:
            lines.append(f"  [????] {entry.name}（无法读取属性：{exc}）")

    if total > len(visible):
        lines.append(f"  ...（还有 {total - len(visible)} 项未显示，可缩小目录范围）")
    if total == 0:
        lines.append("  （空目录）")
    return "\n".join(lines)


def get_file_type(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    target = sandbox.resolve(_require_path(arguments))
    display = sandbox.display(target)
    if not os.path.lexists(target):
        raise ToolError(f"路径不存在：{display}")
    if target.is_dir():
        return f"路径：{display}\n类型：目录"
    if not target.is_file():
        raise ToolError(f"不是普通文件：{display}")

    size = target.stat().st_size
    suffix = target.suffix.lower()
    mime, _encoding = mimetypes.guess_type(target.name)
    decoded = decode_text(read_head(target))
    if decoded is None:
        content_kind = "二进制或非 UTF-8/GB18030 文本"
    else:
        content_kind = f"纯文本（{decoded[1]}）"

    return "\n".join(
        [
            f"路径：{display}",
            f"大小：{format_size(size)}（{size} 字节）",
            f"扩展名：{suffix or '（无）'}",
            f"MIME：{mime or '（未知）'}",
            f"内容类型：{content_kind}",
        ]
    )


def read_file(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    target = sandbox.resolve(_require_path(arguments))
    display = sandbox.display(target)
    sandbox.ensure_text_file(target)

    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ToolError(
            f"文件过大：{display}（{format_size(size)}，上限 {format_size(MAX_READ_BYTES)}）。"
            "请改用命令行工具分段读取。"
        )
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ToolError(f"读取文件失败 {display}：{exc}") from exc

    decoded = decode_text(data)
    if decoded is None:
        raise ToolError(f"不是纯文本文件（UTF-8/GB18030）：{display}")
    text, _encoding = decoded
    return text if text else "（空文件）"


def create_file(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    directory = sandbox.resolve(_require_path(arguments, "directory"))
    if not directory.is_dir():
        raise ToolError(f"目标目录不存在或不是目录：{sandbox.display(directory)}")
    filename = _require_filename(arguments.get("filename"))
    content = _require_content(arguments)

    target = directory / filename
    if os.path.lexists(target):
        raise ToolError(f"已存在，不会覆盖：{sandbox.display(target)}")

    _write_text(target, content, mode="x")
    return f"已创建文件：{sandbox.display(target)}（{format_size(len(content.encode('utf-8')))}）"


def edit_file(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    target = sandbox.resolve(_require_path(arguments))
    display = sandbox.display(target)
    sandbox.ensure_text_file(target)

    content = _require_content(arguments)
    mode = arguments.get("mode", "overwrite")
    if mode not in ("overwrite", "append"):
        raise ToolError(f"mode 只能是 overwrite 或 append，收到 {mode!r}")

    _write_text(target, content, mode="w" if mode == "overwrite" else "a")
    size = target.stat().st_size
    action = "覆盖写入" if mode == "overwrite" else "追加写入"
    return f"已{action}：{display}（当前 {format_size(size)}）"


def create_dir(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    target = sandbox.resolve(_require_path(arguments))
    display = sandbox.display(target)
    if os.path.lexists(target):
        raise ToolError(f"已存在，不会覆盖：{display}")
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ToolError(f"已存在，不会覆盖：{display}") from exc
    except OSError as exc:
        raise ToolError(f"创建目录失败 {display}：{exc}") from exc
    return f"已创建目录：{display}"


def delete_dir(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    target = sandbox.resolve(_require_path(arguments))
    display = sandbox.display(target)
    if sandbox.is_root(target):
        raise ToolError(f"不能删除工作目录本身：{sandbox.root}（可删它的子目录）")
    if not os.path.lexists(target):
        raise ToolError(f"目录不存在：{display}")
    if not target.is_dir() or target.is_symlink():
        raise ToolError(f"不是普通目录：{display}（删除文件请用 deleteFile）")

    recursive = arguments.get("recursive", False)
    if not isinstance(recursive, bool):
        raise ToolError(f"recursive 必须是布尔值，收到 {recursive!r}")

    total = _count_entries(target)
    if total and not recursive:
        raise ToolError(f"目录非空（{total} 项），确认要连同内容一起删除请传 recursive=true：{display}")

    try:
        if recursive:
            shutil.rmtree(target)
        else:
            os.rmdir(target)
    except OSError as exc:
        raise ToolError(f"删除目录失败 {display}：{exc}") from exc
    return f"已删除目录：{display}（连同 {total} 项内容）"


def delete_file(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    target = sandbox.resolve(_require_path(arguments))
    display = sandbox.display(target)
    if not os.path.lexists(target):
        raise ToolError(f"文件不存在：{display}")
    if target.is_dir():
        raise ToolError(f"是目录，不是文件：{display}（删除目录请用 deleteDir）")

    size = target.stat().st_size if not target.is_symlink() else 0
    try:
        os.remove(target)
    except OSError as exc:
        raise ToolError(f"删除文件失败 {display}：{exc}") from exc
    return f"已删除文件：{display}（{format_size(size)}）"


def rename_path(sandbox: Sandbox, arguments: Mapping[str, Any]) -> str:
    source = sandbox.resolve(_require_path(arguments))
    if sandbox.is_root(source):
        raise ToolError(f"不能重命名工作目录本身：{sandbox.root}")
    if not os.path.lexists(source):
        raise ToolError(f"路径不存在：{sandbox.display(source)}")

    new_name = arguments.get("new_name")
    if not isinstance(new_name, str) or not new_name.strip():
        raise ToolError("new_name 必须是非空字符串")
    new_name = new_name.strip()
    if (
        new_name in {".", ".."}
        or "/" in new_name
        or "\\" in new_name
        or (os.sep in new_name)
        or (os.altsep and os.altsep in new_name)
    ):
        raise ToolError(f"new_name 只能是名称本身，不能包含路径分隔符：{new_name}")
    if new_name == source.name:
        raise ToolError(f"新旧名称相同：{new_name}")

    # 只改同目录下的名字，目标路径由「原父目录 + 新名称」拼出，天然不会跑出沙箱
    target = source.parent / new_name
    if os.path.lexists(target):
        try:
            same_file = os.path.samefile(source, target)
        except OSError:
            same_file = False
        if not same_file:
            raise ToolError(f"目标已存在，拒绝覆盖：{sandbox.display(target)}")

    if source.is_symlink():
        kind = "符号链接"
    elif source.is_dir():
        kind = "目录"
    else:
        kind = "文件"

    try:
        os.rename(source, target)
    except OSError as exc:
        raise ToolError(f"重命名失败 {sandbox.display(source)}：{exc}") from exc
    return f"已重命名{kind}：{sandbox.display(source)} → {sandbox.display(target)}"


SandboxTool = Callable[[Sandbox, Mapping[str, Any]], str]


_HANDLERS: tuple[tuple[ToolDefinition, SandboxTool], ...] = (
    (GET_CWD, get_cwd),
    (LIST_DIR, list_dir),
    (GET_FILE_TYPE, get_file_type),
    (READ_FILE, read_file),
    (CREATE_FILE, create_file),
    (EDIT_FILE, edit_file),
    (CREATE_DIR, create_dir),
    (DELETE_DIR, delete_dir),
    (DELETE_FILE, delete_file),
    (RENAME_PATH, rename_path),
)


def file_tools(sandbox: Sandbox) -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    """把沙箱绑定到每个处理器，返回可直接注册的 (定义, 处理器) 列表。"""
    return tuple((definition, _bind(handler, sandbox)) for definition, handler in _HANDLERS)


def _bind(handler: SandboxTool, sandbox: Sandbox) -> ToolHandler:
    """把 ``f(sandbox, args)`` 绑定成注册表要求的 ``f(args)``。"""

    def invoke(arguments: Mapping[str, Any]) -> str:
        return handler(sandbox, arguments)

    return invoke

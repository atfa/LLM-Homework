"""按需加载 Skill 正文与附加文件。

安全边界：所有读取最终都必须落在**当前 Skill 自己的目录**内。支持三种
``filename`` 写法：

1. 相对于 Skill 目录，如 ``references/guide.md``；
2. 相对于项目根目录，如 ``.agents/skills/pdf/references/guide.md``；
3. 绝对路径。

先做精确路径匹配；只给了文件名时，再在 Skill 目录内递归查找。同名文件
不唯一时返回候选路径，由模型选择，而不是替它猜。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from ...domain.errors import SandboxViolation, ToolError
from .catalog import Skill

MAX_SKILL_FILE_BYTES = 2 * 1024 * 1024
"""单个 Skill 文件的安全上限；超出时明确报错，避免把超大文件读进内存。"""


def load_skill_content(skill: Skill) -> str:
    """读取 Skill 的 ``SKILL.md`` 全文。"""
    if not skill.skill_file.is_file():
        raise ToolError(f"Skill 文件不存在：{skill.name}/SKILL.md")
    return _read_text(skill.skill_file, display=f".agents/skills/{skill.directory.name}/SKILL.md")


def load_skill_attachment(skill: Skill, filename: str, project_root: Path) -> str:
    """读取 Skill 目录内的附加文件；``filename`` 已在工具层校验为非空字符串。"""
    raw = filename.strip()
    if not raw:
        raise ToolError("filename 必须是非空字符串")
    if "\x00" in raw:
        raise SandboxViolation("文件名包含非法字符 NUL")

    skill_root = Path(os.path.realpath(skill.directory))
    project = Path(os.path.realpath(project_root))
    bare_name = _is_bare_name(raw)

    # 统一分隔符，兼容模型在 Windows 风格路径里使用反斜杠
    normalized = raw.replace("\\", "/")
    candidate = Path(os.path.expanduser(normalized))

    candidates: list[tuple[str, Path]] = []
    if candidate.is_absolute():
        candidates.append(("absolute", candidate))
    else:
        # 优先 Skill 目录相对路径，其次项目相对路径
        candidates.append(("skill", skill_root / candidate))
        candidates.append(("project", project / candidate))

    for kind, path in candidates:
        if not os.path.lexists(path):
            continue
        resolved = Path(os.path.realpath(path))
        if not _is_within(resolved, skill_root):
            if kind == "project" and bare_name:
                # 项目根下恰好有同名文件，但它不在当前 Skill 内：不是合法目标，
                # 继续按「只给文件名」的规则在 Skill 目录内递归查找。
                continue
            raise SandboxViolation(f"拒绝读取 Skill 目录之外的文件：{raw}（只允许访问 {skill.name}/ 内部）")
        if resolved.is_dir():
            raise ToolError(f"是目录，不是文件：{_display(resolved, project, skill_root)}")
        if not resolved.is_file():
            raise ToolError(f"不是普通文件：{_display(resolved, project, skill_root)}")
        return _read_text(resolved, display=_display(resolved, project, skill_root))

    if not bare_name:
        tried = "、".join(_display(path, project, skill_root) for _kind, path in candidates)
        raise ToolError(
            f"文件不存在：{raw}（已检查：{tried}）。请传入 Skill 目录内的相对路径。" + _available_hint(skill_root)
        )

    matches = _recursive_matches(skill_root, raw)
    if len(matches) == 1:
        return _read_text(matches[0], display=_display(matches[0], project, skill_root))

    if len(matches) > 1:
        lines = [f"在 Skill `{skill.name}` 中找到多个名为 {raw} 的文件，请选择要读取的路径（filename）："]
        lines.extend(f"- {_display(path, project, skill_root)}" for path in matches)
        lines.append("请从中选择一个路径，再次调用 loadSkill。")
        return "\n".join(lines)

    raise ToolError(f"在 Skill `{skill.name}` 中找不到文件：{raw}{_available_hint(skill_root)}")


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
def _available_hint(root: Path) -> str:
    available = _list_files(root)
    if not available:
        return ""
    return "\n该 Skill 目录内的文件：\n" + "\n".join(f"- {path}" for path in available)


def _is_bare_name(raw: str) -> bool:
    return raw not in {".", ".."} and "/" not in raw and "\\" not in raw


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _recursive_matches(root: Path, filename: str) -> list[Path]:
    """在 Skill 目录内递归查找同名普通文件（不跟随越界的软链接）。"""
    matches: dict[str, Path] = {}
    for current, _dirs, files in os.walk(root, followlinks=False):
        if filename not in files:
            continue
        resolved = Path(os.path.realpath(Path(current) / filename))
        if not _is_within(resolved, root) or not resolved.is_file():
            continue
        matches[resolved.as_posix()] = resolved
    return [matches[key] for key in sorted(matches)]


def _list_files(root: Path) -> list[str]:
    entries: list[str] = []
    for current, _dirs, files in os.walk(root, followlinks=False):
        for name in sorted(files):
            path = Path(os.path.realpath(Path(current) / name))
            if _is_within(path, root) and path.is_file():
                entries.append(path.relative_to(root).as_posix())
    return entries[:40]


def _read_text(path: Path, *, display: str) -> str:
    # 延迟导入：tools/__init__ 会导入 catalog，而 catalog 又导入本包；
    # 在模块顶层导入 tools.file_ops.sandbox 会形成循环依赖。
    from ..tools.file_ops.sandbox import decode_text

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ToolError(f"读取文件失败 {display}：{exc}") from exc
    if size > MAX_SKILL_FILE_BYTES:
        raise ToolError(f"文件过大：{display}（超过上限 {MAX_SKILL_FILE_BYTES // 1024} KB）")

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"读取文件失败 {display}：{exc}") from exc

    decoded = decode_text(data)
    if decoded is None:
        raise ToolError(f"不是纯文本文件（UTF-8/GB18030）：{display}")
    text, _encoding = decoded
    return text if text else "（空文件）"


def _display(path: Path, project_root: Path, skill_root: Path) -> str:
    for root in (project_root, skill_root):
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(path)


def list_skill_paths(skill: Skill) -> Iterable[str]:
    """展示用：列出 Skill 目录内的文件（相对路径）。"""
    return _list_files(Path(os.path.realpath(skill.directory)))


__all__ = [
    "MAX_SKILL_FILE_BYTES",
    "list_skill_paths",
    "load_skill_attachment",
    "load_skill_content",
]

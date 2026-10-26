"""创建 Skill：生成符合 Agent Skills 规范的 ``SKILL.md`` 并注册到目录。

``createSkill`` 只负责 Skill 入口文件；references / scripts 等辅助内容
仍然交给现有 fileOps 工具创建。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ...domain.errors import ToolError
from .catalog import SKILL_FILE, SKILLS_DIR, Skill, SkillCatalog
from .frontmatter import parse_frontmatter, read_frontmatter_file, split_frontmatter

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
MAX_DERIVED_DESCRIPTION_LENGTH = 200


def normalize_skill_name(raw: str) -> str:
    """把名称规范成小写字母、数字和连字符组成的格式。

    允许把空格、下划线转换为连字符并合并连续连字符；转换后仍含非法字符
    （例如中文、路径分隔符）时拒绝，避免创建到非预期目录。
    """
    text = raw.strip().lower()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        raise ToolError("Skill 名称不能为空")
    if len(text) > MAX_NAME_LENGTH:
        raise ToolError(f"Skill 名称不能超过 {MAX_NAME_LENGTH} 个字符：{text}")
    if not NAME_PATTERN.fullmatch(text):
        raise ToolError(f"Skill 名称只能包含小写字母、数字和连字符，且不能以连字符开头或结尾：{raw!r}")
    return text


def create_skill(catalog: SkillCatalog, name: str, skill_context: str) -> Skill:
    """创建 ``.agents/skills/<name>/SKILL.md``，成功后注册到 ``catalog``。"""
    normalized = normalize_skill_name(name)
    if not skill_context.strip():
        raise ToolError("skillContext 必须是非空字符串")

    skills_root = Path(os.path.realpath(catalog.project_root / SKILLS_DIR))
    skill_dir = skills_root / normalized
    skill_file = skill_dir / SKILL_FILE

    if catalog.get(normalized) is not None or os.path.lexists(skill_dir) or os.path.lexists(skill_file):
        raise ToolError(f"Skill 已存在，拒绝覆盖：{normalized}（{_display(skill_file, catalog.project_root)}）")

    document = build_skill_document(normalized, skill_context)
    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        with skill_file.open("x", encoding="utf-8", newline="") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        _cleanup_failed_create(skill_file, skill_dir)
        raise ToolError(f"Skill 已存在，拒绝覆盖：{normalized}") from exc
    except OSError as exc:
        _cleanup_failed_create(skill_file, skill_dir)
        raise ToolError(f"创建 Skill 失败 {normalized}：{exc}") from exc

    metadata = read_frontmatter_file(skill_file)
    if metadata is None:
        _cleanup_failed_create(skill_file, skill_dir)
        raise ToolError(f"创建的 SKILL.md 无法解析 Frontmatter：{normalized}")

    description = metadata.get("description", "").strip() or f"{normalized} skill"
    skill = Skill(
        name=normalized,
        description=description,
        directory=Path(os.path.realpath(skill_dir)),
        skill_file=Path(os.path.realpath(skill_file)),
    )
    catalog.register(skill)
    return skill


def build_skill_document(name: str, skill_context: str) -> str:
    """确保内容包含正确的 Frontmatter、name、description 与正文。

    ``skill_context`` 可以是：

    - 完整的 SKILL.md（已带 Frontmatter）：保留正文和额外 Frontmatter 字段，
      只修正/补全 name、description；
    - 只有正文：自动补上 Frontmatter，并从第一行有效文本推导 description。
    """
    context = skill_context[1:] if skill_context.startswith("\ufeff") else skill_context
    parts = split_frontmatter(context)
    if parts is None:
        if context.lstrip().startswith("---"):
            raise ToolError("skillContext 的 YAML Frontmatter 缺少结束分隔线 ---")
        body = context.strip("\n")
        description = _derive_description(body) or f"{name} skill"
        return _render_document(name, description, body)

    frontmatter, body = parts
    metadata = parse_frontmatter(context) or {}
    existing_name = metadata.get("name", "").strip()
    description = metadata.get("description", "").strip() or _derive_description(body) or f"{name} skill"

    # 已经是规范文档：原样保留，避免无谓改写用户提供的完整内容。
    if existing_name == name and metadata.get("description", "").strip():
        return context

    remaining = _remove_key_lines(frontmatter.splitlines(), "name")
    remaining = _remove_key_lines(remaining, "description")
    return _render_document(name, description, body, extra_lines=remaining)


def _render_document(name: str, description: str, body: str, extra_lines: list[str] | None = None) -> str:
    lines = [
        "---",
        f"name: {name}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
    ]
    lines.extend(extra_lines or ())
    lines.append("---")

    content = body.strip("\n")
    if content:
        return "\n".join(lines) + "\n\n" + content + "\n"
    return "\n".join(lines) + "\n"


def _derive_description(body: str) -> str:
    for line in body.splitlines():
        text = line.strip()
        if not text:
            continue
        text = text.lstrip("#").strip()
        if text and not set(text) <= set("-=*_ "):
            return text[:MAX_DERIVED_DESCRIPTION_LENGTH]
    return ""


def _remove_key_lines(lines: list[str], key: str) -> list[str]:
    """移除顶层 ``key:`` 及其缩进续行，保留其它 Frontmatter 字段。"""
    pattern = re.compile(rf"^{re.escape(key)}[ \t]*:")
    result: list[str] = []
    skipping = False
    for line in lines:
        if pattern.match(line):
            skipping = True
            continue
        if skipping:
            if not line.strip():
                continue
            if line[:1].isspace():
                continue
            skipping = False
        result.append(line)
    return result


def _cleanup_failed_create(skill_file: Path, skill_dir: Path) -> None:
    try:
        if skill_file.exists():
            skill_file.unlink()
        skill_dir.rmdir()
    except OSError:
        pass


def _display(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


__all__ = [
    "MAX_NAME_LENGTH",
    "NAME_PATTERN",
    "build_skill_document",
    "create_skill",
    "normalize_skill_name",
]

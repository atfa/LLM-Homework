"""``loadSkill`` / ``createSkill`` 工具：按需加载或创建 Agent Skills。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...domain.errors import ToolError
from ...domain.tools import ToolDefinition
from ...ports.tools import ToolHandler
from .catalog import SkillCatalog
from .creator import create_skill as create_skill_on_disk
from .loader import load_skill_attachment, load_skill_content

LOAD_SKILL = ToolDefinition(
    name="loadSkill",
    description=(
        "按需加载一个 Skill 的完整说明，或读取该 Skill 目录内的附加文件。"
        "只传 name 时读取该 Skill 的 SKILL.md 全文；读取 references、scripts 等附加文件时再传 filename。"
        "filename 支持 Skill 目录相对路径、项目相对路径和绝对路径；只给文件名时会自动在 Skill 目录内递归查找。"
        "所有读取都被限制在该 Skill 自己的目录内。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill 名称，必须与 System Prompt 技能列表中的名称完全一致",
            },
            "filename": {
                "type": "string",
                "description": (
                    '可选：要读取的附加文件，如 "references/guide.md"、'
                    '".agents/skills/pdf/references/guide.md" 或绝对路径；只给文件名时会递归查找'
                ),
            },
        },
        "required": ["name"],
    },
)

CREATE_SKILL = ToolDefinition(
    name="createSkill",
    description=(
        "根据用户要求创建一个新的 Agent Skill：在项目根目录的 "
        ".agents/skills/<name>/SKILL.md 写入技能内容。"
        "name 会被规范为小写字母、数字和连字符组成的格式；"
        "skillContext 是 SKILL.md 的完整内容，可以带 Frontmatter，也可以只给正文，"
        "工具会确保 Frontmatter 中的 name 与参数一致，并补全 description。"
        "如果目标 Skill 已存在会返回明确提示，不会覆盖原内容。"
        "需要 references/、scripts/ 等目录或辅助文件时，请在创建成功后继续调用 "
        "createDir / createFile / editFile 等 fileOps 工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": 'Skill 名称，例如 "pdf-forms"；只能包含小写字母、数字和连字符',
            },
            "skillContext": {
                "type": "string",
                "description": (
                    "要写入 SKILL.md 的完整内容。可以是带 YAML Frontmatter 的完整文档，"
                    "也可以只是正文（工具会自动补全 Frontmatter 和 description）"
                ),
            },
        },
        "required": ["name", "skillContext"],
    },
)


def load_skill(catalog: SkillCatalog, arguments: Mapping[str, Any]) -> str:
    name = _require_text(arguments, "name")
    skill = catalog.get(name)
    if skill is None:
        available = "、".join(f"`{item}`" for item in catalog.names) or "（当前没有可用 Skill）"
        raise ToolError(f"未找到 Skill：{name}。可用 Skill：{available}")

    filename = _optional_text(arguments, "filename")
    if filename is None:
        return load_skill_content(skill)
    return load_skill_attachment(skill, filename, catalog.project_root)


def create_skill(catalog: SkillCatalog, arguments: Mapping[str, Any]) -> str:
    name = _require_text(arguments, "name")
    skill_context = _require_content(arguments, "skillContext")
    skill = create_skill_on_disk(catalog, name, skill_context)
    return (
        f"已创建 Skill：`{skill.name}`\n"
        f"文件：{_relative_display(skill.skill_file, catalog.project_root)}\n"
        f"描述：{skill.description}\n"
        "当前会话的可用 Skill 列表已更新，可以直接使用 loadSkill 加载它。"
        "如需 references/、scripts/ 等目录或辅助文件，请继续调用 createDir / createFile 等文件工具。"
    )


def _require_text(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} 必须是非空字符串")
    return value.strip()


def _require_content(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} 必须是非空字符串")
    return value


def _optional_text(arguments: Mapping[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"{key} 必须是字符串")
    text = value.strip()
    return text or None


def _relative_display(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def skill_tools(catalog: SkillCatalog) -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    """把 Skill 目录绑定到 ``loadSkill`` / ``createSkill`` 处理器。"""

    def load_handler(arguments: Mapping[str, Any]) -> str:
        return load_skill(catalog, arguments)

    def create_handler(arguments: Mapping[str, Any]) -> str:
        return create_skill(catalog, arguments)

    return ((LOAD_SKILL, load_handler), (CREATE_SKILL, create_handler))


__all__ = ["CREATE_SKILL", "LOAD_SKILL", "create_skill", "load_skill", "skill_tools"]

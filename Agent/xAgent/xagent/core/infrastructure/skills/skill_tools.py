"""``loadSkill`` 工具：把 Skill 正文或附加文件按需送进对话上下文。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.errors import ToolError
from ...domain.tools import ToolDefinition
from ...ports.tools import ToolHandler
from .catalog import SkillCatalog
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


def _require_text(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} 必须是非空字符串")
    return value.strip()


def _optional_text(arguments: Mapping[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"{key} 必须是字符串")
    text = value.strip()
    return text or None


def skill_tools(catalog: SkillCatalog) -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    """把 Skill 目录绑定到 ``loadSkill`` 处理器。"""

    def handler(arguments: Mapping[str, Any]) -> str:
        return load_skill(catalog, arguments)

    return ((LOAD_SKILL, handler),)


__all__ = ["LOAD_SKILL", "load_skill", "skill_tools"]

"""把 Skill 摘要整理成 System Prompt 片段。"""

from __future__ import annotations

from collections.abc import Iterable

from .catalog import Skill

SKILLS_PROMPT_HEADER = "You have access to the following skills:"
SKILLS_PROMPT_FOOTER = (
    "Use the `loadSkill` tool to load the full instructions of a skill when needed. "
    "You can also load additional files referenced by a skill."
)


def render_skills_prompt(skills: Iterable[Skill]) -> str:
    """渲染技能列表；没有技能时返回空串，表示不修改 System Prompt。"""
    items = list(skills)
    if not items:
        return ""

    lines = [SKILLS_PROMPT_HEADER, ""]
    for skill in items:
        description = " ".join(skill.description.split())
        lines.append(f"- `{skill.name}`: {description}")
    lines.extend(("", SKILLS_PROMPT_FOOTER))
    return "\n".join(lines)


def compose_system_prompt(base_prompt: str, skills: Iterable[Skill]) -> str:
    """在原有 System Prompt 后追加技能摘要，保持原有内容不变。"""
    block = render_skills_prompt(skills)
    if not block:
        return base_prompt
    base = base_prompt.rstrip()
    return f"{base}\n\n{block}" if base else block


__all__ = ["SKILLS_PROMPT_FOOTER", "SKILLS_PROMPT_HEADER", "compose_system_prompt", "render_skills_prompt"]

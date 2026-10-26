"""Agent Skills 动态加载：发现、System Prompt 摘要与 ``loadSkill`` 工具。

初始化只读取 ``.agents/skills/<name>/SKILL.md`` 的 Frontmatter；
完整正文和附加文件只有在模型调用 ``loadSkill`` 时才按需读取。
"""

from .catalog import SKILL_FILE, SKILLS_DIR, Skill, SkillCatalog
from .loader import list_skill_paths, load_skill_attachment, load_skill_content
from .prompt import compose_system_prompt, render_skills_prompt
from .skill_tools import LOAD_SKILL, load_skill, skill_tools

__all__ = [
    "LOAD_SKILL",
    "SKILLS_DIR",
    "SKILL_FILE",
    "Skill",
    "SkillCatalog",
    "compose_system_prompt",
    "list_skill_paths",
    "load_skill",
    "load_skill_attachment",
    "load_skill_content",
    "render_skills_prompt",
    "skill_tools",
]

"""Agent Skills 动态加载：发现、System Prompt 摘要与 loadSkill / createSkill。

初始化只读取 ``.agents/skills/<name>/SKILL.md`` 的 Frontmatter；
完整正文和附加文件只有在模型调用 ``loadSkill`` 时才按需读取。
``createSkill`` 可在运行期创建新 Skill 并刷新当前会话的技能列表。
"""

from .catalog import SKILL_FILE, SKILLS_DIR, Skill, SkillCatalog
from .creator import build_skill_document, create_skill, normalize_skill_name
from .loader import list_skill_paths, load_skill_attachment, load_skill_content
from .prompt import compose_system_prompt, render_skills_prompt
from .skill_tools import CREATE_SKILL, LOAD_SKILL, load_skill, skill_tools

__all__ = [
    "CREATE_SKILL",
    "LOAD_SKILL",
    "SKILLS_DIR",
    "SKILL_FILE",
    "Skill",
    "SkillCatalog",
    "build_skill_document",
    "compose_system_prompt",
    "create_skill",
    "list_skill_paths",
    "load_skill",
    "load_skill_attachment",
    "load_skill_content",
    "normalize_skill_name",
    "render_skills_prompt",
    "skill_tools",
]

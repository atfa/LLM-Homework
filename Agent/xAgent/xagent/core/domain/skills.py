"""会话内已加载的 Skill 正文。

Skill 正文只在第一次 ``loadSkill`` 时读取一次。之后由本对象保存，并注入
System Prompt，使后续轮次可以直接复用，而不是让模型重新加载同一技能。
"""

from __future__ import annotations

from dataclasses import dataclass, field

LOADED_SKILLS_HEADER = (
    "# Loaded skills\n\n"
    "The following skills are already loaded in this conversation. "
    "Use their instructions directly; do NOT call `loadSkill` for them again.\n"
)
"""已加载技能段落的标题。

明确告诉模型「正文已经在上下文里」，避免它仅凭技能列表再次调用
``loadSkill``，重复消耗 token。
"""


@dataclass
class LoadedSkills:
    """按加载顺序保存 ``Skill 名称 → SKILL.md 正文``。"""

    _entries: dict[str, str] = field(default_factory=dict)

    def __contains__(self, name: object) -> bool:
        return name in self._entries

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def get(self, name: str) -> str | None:
        return self._entries.get(name)

    def add(self, name: str, body: str) -> None:
        """记录一次成功加载；重复添加不会覆盖首次正文。"""
        self._entries.setdefault(name, body)

    def clear(self) -> None:
        self._entries.clear()

    def render(self) -> str:
        """渲染成可直接追加到 System Prompt 的文本；没有技能时返回空串。"""
        if not self._entries:
            return ""
        blocks = [LOADED_SKILLS_HEADER.rstrip()]
        for name, body in self._entries.items():
            blocks.append(f"## {name}\n\n{body.strip()}")
        return "\n\n".join(blocks)


def merge_system_prompt(base_prompt: str, rendered_skills: str) -> str:
    """把已加载技能段落追加到基础 System Prompt 之后。"""
    if not rendered_skills:
        return base_prompt
    base = base_prompt.rstrip()
    return f"{base}\n\n{rendered_skills}" if base else rendered_skills


__all__ = ["LOADED_SKILLS_HEADER", "LoadedSkills", "merge_system_prompt"]

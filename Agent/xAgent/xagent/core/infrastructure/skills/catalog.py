"""Skill 发现与目录模型。

约定（Agent Skills 规范）：

```text
<project-root>/.agents/skills/<skill-name>/SKILL.md
```

初始化时只读取每个 ``SKILL.md`` 的 YAML Frontmatter，拿到 ``name`` 与
``description``；正文只会在模型调用 ``loadSkill`` 时按需加载。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .frontmatter import read_frontmatter_file

SKILLS_DIR = Path(".agents") / "skills"
"""相对项目根的 Skill 根目录。"""

SKILL_FILE = "SKILL.md"
"""每个 Skill 目录中的入口文件名。"""


def _resolve(path: str | Path) -> Path:
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.realpath(expanded))


@dataclass(frozen=True, slots=True)
class Skill:
    """一个已发现 Skill 的元数据与目录位置（不含正文）。"""

    name: str
    description: str
    directory: Path
    skill_file: Path

    @property
    def relative_directory(self) -> Path:
        """返回相对于 ``.agents/skills`` 的目录名（仅用于展示）。"""
        return Path(self.directory.name)


class SkillCatalog:
    """初始化阶段扫描得到的 Skill 集合。

    ``project_root`` 是程序启动时的工作目录，用于解析项目相对路径；
    它同时决定去哪个 ``.agents/skills`` 目录扫描。
    """

    def __init__(self, project_root: str | Path, skills: Iterable[Skill] = ()) -> None:
        self.project_root = _resolve(project_root)

        entries: list[Skill] = []
        seen: set[str] = set()
        for skill in skills:
            if skill.name in seen:
                # 同名 Skill 以排序后的第一个为准，避免注册表出现歧义
                continue
            seen.add(skill.name)
            entries.append(skill)

        self._skills = tuple(entries)
        self._by_name = {skill.name: skill for skill in self._skills}

    # ---- 发现 ---------------------------------------------------------
    @classmethod
    def discover(cls, project_root: str | Path | None = None) -> SkillCatalog:
        """扫描 ``<project_root>/.agents/skills/`` 下的直接子目录。

        没有目录、缺少 ``SKILL.md``、Frontmatter 缺字段或无法解码的 Skill
        会被安静跳过；单个坏 Skill 不应阻塞 xAgent 启动。
        """
        root = _resolve(project_root if project_root is not None else Path.cwd())
        skills_root = root / SKILLS_DIR
        if not skills_root.is_dir():
            return cls(root)

        try:
            children = sorted(skills_root.iterdir(), key=lambda path: (path.name.lower(), path.name))
        except OSError:
            return cls(root)

        discovered: list[Skill] = []
        for child in children:
            if not child.is_dir():
                continue
            skill_file = child / SKILL_FILE
            if not skill_file.is_file():
                continue
            metadata = read_frontmatter_file(skill_file)
            if metadata is None:
                continue
            name = metadata.get("name", "").strip()
            description = metadata.get("description", "").strip()
            if not name or not description:
                continue

            directory = Path(os.path.realpath(child))
            resolved_file = directory / SKILL_FILE
            if not resolved_file.is_file():
                continue
            discovered.append(
                Skill(
                    name=name,
                    description=description,
                    directory=directory,
                    skill_file=resolved_file,
                )
            )
        return cls(root, discovered)

    # ---- 查询 ---------------------------------------------------------
    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

    @property
    def skills(self) -> tuple[Skill, ...]:
        return self._skills

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self._skills)

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills)

    def __len__(self) -> int:
        return len(self._skills)


__all__ = ["SKILLS_DIR", "SKILL_FILE", "Skill", "SkillCatalog"]

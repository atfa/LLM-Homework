"""工具目录：装配默认工具（时间 2 + 文件 10 + 联网搜索 1 + 网页抓取 1 + 命令行 1 + Skill 2）。"""

from __future__ import annotations

from pathlib import Path

from ...domain.skills import LoadedSkills
from ..config import SearchSettings
from ..skills import SkillCatalog, skill_tools
from .date_time import datetime_tools
from .file_ops import Sandbox, file_tools
from .registry import ToolRegistryImpl
from .run_cli import run_cli_tools
from .web_fetch import fetch_web_tools
from .web_search import QianfanSearchClient, web_search_tools


def build_tool_registry(
    workspace: str | Path,
    *,
    search: SearchSettings | None = None,
    skills: SkillCatalog | None = None,
    loaded_skills: LoadedSkills | None = None,
) -> ToolRegistryImpl:
    """构建绑定到指定工作目录的默认工具集。

    ``search`` 为空表示没有配置百度千帆搜索；此时 ``webSearch`` 仍会注册，
    但调用时会返回「未配置 API Key」的可读提示。

    ``skills`` 为初始化阶段发现的 Skill 目录；省略时以 ``workspace`` 为项目根
    扫描 ``.agents/skills/``。``loadSkill`` 始终注册，没有 Skill 时返回可读提示。

    ``loaded_skills`` 是会话级已加载技能状态；与 ``ChatSession`` 共享同一实例
    后，加载过的正文才能跨轮注入 System Prompt，并被后续 ``loadSkill`` 去重。
    """
    sandbox = Sandbox(workspace)
    settings = search or SearchSettings()
    skill_catalog = skills if skills is not None else SkillCatalog.discover(workspace)
    client = QianfanSearchClient(
        api_key=settings.api_key,
        endpoint=settings.endpoint,
        search_source=settings.search_source,
        top_k=settings.top_k,
        timeout_s=settings.timeout_s,
        recency=settings.recency,
        model=settings.model,
    )
    registry = ToolRegistryImpl()
    for definition, handler in (
        *datetime_tools(),
        *file_tools(sandbox),
        *web_search_tools(client),
        *fetch_web_tools(),
        *run_cli_tools(workspace),
        *skill_tools(skill_catalog, loaded_skills),
    ):
        registry.register(definition, handler)
    return registry


__all__ = ["ToolRegistryImpl", "build_tool_registry"]

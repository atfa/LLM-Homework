"""组合根：唯一知道「接口 ↔ 实现」如何配对的地方。

只有这里可以同时 import application 与 infrastructure；UI 与 core
都不应该出现具体实现类（OpenAICompatibleClient、ToolRegistryImpl）。
"""

from __future__ import annotations

from pathlib import Path

from .. import __version__
from .application.runtime import AgentRuntime, RuntimeInfo
from .application.service import AgentService
from .application.session import ChatSession
from .application.settings import DEFAULT_SYSTEM_PROMPT, AgentSettings
from .infrastructure.config import AppConfig, load_config
from .infrastructure.llm.openai_compatible import OpenAICompatibleClient
from .infrastructure.skills import SkillCatalog, compose_system_prompt
from .infrastructure.tools.catalog import build_tool_registry


def build_runtime(
    config: AppConfig | None = None,
    *,
    config_path: str | Path | None = None,
    workspace: str | Path | None = None,
    project_root: str | Path | None = None,
) -> AgentRuntime:
    """装配并返回可运行的 :class:`AgentRuntime`。

    传入已加载的 ``config`` 可跳过配置读取（测试/嵌入场景）。
    ``project_root`` 用于发现 ``.agents/skills/``，默认是程序启动时的工作目录。
    """
    app_config = config or load_config(config_path=config_path, workspace=workspace)

    skills = SkillCatalog.discover(project_root)
    registry = build_tool_registry(app_config.workspace, search=app_config.search, skills=skills)
    llm = OpenAICompatibleClient(
        base_url=app_config.llm.base_url,
        api_key=app_config.llm.api_key,
        model=app_config.llm.model,
        timeout_s=app_config.llm.timeout_s,
        temperature=app_config.llm.temperature,
    )
    base_prompt = app_config.system_prompt or DEFAULT_SYSTEM_PROMPT

    def render_system_prompt() -> str:
        # createSkill 会向同一个 SkillCatalog 注册新技能，下一次模型调用前重新渲染。
        return compose_system_prompt(base_prompt, skills)

    system_prompt = render_system_prompt()
    settings = AgentSettings(
        system_prompt=system_prompt,
        max_iterations=app_config.max_iterations,
        max_tool_output_chars=app_config.max_tool_output_chars,
    )
    agent = AgentService(
        llm=llm,
        tools=registry,
        settings=settings,
        system_prompt_provider=render_system_prompt,
    )
    session = ChatSession.create(system_prompt)

    info = RuntimeInfo(
        app_name="xAgent",
        version=__version__,
        model=app_config.llm.model,
        base_url=app_config.llm.base_url,
        workspace=str(app_config.workspace),
        theme=app_config.theme,
        tools=tuple(registry.definitions()),
    )
    return AgentRuntime(agent=agent, session=session, info=info)


__all__ = ["build_runtime"]

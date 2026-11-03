"""组合根：唯一知道「接口 ↔ 实现」如何配对的地方。

只有这里可以同时 import application 与 infrastructure；UI 与 core
都不应该出现具体实现类（OpenAICompatibleClient、ToolRegistryImpl）。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from .. import __version__
from .application.runtime import AgentRuntime, RuntimeInfo, SkillInfo
from .application.service import AgentService
from .application.session import ChatSession
from .application.settings import DEFAULT_SYSTEM_PROMPT, AgentSettings
from .domain.skills import LoadedSkills
from .infrastructure.config import AppConfig, load_config
from .infrastructure.context import LocalContextArchive
from .infrastructure.llm.openai_compatible import OpenAICompatibleClient
from .infrastructure.mcp import McpClientManager, McpToolBridge, load_mcp_config
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
    ``project_root`` 用于发现技能；归档默认写入工作目录的 ``.agents/context/``，
    显式提供 ``project_root`` 时归档与检索统一使用该项目目录。
    """
    app_config = config or load_config(config_path=config_path, workspace=workspace)

    skills = SkillCatalog.discover(project_root)
    # 技能状态由工具注册表与聊天会话共享：loadSkill 写入，System Prompt 读取。
    loaded_skills = LoadedSkills()
    context_archive = LocalContextArchive(project_root or app_config.workspace)
    registry = build_tool_registry(
        app_config.workspace,
        search=app_config.search,
        skills=skills,
        loaded_skills=loaded_skills,
        context_archive=context_archive,
        connect_mcp=False,
        tool_settings=app_config.tools,
    )
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
        context_window=app_config.llm.context_window,
        context_window_default=app_config.llm.context_window_default,
        max_tool_output_chars=app_config.max_tool_output_chars,
    )
    agent = AgentService(
        llm=llm,
        tools=registry,
        settings=settings,
        system_prompt_provider=render_system_prompt,
        context_archive=context_archive,
    )
    session = ChatSession.create(
        system_prompt,
        loaded_skills=loaded_skills,
        project_root=project_root or app_config.workspace,
    )

    def render_skills() -> tuple[SkillInfo, ...]:
        return tuple(SkillInfo(name=skill.name, description=skill.description) for skill in skills.skills)

    info = RuntimeInfo(
        app_name="xAgent",
        version=__version__,
        model=app_config.llm.model,
        base_url=app_config.llm.base_url,
        workspace=str(app_config.workspace),
        theme=app_config.theme,
        tools=tuple(registry.definitions()),
        skills=render_skills(),
    )
    runtime = AgentRuntime(agent=agent, session=session, info=info, skills_provider=render_skills)

    async def load_mcp() -> str:
        result = load_mcp_config(app_config.workspace)
        lines = list(result.errors)
        if not result.servers:
            return "\n".join(lines) or "（未配置 MCP 服务器）"
        manager = McpClientManager(result.servers)
        # 在连接前挂载清理，退出时即使正在加载也会关闭资源。
        registry.set_cleanup(manager.close)
        statuses = await asyncio.to_thread(manager.connect)
        bridge = McpToolBridge(manager, reserved_names=registry.names())
        for definition, handler in bridge.entries():
            registry.register(definition, handler)
        runtime.info = replace(runtime.info, tools=registry.definitions())
        for status in statuses:
            lines.append(
                f"{'●' if status.connected else '×'} {status.name}"
                + (" · 已连接" if status.connected else f" · {status.error}")
            )
            lines.extend(f"  • {binding.exposed_name}" for binding in bridge.bindings if binding.server == status.name)
        return "\n".join(lines)

    runtime.mcp_loader = load_mcp
    return runtime


__all__ = ["build_runtime"]

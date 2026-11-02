"""工具目录：装配默认工具（时间 2 + 文件 10 + 联网搜索 1 + 网页抓取 1 + 命令行 1 + Skill 2 + MCP）。"""

from __future__ import annotations

import sys
from pathlib import Path

from ...domain.skills import LoadedSkills
from ..config import SearchSettings, ToolSettings
from ..context import LocalContextArchive, context_tools
from ..mcp import McpClientManager, McpToolBridge, load_mcp_config
from ..skills import SkillCatalog, skill_tools
from .date_time import datetime_tools
from .file_ops import Sandbox, file_tools
from .registry import ToolRegistryImpl
from .run_cli import run_cli_tools
from .web_fetch import fetch_web_tools
from .web_search import QianfanSearchClient, web_search_tools


def _warn(message: str) -> None:
    print(f"[MCP] {message}", file=sys.stderr)


def _connect_mcp(workspace: str | Path) -> McpClientManager | None:
    """读取 ``<workspace>/.mcp.json`` 并连接所有 Server。

    配置错误和单个 Server 的连接失败都只输出提示，不阻塞本地工具。
    """
    result = load_mcp_config(workspace)
    if result.path is not None:
        _warn(f"配置文件：{result.path}")
    for error in result.errors:
        _warn(error)
    if not result.servers:
        return None

    manager = McpClientManager(result.servers)
    try:
        statuses = manager.connect()
    except Exception as exc:
        _warn(f"初始化 MCP Client 失败：{type(exc).__name__}: {exc}")
        manager.close()
        return None

    for status in statuses:
        if status.connected:
            _warn(f"已连接「{status.name}」：发现 {status.tool_count} 个工具")
        else:
            _warn(f"无法连接「{status.name}」（{status.target}）：{status.error or '未知错误'}；已跳过该服务器的工具")
    return manager


def build_tool_registry(
    workspace: str | Path,
    *,
    search: SearchSettings | None = None,
    skills: SkillCatalog | None = None,
    loaded_skills: LoadedSkills | None = None,
    mcp: McpClientManager | None = None,
    context_archive: LocalContextArchive | None = None,
    connect_mcp: bool = True,
    tool_settings: ToolSettings | None = None,
) -> ToolRegistryImpl:
    """构建绑定到指定工作目录的默认工具集。

    ``search`` 为空表示没有配置百度千帆搜索；此时 ``webSearch`` 仍会注册，
    但调用时会返回「未配置 API Key」的可读提示。

    ``skills`` 为初始化阶段发现的 Skill 目录；省略时以 ``workspace`` 为项目根
    扫描 ``.agents/skills/``。``loadSkill`` 始终注册，没有 Skill 时返回可读提示。

    ``loaded_skills`` 是会话级已加载技能状态；与 ``ChatSession`` 共享同一实例
    后，加载过的正文才能跨轮注入 System Prompt，并被后续 ``loadSkill`` 去重。

    ``connect_mcp=False`` 时只装配本地工具，由运行时在 TUI 挂载后异步加载 MCP。
    其他调用方保留同步连接的默认行为；连接失败不影响内置工具。
    """
    sandbox = Sandbox(workspace)
    settings = search or SearchSettings()
    tool_config = tool_settings or ToolSettings()
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
        *context_tools(context_archive or LocalContextArchive(workspace)),
        *datetime_tools(),
        *file_tools(sandbox),
        *web_search_tools(client),
        *fetch_web_tools(
            timeout_s=tool_config.fetch_timeout_s,
            connect_timeout_s=tool_config.fetch_connect_timeout_s,
        ),
        *run_cli_tools(workspace, timeout_s=tool_config.cli_timeout_s),
        *skill_tools(skill_catalog, loaded_skills),
    ):
        registry.register(definition, handler)

    mcp_manager = mcp if mcp is not None else (_connect_mcp(workspace) if connect_mcp else None)
    if mcp_manager is not None:
        bridge = McpToolBridge(mcp_manager, reserved_names=registry.names())
        for definition, handler in bridge.entries():
            registry.register(definition, handler)
        registry.set_cleanup(mcp_manager.close)
    return registry


__all__ = ["ToolRegistryImpl", "build_tool_registry"]

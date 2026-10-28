"""运行时装配结果：UI 需要的全部依赖与只读元信息。

这是应用层对 UI 暴露的唯一入口对象，避免 UI 直接接触配置解析、
HTTP 客户端或工具实现。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.tools import ToolDefinition
from .service import AgentService
from .session import ChatSession


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """展示用 Skill 元信息（不含正文与文件路径）。"""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    """展示用元信息（不含 API Key）。"""

    app_name: str
    version: str
    model: str
    base_url: str
    workspace: str
    theme: str
    tools: tuple[ToolDefinition, ...]
    skills: tuple[SkillInfo, ...] = ()


@dataclass
class AgentRuntime:
    agent: AgentService
    session: ChatSession
    info: RuntimeInfo
    # createSkill 会在运行期注册新 Skill，侧栏据此刷新「当前可用」列表。
    skills_provider: Callable[[], tuple[SkillInfo, ...]] | None = None

    def skills(self) -> tuple[SkillInfo, ...]:
        if self.skills_provider is None:
            return self.info.skills
        return self.skills_provider()

    async def aclose(self) -> None:
        await self.agent.aclose()

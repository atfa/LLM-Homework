"""会话实体：把领域历史与展示所需的元信息组合在一起。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..domain.conversation import Conversation
from ..domain.skills import LoadedSkills


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ChatSession:
    """一次应用运行期间的对话会话（内存态，可扩展为持久化）。"""

    conversation: Conversation
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        system_prompt: str,
        *,
        loaded_skills: LoadedSkills | None = None,
    ) -> ChatSession:
        """创建会话；``loaded_skills`` 应与工具注册表共享同一实例。"""
        return cls(
            conversation=Conversation(
                system_prompt=system_prompt,
                loaded_skills=loaded_skills if loaded_skills is not None else LoadedSkills(),
            )
        )

    @property
    def turn_count(self) -> int:
        return self.conversation.turn_count

    @property
    def message_count(self) -> int:
        return len(self.conversation)

    def reset(self) -> None:
        self.conversation.clear()
        self.id = _new_id()
        self.created_at = _now()

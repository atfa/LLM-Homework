"""会话实体：把领域历史与展示所需的元信息组合在一起。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..domain.conversation import Conversation
from ..domain.messages import ChatMessage
from ..domain.skills import LoadedSkills
from ..domain.tools import ToolCall


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
    context_tokens: int = 0
    needs_compaction: bool = False
    state: str = "idle"
    # 原始工具结果（含未成功完成的轮次）独立保留，不受回传截断影响。
    archive_messages: list[ChatMessage] = field(default_factory=list)

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
        if self.state != "idle":
            raise RuntimeError("会话正忙")
        self.conversation.clear()
        self.archive_messages.clear()
        self.context_tokens = 0
        self.needs_compaction = False
        self.id = _new_id()
        self.created_at = _now()

    def save(self, directory: Path) -> None:
        """原子保存完整上下文；不会保存 API Key。"""
        if self.state != "idle":
            return
        directory.mkdir(parents=True, exist_ok=True)
        data = {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "context_tokens": self.context_tokens,
            "needs_compaction": self.needs_compaction,
            "messages": [message.to_wire() for message in self.conversation.history()],
            "archive": [message.to_wire() for message in self.archive_messages],
            "skills": {
                name: self.conversation.loaded_skills.get(name) for name in self.conversation.loaded_skills.names()
            },
        }
        target = directory / f"{self.id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(target)

    def restore(self, directory: Path, session_id: str = "") -> None:
        """恢复最近会话或指定 ID；解析成功后才替换当前状态。"""
        if self.state != "idle":
            raise ValueError("会话正忙")
        if session_id and not re.fullmatch(r"[0-9a-f]{8}", session_id):
            raise ValueError("无效会话 ID，请使用 /session 查看")
        if session_id:
            path = directory / f"{session_id}.json"
        else:
            paths = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            paths = [p for p in paths if p.stem != self.id]
            if not paths:
                raise ValueError("没有可恢复的历史会话")
            path = paths[0]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法读取会话：{exc}") from exc

        def decode(rows: list) -> list[ChatMessage]:
            return [
                ChatMessage(
                    role=row["role"],
                    content=row.get("content", ""),
                    tool_call_id=row.get("tool_call_id"),
                    tool_calls=tuple(
                        ToolCall(id=call["id"], name=call["function"]["name"], arguments=call["function"]["arguments"])
                        for call in row.get("tool_calls", [])
                    ),
                )
                for row in rows
            ]

        messages = decode(data["messages"])
        archive = decode(data.get("archive", []))
        created = datetime.fromisoformat(data["created_at"])
        tokens = int(data.get("context_tokens", 0))
        skills = data.get("skills", {})
        if not isinstance(skills, dict) or not all(isinstance(v, str) for v in skills.values()):
            raise ValueError("无效技能记录")
        self.conversation.replace_history(messages)
        self.conversation.loaded_skills.clear()
        for name, body in skills.items():
            self.conversation.loaded_skills.add(name, body)
        self.id, self.created_at = path.stem, created
        self.archive_messages = archive
        self.context_tokens = tokens
        self.needs_compaction = bool(data.get("needs_compaction", False))

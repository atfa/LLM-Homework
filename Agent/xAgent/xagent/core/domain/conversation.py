"""会话历史（含成功轮次的工具往返）与已加载技能正文。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .messages import ChatMessage, Role, Turn
from .skills import LoadedSkills, merge_system_prompt


@dataclass
class Conversation:
    """一段多轮对话。

    成功轮次保留 user / assistant / tool 消息，供后续推理与压缩。
    压缩时仅替换历史，不改变 System Prompt 或已加载技能。

    例外是 ``loadSkill`` 加载的技能正文：它被记录在 ``loaded_skills`` 中，
    每轮随 System Prompt 注入，避免模型在后续轮次重复加载同一个技能。
    """

    system_prompt: str = ""
    _messages: list[ChatMessage] = field(default_factory=list)
    loaded_skills: LoadedSkills = field(default_factory=LoadedSkills)

    def render_system_prompt(self) -> str:
        """基础 System Prompt + 已加载技能正文。"""
        return merge_system_prompt(self.system_prompt, self.loaded_skills.render())

    def messages(self) -> tuple[ChatMessage, ...]:
        """发给模型的完整消息序列（含 system 提示词）。"""
        prompt = self.render_system_prompt()
        prefix: tuple[ChatMessage, ...] = ()
        if prompt.strip():
            prefix = (ChatMessage.system(prompt),)
        return prefix + tuple(self._messages)

    def history(self) -> tuple[ChatMessage, ...]:
        """不含 system 的问答历史。"""
        return tuple(self._messages)

    def add_turn(self, user: str, assistant: str) -> None:
        self._messages.extend((ChatMessage.user(user), ChatMessage.assistant(assistant)))

    def add_turns(self, turns: list[Turn]) -> None:
        for turn in turns:
            self._messages.extend(turn.as_messages())

    def append(self, *messages: ChatMessage) -> None:
        for message in messages:
            if message.role is Role.SYSTEM:
                raise ValueError("system 提示词请通过 system_prompt 设置")
            self._messages.append(message)

    def prepend(self, *messages: ChatMessage) -> None:
        """把消息放到历史最前面，用于会话首次加载的项目上下文。"""
        for message in messages:
            if message.role is Role.SYSTEM:
                raise ValueError("system 提示词请通过 system_prompt 设置")
        self._messages[0:0] = messages

    def replace_history(self, messages: list[ChatMessage]) -> None:
        """仅替换历史；保留 System Prompt 和已加载技能。"""
        self._messages = list(messages)

    def clear(self) -> None:
        self._messages.clear()
        self.loaded_skills.clear()

    def __len__(self) -> int:
        return len(self._messages)

    @property
    def turn_count(self) -> int:
        return sum(1 for message in self._messages if message.role is Role.USER)

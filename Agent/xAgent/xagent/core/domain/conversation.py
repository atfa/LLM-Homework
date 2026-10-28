"""会话历史（只保存最终问答）与已加载技能正文。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .messages import ChatMessage, Role, Turn
from .skills import LoadedSkills, merge_system_prompt


@dataclass
class Conversation:
    """一段多轮对话。

    工具调用的中间消息属于「本轮工作区」，由应用层临时维护，
    不会写入这里；这里只保留 user / assistant 的最终问答，
    与原始脚本 ``history`` 的语义一致。

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

    def clear(self) -> None:
        self._messages.clear()
        self.loaded_skills.clear()

    def __len__(self) -> int:
        return len(self._messages)

    @property
    def turn_count(self) -> int:
        return sum(1 for message in self._messages if message.role is Role.USER)

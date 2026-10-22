"""应用层配置：只包含「Agent 行为」相关的设置，与连接信息解耦。"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = """你是 xAgent，一个运行在用户本机的编程助手。

工作方式：
1. 需要查看文件、目录或执行文件操作时，优先调用工具获取真实结果，不要凭空猜测。
2. 需要全网的最新信息（新闻、公告、政策、版本号、价格等）时，调用 webSearch 联网搜索，并注明信息来源。
3. 所有文件路径都相对于当前工作目录（沙箱根目录），不要尝试访问工作目录之外的位置。
4. 删除、覆盖、重命名等破坏性操作前，先确认目标确实存在且符合用户意图；如果信息不足，先询问用户。
5. 工具报错时阅读错误信息并自行纠正，不要重复同一个失败调用。
6. 用简体中文回答，结论先行；涉及代码时给出可直接使用的片段。
"""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Agent 引擎参数（不含 API Key 等基础设施配置）。"""

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_iterations: int = 8
    """单轮问答内允许的最大模型调用次数，防止工具循环失控。"""

    max_tool_output_chars: int = 30_000
    """单个工具结果回传模型的最大字符数，超出部分截断（UI 仍显示完整内容）。"""

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations 必须 >= 1")
        if self.max_tool_output_chars < 0:
            raise ValueError("max_tool_output_chars 不能为负数")

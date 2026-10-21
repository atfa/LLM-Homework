"""领域错误类型。

所有异常都继承自 :class:`AgentError`，使上层可以只捕获一个基类，
同时又能按类型区分「配置错 / 模型错 / 工具错」。
"""

from __future__ import annotations


class AgentError(Exception):
    """xAgent 全部可预期错误的基类。"""


class ConfigurationError(AgentError):
    """配置缺失或非法（例如 config.ini 里没有 api_key）。"""


class LLMError(AgentError):
    """调用模型接口失败（HTTP 错误、网络中断、响应格式异常等）。"""


class ToolError(AgentError):
    """工具执行失败。消息会原样回传给模型，让其自行纠正。"""


class SandboxViolation(ToolError):
    """路径越出工作目录沙箱。"""

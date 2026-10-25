"""命令行工具（runCli）：以参数列表方式调用本机任意可执行程序。"""

from .cli_tools import RUN_CLI, run_cli, run_cli_tools

__all__ = [
    "RUN_CLI",
    "run_cli",
    "run_cli_tools",
]

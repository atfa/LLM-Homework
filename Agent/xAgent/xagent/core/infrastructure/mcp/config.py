"""读取项目根目录下的 ``.mcp.json``（MCP Server 连接配置）。

支持 MCP 的两种标准传输：

- Streamable HTTP：``type: streamable_http`` + ``url``
- stdio：``type: stdio`` + ``command`` / ``args`` / ``env`` / ``cwd``

配置读取失败不会中断 xAgent 启动：错误会收集到
:class:`McpConfigResult.errors`，由调用方输出可读提示后继续使用本地工具。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

MCP_CONFIG_FILENAME = ".mcp.json"
"""项目根目录下的 MCP Server 配置文件名。"""

DEFAULT_MCP_TIMEOUT_S = 30.0
"""连接 / 单次工具调用的默认超时时间（秒）。"""

_STREAMABLE_HTTP_TYPES = {
    "streamable_http",
    "streamable-http",
    "streamablehttp",
    "http",
}
"""兼容常见写法；统一视为 Streamable HTTP。"""

_STDIO_TYPES = {"stdio", "standard_io", "standard-io"}
"""兼容常见写法；统一视为 stdio。"""


@dataclass(frozen=True, slots=True)
class McpServerSettings:
    """一个 MCP Server 的连接信息（HTTP 与 stdio 共用）。"""

    name: str
    url: str = ""
    transport: str = "auto"
    headers: Mapping[str, str] = field(default_factory=dict)
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""
    timeout_s: float = DEFAULT_MCP_TIMEOUT_S

    def __post_init__(self) -> None:
        """冻结后仍把 headers / env / args 转成不可变结构。"""
        transport = self.transport.strip().lower()
        if transport in ("", "auto"):
            transport = "stdio" if self.command else "streamable_http"
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "args", tuple(str(arg) for arg in self.args))

    @property
    def target(self) -> str:
        """用于日志 / 状态展示的连接目标。"""
        if self.transport == "stdio":
            return " ".join((self.command, *self.args)).strip() or self.command
        return self.url


@dataclass(frozen=True, slots=True)
class McpConfigResult:
    """一次 ``.mcp.json`` 加载结果（可能同时包含可用配置与错误）。"""

    path: Path | None
    servers: tuple[McpServerSettings, ...] = ()
    errors: tuple[str, ...] = ()


def find_mcp_config(project_root: str | Path | None = None) -> Path | None:
    """返回项目根目录下的 ``.mcp.json``；不存在时返回 ``None``。"""
    root = Path(project_root if project_root is not None else Path.cwd()).expanduser()
    path = root / MCP_CONFIG_FILENAME
    if not path.is_file():
        return None
    return Path(os.path.realpath(path))


def _normalize_transport(raw: object, entry: Mapping[str, object]) -> str | None:
    value = str(raw or "").strip().lower()
    if not value:
        # 标准 MCP 配置通常不给 stdio 写 type，只给 command。
        return "stdio" if entry.get("command") else "streamable_http"
    if value in _STREAMABLE_HTTP_TYPES:
        return "streamable_http"
    if value in _STDIO_TYPES:
        return "stdio"
    return None


def _misplaced_server_keys(raw: Mapping[str, object]) -> list[str]:
    """找出被误放到顶层的 Server 配置（常见于 JSON 括号写错）。"""
    keys: list[str] = []
    for key, value in raw.items():
        if key == "mcpServers" or not isinstance(value, Mapping):
            continue
        if any(field in value for field in ("command", "url", "type")):
            keys.append(str(key))
    return keys


def load_mcp_config(project_root: str | Path | None = None) -> McpConfigResult:
    """读取并校验 ``.mcp.json``。

    非法文件 / 单个非法 Server 不会抛异常：所有问题都放进 ``errors``，
    其余合法 Server 仍然可用，保证 MCP 故障不影响本地工具。
    """
    path = find_mcp_config(project_root)
    if path is None:
        return McpConfigResult(path=None)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return McpConfigResult(path=path, errors=(f"读取 {path} 失败：{exc}",))
    except json.JSONDecodeError as exc:
        return McpConfigResult(
            path=path,
            errors=(f"{path} 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}",),
        )

    if not isinstance(raw, Mapping):
        return McpConfigResult(path=path, errors=(f"{path} 顶层必须是 JSON 对象",))

    servers_raw = raw.get("mcpServers")
    if servers_raw is None:
        misplaced = _misplaced_server_keys(raw)
        detail = f"；检测到疑似 Server 配置：{', '.join(misplaced)}，它们必须放在 mcpServers 内" if misplaced else ""
        return McpConfigResult(path=path, errors=(f'{path} 缺少 "mcpServers" 字段{detail}',))
    if not isinstance(servers_raw, Mapping):
        return McpConfigResult(path=path, errors=(f'{path} 的 "mcpServers" 必须是对象',))

    errors: list[str] = []
    misplaced = _misplaced_server_keys(raw)
    if misplaced:
        errors.append(
            f"{path} 中这些顶层字段未被识别：{', '.join(misplaced)}；"
            f'MCP Server 配置必须写在 "mcpServers" 对象内，已忽略'
        )

    servers: list[McpServerSettings] = []
    for name_raw, entry in servers_raw.items():
        name = str(name_raw).strip()
        if not name:
            errors.append(f"{path} 中存在空名称的 MCP Server，已跳过")
            continue
        if not isinstance(entry, Mapping):
            errors.append(f"MCP Server「{name}」的配置必须是对象，已跳过")
            continue

        transport = _normalize_transport(entry.get("type"), entry)
        if transport is None:
            errors.append(
                f"MCP Server「{name}」的 type 只支持 streamable_http / stdio，收到 {entry.get('type')!r}，已跳过"
            )
            continue

        url = ""
        headers: dict[str, str] = {}
        command = ""
        args: tuple[str, ...] = ()
        env: dict[str, str] = {}
        cwd = ""

        if transport == "stdio":
            command = str(entry.get("command") or "").strip()
            if not command:
                errors.append(f"MCP Server「{name}」是 stdio 类型但缺少 command，已跳过")
                continue

            args_raw = entry.get("args", [])
            if args_raw is None:
                args_raw = []
            if not isinstance(args_raw, list):
                errors.append(f"MCP Server「{name}」的 args 必须是数组，已跳过")
                continue
            args = tuple(str(arg) for arg in args_raw)

            env_raw = entry.get("env", {})
            if env_raw is None:
                env_raw = {}
            if not isinstance(env_raw, Mapping):
                errors.append(f"MCP Server「{name}」的 env 必须是对象，已跳过")
                continue
            env = {str(key): str(value) for key, value in env_raw.items()}

            cwd_raw = entry.get("cwd")
            cwd = str(cwd_raw).strip() if cwd_raw is not None else ""
        else:
            url = str(entry.get("url") or "").strip()
            if not url:
                errors.append(f"MCP Server「{name}」缺少 url，已跳过")
                continue
            if not url.startswith(("http://", "https://")):
                errors.append(f"MCP Server「{name}」的 url 必须以 http:// 或 https:// 开头，收到 {url!r}，已跳过")
                continue

            headers_raw = entry.get("headers", {})
            if headers_raw is None:
                headers_raw = {}
            if not isinstance(headers_raw, Mapping):
                errors.append(f"MCP Server「{name}」的 headers 必须是对象，已跳过")
                continue
            headers = {str(key): str(value) for key, value in headers_raw.items()}

        timeout_raw = entry.get("timeout", DEFAULT_MCP_TIMEOUT_S)
        try:
            timeout_s = float(timeout_raw)
        except (TypeError, ValueError):
            errors.append(f"MCP Server「{name}」的 timeout 必须是数字，收到 {timeout_raw!r}，已跳过")
            continue
        if timeout_s <= 0:
            errors.append(f"MCP Server「{name}」的 timeout 必须大于 0，收到 {timeout_s}，已跳过")
            continue

        servers.append(
            McpServerSettings(
                name=name,
                url=url,
                transport=transport,
                headers=headers,
                command=command,
                args=args,
                env=env,
                cwd=cwd,
                timeout_s=timeout_s,
            )
        )

    return McpConfigResult(path=path, servers=tuple(servers), errors=tuple(errors))


__all__ = [
    "DEFAULT_MCP_TIMEOUT_S",
    "MCP_CONFIG_FILENAME",
    "McpConfigResult",
    "McpServerSettings",
    "find_mcp_config",
    "load_mcp_config",
]

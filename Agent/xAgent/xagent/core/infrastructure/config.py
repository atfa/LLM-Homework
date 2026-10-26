"""配置加载：config.ini + 环境变量覆盖。

配置只负责「连接与运行参数」，不包含任何 Agent 行为逻辑；
环境变量优先级高于 config.ini，便于 CI / 容器部署。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from ..domain.errors import ConfigurationError

DEFAULT_CONFIG_FILENAME = "config.ini"

DEFAULT_SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
"""百度千帆 AI 搜索端点。"""

DEFAULT_SEARCH_SOURCE = "baidu_search_v2"
"""搜索引擎版本：v2 数据更丰富，更适合配合大模型使用。"""

SEARCH_RECENCY_VALUES = ("week", "month", "semiyear", "year")
"""发布时间过滤：最近 7 天 / 30 天 / 180 天 / 365 天。"""

MAX_SEARCH_TOP_K = 20
"""单次搜索最多返回的网页结果条数。"""


@dataclass(frozen=True, slots=True)
class LLMSettings:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 90.0
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class SearchSettings:
    """百度千帆 AI 搜索（联网搜索工具）的连接与运行参数。"""

    api_key: str = ""
    endpoint: str = DEFAULT_SEARCH_ENDPOINT
    search_source: str = DEFAULT_SEARCH_SOURCE
    top_k: int = 5
    timeout_s: float = 30.0
    recency: str = ""
    model: str = ""
    """留空 = 百度搜索模式（只返回网页结果）；填写 = 智能搜索生成模式（额外返回模型总结）。"""


@dataclass(frozen=True, slots=True)
class AppConfig:
    llm: LLMSettings
    workspace: Path
    max_iterations: int = 20
    max_tool_output_chars: int = 30_000
    system_prompt: str = ""
    theme: str = "textual-dark"
    config_path: Path | None = None
    search: SearchSettings = SearchSettings()


def _project_config_path() -> Path:
    # config.py -> infrastructure -> xagent -> xAgent
    return Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME


def find_config_file(explicit: str | Path | None = None) -> Path:
    """按「显式参数 → 环境变量 → 当前目录 → 项目根目录」查找配置文件。"""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        env_path = os.environ.get("XAGENT_CONFIG", "").strip()
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.append(Path.cwd() / DEFAULT_CONFIG_FILENAME)
        candidates.append(_project_config_path())

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = "、".join(str(path) for path in candidates) or "(无)"
    raise ConfigurationError(
        f"找不到 {DEFAULT_CONFIG_FILENAME}（已查找：{searched}）。"
        f"请先复制 {DEFAULT_CONFIG_FILENAME}.example 为 {DEFAULT_CONFIG_FILENAME} 并填写 base_url / model / api_key。"
    )


def _read_parser(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        loaded = parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        raise ConfigurationError(f"读取配置文件失败 {path}：{exc}") from exc
    if not loaded:
        raise ConfigurationError(f"配置文件为空或无法读取：{path}")
    return parser


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _env_float(name: str) -> float | None:
    raw = _env(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"环境变量 {name} 必须是数字，收到 {raw!r}") from exc


def _int_or(value: str | int | None, default: int, *, label: str) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} 必须是整数，收到 {value!r}") from exc


def _float_or(value: str | float | None, default: float, *, label: str) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} 必须是数字，收到 {value!r}") from exc


def _load_search_settings(parser: configparser.ConfigParser, path: Path) -> SearchSettings:
    """读取 [search] 段。缺失或留空是允许的（搜索工具会提示未配置）。"""
    section = parser["search"] if "search" in parser else {}

    api_key = (_env("XAGENT_SEARCH_API_KEY") or section.get("baidu_qianfan_api_key", "")).strip()
    if api_key and ("your-" in api_key.lower() or api_key.lower().endswith("/xxx")):
        raise ConfigurationError(f"{path} 的 [search] 段 baidu_qianfan_api_key 仍是模板占位值，请填写真实 Key")

    endpoint = (_env("XAGENT_SEARCH_ENDPOINT") or section.get("endpoint", "") or DEFAULT_SEARCH_ENDPOINT).strip()
    if not endpoint.startswith(("http://", "https://")):
        raise ConfigurationError(f"[search] endpoint 必须以 http:// 或 https:// 开头，收到 {endpoint!r}")

    search_source = (_env("XAGENT_SEARCH_SOURCE") or section.get("search_source", "") or DEFAULT_SEARCH_SOURCE).strip()
    if search_source not in ("baidu_search_v1", "baidu_search_v2"):
        raise ConfigurationError(
            f"[search] search_source 只能是 baidu_search_v1 或 baidu_search_v2，收到 {search_source!r}"
        )

    top_k = _int_or(
        _env("XAGENT_SEARCH_TOP_K") or section.get("top_k"),
        5,
        label="[search] top_k",
    )
    if top_k < 1 or top_k > MAX_SEARCH_TOP_K:
        raise ConfigurationError(f"[search] top_k 必须在 1~{MAX_SEARCH_TOP_K} 之间，收到 {top_k}")

    timeout_s = _float_or(
        _env("XAGENT_SEARCH_TIMEOUT") or section.get("timeout"),
        30.0,
        label="[search] timeout",
    )
    if timeout_s <= 0:
        raise ConfigurationError("[search] timeout 必须大于 0")

    recency = (_env("XAGENT_SEARCH_RECENCY") or section.get("recency", "")).strip()
    if recency and recency not in SEARCH_RECENCY_VALUES:
        raise ConfigurationError(f"[search] recency 只能是 {'/'.join(SEARCH_RECENCY_VALUES)} 之一，收到 {recency!r}")

    model = (_env("XAGENT_SEARCH_MODEL") or section.get("model", "")).strip()

    return SearchSettings(
        api_key=api_key,
        endpoint=endpoint,
        search_source=search_source,
        top_k=top_k,
        timeout_s=timeout_s,
        recency=recency,
        model=model,
    )


def load_config(
    config_path: str | Path | None = None,
    workspace: str | Path | None = None,
) -> AppConfig:
    """加载并校验配置。任何缺失都抛出可读的 :class:`ConfigurationError`。"""
    path = find_config_file(config_path)
    parser = _read_parser(path)

    if "llm" not in parser:
        raise ConfigurationError(f"{path} 缺少 [llm] 段")

    llm_section = parser["llm"]
    base_url = (_env("XAGENT_BASE_URL") or llm_section.get("base_url", "")).strip().rstrip("/")
    api_key = (_env("XAGENT_API_KEY") or llm_section.get("api_key", "")).strip()
    model = (_env("XAGENT_MODEL") or llm_section.get("model", "")).strip()

    missing = [name for name, value in (("base_url", base_url), ("model", model), ("api_key", api_key)) if not value]
    if missing:
        raise ConfigurationError(
            f"{path} 的 [llm] 段缺少：{', '.join(missing)}。"
            "可以填写配置文件，或通过 XAGENT_BASE_URL / XAGENT_MODEL / XAGENT_API_KEY 提供。"
        )
    if not base_url.startswith(("http://", "https://")):
        raise ConfigurationError(f"base_url 必须以 http:// 或 https:// 开头，收到 {base_url!r}")
    if "your-" in api_key.lower() or api_key.lower() in {"sk-xxx", "changeme"}:
        raise ConfigurationError("api_key 仍是模板占位值，请填写真实 Key")

    timeout_raw = _env("XAGENT_TIMEOUT") or llm_section.get("timeout", "")
    timeout_s = 90.0
    if timeout_raw.strip():
        try:
            timeout_s = float(timeout_raw)
        except ValueError as exc:
            raise ConfigurationError(f"timeout 必须是数字，收到 {timeout_raw!r}") from exc
        if timeout_s <= 0:
            raise ConfigurationError("timeout 必须大于 0")

    temperature_raw = _env("XAGENT_TEMPERATURE") or llm_section.get("temperature", "")
    temperature: float | None = None
    if temperature_raw.strip():
        try:
            temperature = float(temperature_raw)
        except ValueError as exc:
            raise ConfigurationError(f"temperature 必须是数字，收到 {temperature_raw!r}") from exc

    agent_section = parser["agent"] if "agent" in parser else {}
    max_iterations = _int_or(
        _env("XAGENT_MAX_ITERATIONS") or agent_section.get("max_iterations"),
        20,
        label="max_iterations",
    )
    if max_iterations < 1:
        raise ConfigurationError("max_iterations 必须 >= 1")
    max_tool_output_chars = _int_or(
        agent_section.get("max_tool_output_chars"),
        30_000,
        label="max_tool_output_chars",
    )

    workspace_raw = (
        str(workspace)
        if workspace is not None
        else (_env("XAGENT_WORKSPACE") or agent_section.get("workspace") or str(Path.cwd()))
    )
    workspace_path = Path(workspace_raw).expanduser()
    if not workspace_path.is_absolute():
        workspace_path = Path.cwd() / workspace_path
    workspace_path = workspace_path.resolve()
    if not workspace_path.is_dir():
        raise ConfigurationError(f"工作目录不存在或不是目录：{workspace_path}")

    return AppConfig(
        llm=LLMSettings(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            temperature=temperature,
        ),
        workspace=workspace_path,
        max_iterations=max_iterations,
        max_tool_output_chars=max_tool_output_chars,
        system_prompt=agent_section.get("system_prompt", "").strip(),
        theme=(_env("XAGENT_THEME") or agent_section.get("theme", "textual-dark")).strip() or "textual-dark",
        config_path=path,
        search=_load_search_settings(parser, path),
    )

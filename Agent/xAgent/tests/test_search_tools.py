"""联网搜索工具测试：配置解析、参数校验、响应解析与错误归一化。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from xagent.core.domain.errors import ConfigurationError, ToolError
from xagent.core.domain.tools import ToolCall, ToolOutcome
from xagent.core.infrastructure.config import SearchSettings, load_config
from xagent.core.infrastructure.tools.catalog import build_tool_registry
from xagent.core.infrastructure.tools.registry import ToolRegistryImpl
from xagent.core.infrastructure.tools.web_search import qianfan_client as client_module
from xagent.core.infrastructure.tools.web_search.qianfan_client import QianfanSearchClient
from xagent.core.infrastructure.tools.web_search.search_tools import web_search

REFERENCES = [
    {
        "id": 1,
        "url": "https://example.com/a",
        "title": "示例标题 A",
        "date": "2026-10-20 16:33:17",
        "content": "这是第一条结果的摘要内容。",
        "website": "示例站",
        "type": "web",
    },
    {
        "id": 2,
        "url": "https://example.com/b",
        "title": "示例标题 B",
        "date": "2026-10-19 10:00:00",
        "content": "这是第二条结果的摘要内容。",
        "web_anchor": "示例来源",
        "type": "web",
    },
]


class FakeResponse:
    """最小可用的 ``requests.Response`` 替身。"""

    def __init__(self, payload: Any = None, *, status_code: int = 200, text: str | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload, ensure_ascii=False)

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class Recorder:
    """记录被调用的 ``requests.post`` 参数。"""

    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def invoke(registry: ToolRegistryImpl, name: str, **arguments: object) -> ToolOutcome:
    call = ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments, ensure_ascii=False))
    return asyncio.run(registry.invoke(call))


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
INI_WITH_SEARCH = """
[llm]
base_url = https://example.com/v1
model = test-model
api_key = sk-test-key

[search]
baidu_qianfan_api_key = bce-v3/ALTAK-ak/sk
search_source = baidu_search_v1
top_k = 3
timeout = 12
recency = month
"""


def test_search_section_is_parsed(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_text(INI_WITH_SEARCH, encoding="utf-8")

    config = load_config(path, workspace=tmp_path)

    assert config.search.api_key == "bce-v3/ALTAK-ak/sk"
    assert config.search.search_source == "baidu_search_v1"
    assert config.search.top_k == 3
    assert config.search.timeout_s == 12.0
    assert config.search.recency == "month"
    assert config.search.model == ""


def test_search_section_is_optional(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_text("[llm]\nbase_url = https://x/v1\nmodel = m\napi_key = sk-k\n", encoding="utf-8")

    config = load_config(path, workspace=tmp_path)

    assert config.search.api_key == ""
    assert config.search.search_source == "baidu_search_v2"
    assert config.search.top_k == 5


def test_search_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.ini"
    path.write_text(INI_WITH_SEARCH, encoding="utf-8")
    monkeypatch.setenv("XAGENT_SEARCH_API_KEY", "bce-v3/ALTAK-env/env")
    monkeypatch.setenv("XAGENT_SEARCH_TOP_K", "7")
    monkeypatch.setenv("XAGENT_SEARCH_RECENCY", "week")
    monkeypatch.setenv("XAGENT_SEARCH_SOURCE", "baidu_search_v2")

    config = load_config(path, workspace=tmp_path)

    assert config.search.api_key == "bce-v3/ALTAK-env/env"
    assert config.search.top_k == 7
    assert config.search.recency == "week"
    assert config.search.search_source == "baidu_search_v2"


def test_invalid_search_values_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_text(INI_WITH_SEARCH.replace("top_k = 3", "top_k = 99"), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path, workspace=tmp_path)

    path2 = tmp_path / "bad-source.ini"
    path2.write_text(INI_WITH_SEARCH.replace("baidu_search_v1", "google"), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path2, workspace=tmp_path)

    path3 = tmp_path / "bad-recency.ini"
    path3.write_text(INI_WITH_SEARCH.replace("recency = month", "recency = decade"), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path3, workspace=tmp_path)


def test_placeholder_search_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_text(INI_WITH_SEARCH.replace("bce-v3/ALTAK-ak/sk", "bce-v3/ALTAK-your-ak/xxx"), encoding="utf-8")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path, workspace=tmp_path)
    assert "占位" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
def test_search_builds_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeResponse({"references": REFERENCES, "request_id": "rid"}))
    monkeypatch.setattr(client_module.requests, "post", recorder)

    client = QianfanSearchClient(api_key="key", search_source="baidu_search_v2", top_k=4, recency="week")
    result = client.search("上海天气", top_k=2)

    assert len(result.hits) == 2
    assert result.hits[0].title == "示例标题 A"
    assert result.hits[0].site == "示例站"
    assert result.hits[1].site == "示例来源"
    assert result.request_id == "rid"

    call = recorder.calls[0]
    assert call["url"] == "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer key"
    payload = call["json"]
    assert payload["messages"] == [{"role": "user", "content": "上海天气"}]
    assert payload["search_source"] == "baidu_search_v2"
    assert payload["resource_type_filter"] == [{"type": "web", "top_k": 2}]
    assert payload["search_recency_filter"] == "week"
    assert payload["stream"] is False
    assert "model" not in payload


def test_search_summary_mode_sends_model(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(
        FakeResponse(
            {
                "references": REFERENCES,
                "choices": [{"message": {"role": "assistant", "content": "这是模型总结。"}}],
            }
        )
    )
    monkeypatch.setattr(client_module.requests, "post", recorder)

    client = QianfanSearchClient(api_key="key", model="ernie-4.5-turbo-32k")
    result = client.search("北京景点")

    assert result.summary == "这是模型总结。"
    assert recorder.calls[0]["json"]["model"] == "ernie-4.5-turbo-32k"


def test_search_rejects_invalid_arguments() -> None:
    client = QianfanSearchClient(api_key="key")
    with pytest.raises(ToolError):
        client.search("   ")
    with pytest.raises(ToolError):
        client.search("查询", top_k=0)
    with pytest.raises(ToolError):
        client.search("查询", top_k=99)
    with pytest.raises(ToolError):
        client.search("查询", recency="decade")


def test_search_without_api_key_explains_what_to_do() -> None:
    client = QianfanSearchClient(api_key="")
    with pytest.raises(ToolError) as excinfo:
        client.search("查询")
    message = str(excinfo.value)
    assert "[search]" in message
    assert "baidu_qianfan_api_key" in message


def test_http_error_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module.requests,
        "post",
        Recorder(FakeResponse(status_code=401, text='{"message":"invalid token"}')),
    )
    client = QianfanSearchClient(api_key="key")
    with pytest.raises(ToolError) as excinfo:
        client.search("查询")
    assert "401" in str(excinfo.value)


def test_api_error_code_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module.requests,
        "post",
        Recorder(FakeResponse({"code": "AccessDenied", "message": "没有权限"})),
    )
    client = QianfanSearchClient(api_key="key")
    with pytest.raises(ToolError) as excinfo:
        client.search("查询")
    assert "AccessDenied" in str(excinfo.value)


def test_network_error_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module.requests, "post", Recorder(client_module.requests.Timeout("超时了")))
    client = QianfanSearchClient(api_key="key")
    with pytest.raises(ToolError) as excinfo:
        client.search("查询")
    assert "超时" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def test_web_search_tool_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module.requests,
        "post",
        Recorder(FakeResponse({"references": REFERENCES, "request_id": "rid"})),
    )
    client = QianfanSearchClient(api_key="key")
    text = web_search(client, {"query": "北京景点", "top_k": 2})

    assert "示例标题 A" in text
    assert "https://example.com/a" in text
    assert "示例站" in text
    assert "共 2 条" in text


def test_web_search_tool_validates_query() -> None:
    client = QianfanSearchClient(api_key="key")
    with pytest.raises(ToolError):
        web_search(client, {"query": ""})
    with pytest.raises(ToolError):
        web_search(client, {})
    with pytest.raises(ToolError):
        web_search(client, {"query": "x", "top_k": "很多"})


def test_web_search_tool_reports_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module.requests, "post", Recorder(FakeResponse({"references": []})))
    client = QianfanSearchClient(api_key="key")
    text = web_search(client, {"query": "不存在的词"})
    assert "没有找到" in text


def test_registry_registers_web_search_and_fails_readably(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    assert "webSearch" in registry.names()

    outcome = invoke(registry, "webSearch", query="测试")
    assert not outcome.ok
    assert "baidu_qianfan_api_key" in outcome.content


def test_registry_uses_configured_search_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(FakeResponse({"references": REFERENCES}))
    monkeypatch.setattr(client_module.requests, "post", recorder)

    settings = SearchSettings(api_key="key", top_k=7, recency="year", search_source="baidu_search_v1")
    registry = build_tool_registry(tmp_path, search=settings)

    outcome = invoke(registry, "webSearch", query="测试")
    assert outcome.ok, outcome.content

    payload = recorder.calls[0]["json"]
    assert payload["resource_type_filter"] == [{"type": "web", "top_k": 7}]
    assert payload["search_recency_filter"] == "year"
    assert payload["search_source"] == "baidu_search_v1"

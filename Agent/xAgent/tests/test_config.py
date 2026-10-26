"""配置加载测试：模板、环境变量覆盖与错误提示。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from xagent.core.domain.errors import ConfigurationError
from xagent.core.infrastructure.config import find_config_file, load_config

INI = """
[llm]
base_url = https://example.com/v1/
model = test-model
api_key = sk-test-key
timeout = 12.5
temperature = 0.3

[agent]
max_iterations = 3
max_tool_output_chars = 1234
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("XAGENT_"):
            monkeypatch.delenv(key, raising=False)


def write_config(tmp_path: Path, text: str = INI, name: str = "config.ini") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_reads_all_sections(tmp_path: Path) -> None:
    path = write_config(tmp_path)
    config = load_config(path, workspace=tmp_path)

    assert config.llm.base_url == "https://example.com/v1"  # 去掉末尾斜杠
    assert config.llm.model == "test-model"
    assert config.llm.api_key == "sk-test-key"
    assert config.llm.timeout_s == 12.5
    assert config.llm.temperature == 0.3
    assert config.max_iterations == 3
    assert config.max_tool_output_chars == 1234
    assert config.workspace == tmp_path.resolve()
    assert config.config_path == path.resolve()
    assert config.theme == "textual-dark"


def test_default_max_iterations_is_twenty(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "[llm]\nbase_url = https://example.com/v1\nmodel = test-model\napi_key = sk-test-key\n",
    )
    config = load_config(path, workspace=tmp_path)
    assert config.max_iterations == 20


def test_environment_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_config(tmp_path)
    monkeypatch.setenv("XAGENT_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("XAGENT_MODEL", "env-model")
    monkeypatch.setenv("XAGENT_API_KEY", "env-key")
    monkeypatch.setenv("XAGENT_MAX_ITERATIONS", "9")
    monkeypatch.setenv("XAGENT_THEME", "textual-light")

    config = load_config(path, workspace=tmp_path)
    assert config.llm.base_url == "http://localhost:8000/v1"
    assert config.llm.model == "env-model"
    assert config.llm.api_key == "env-key"
    assert config.max_iterations == 9
    assert config.theme == "textual-light"


def test_workspace_can_come_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_config(tmp_path)
    workspace = tmp_path / "work"
    workspace.mkdir()
    monkeypatch.setenv("XAGENT_WORKSPACE", str(workspace))
    assert load_config(path).workspace == workspace.resolve()


def test_missing_api_key_has_actionable_message(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[llm]\nbase_url = https://x/v1\nmodel = m\napi_key =\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path, workspace=tmp_path)
    assert "api_key" in str(excinfo.value)


def test_placeholder_api_key_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[llm]\nbase_url = https://x/v1\nmodel = m\napi_key = sk-your-key\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path, workspace=tmp_path)
    assert "占位" in str(excinfo.value)


def test_missing_llm_section(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[agent]\nmax_iterations = 2\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path, workspace=tmp_path)
    assert "[llm]" in str(excinfo.value)


def test_invalid_numbers(tmp_path: Path) -> None:
    path = write_config(tmp_path, INI.replace("max_iterations = 3", "max_iterations = many"))
    with pytest.raises(ConfigurationError):
        load_config(path, workspace=tmp_path)

    path2 = write_config(tmp_path, INI.replace("timeout = 12.5", "timeout = soon"), name="bad-timeout.ini")
    with pytest.raises(ConfigurationError):
        load_config(path2, workspace=tmp_path)


def test_workspace_must_exist(tmp_path: Path) -> None:
    path = write_config(tmp_path)
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path, workspace=tmp_path / "nope")
    assert "工作目录" in str(excinfo.value)


def test_base_url_scheme_required(tmp_path: Path) -> None:
    path = write_config(tmp_path, INI.replace("https://example.com/v1/", "example.com/v1"))
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path, workspace=tmp_path)
    assert "http" in str(excinfo.value)


def test_find_config_file_reports_searched_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from xagent.core.infrastructure import config as config_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "_project_config_path", lambda: tmp_path / "project" / "config.ini")
    with pytest.raises(ConfigurationError) as excinfo:
        find_config_file()
    assert "config.ini" in str(excinfo.value)
    assert "config.ini.example" in str(excinfo.value)


def test_xagent_config_env_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_config(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setenv("XAGENT_CONFIG", str(path))
    inferred = load_config(workspace=tmp_path)
    assert inferred.config_path == path.resolve()

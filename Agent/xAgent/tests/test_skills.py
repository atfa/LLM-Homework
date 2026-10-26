"""Agent Skills：发现、System Prompt 注入、loadSkill 与动态加载。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.fakes import FakeLLM
from xagent.core.application.service import AgentService
from xagent.core.application.session import ChatSession
from xagent.core.application.settings import DEFAULT_SYSTEM_PROMPT, AgentSettings
from xagent.core.composition import build_runtime
from xagent.core.domain.events import StreamCompleted, TextDelta, ToolCallDelta
from xagent.core.domain.messages import Role
from xagent.core.domain.tools import ToolCall, ToolOutcome
from xagent.core.infrastructure.config import AppConfig, LLMSettings
from xagent.core.infrastructure.skills import (
    SkillCatalog,
    compose_system_prompt,
    render_skills_prompt,
)
from xagent.core.infrastructure.tools.catalog import build_tool_registry

BODY = "# 使用说明\n\n这是只在调用 loadSkill 后才应该进入上下文的正文。\n"


def write_skill(
    root: Path,
    dirname: str,
    *,
    name: str | None = None,
    description: str = "处理 PDF 文件",
    body: str = BODY,
    frontmatter: str | None = None,
) -> Path:
    directory = root / ".agents" / "skills" / dirname
    directory.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        frontmatter = f"---\nname: {name or dirname}\ndescription: {description}\n---\n"
    (directory / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    return directory


def registry_for(root: Path, catalog: SkillCatalog | None = None):
    return build_tool_registry(root, skills=catalog or SkillCatalog.discover(root))


def invoke(registry, **arguments: object) -> ToolOutcome:
    call = ToolCall(id="call_skill", name="loadSkill", arguments=json.dumps(arguments, ensure_ascii=False))
    return asyncio.run(registry.invoke(call))


def tool_call_chunks(index: int, call_id: str, name: str, arguments: str) -> list[object]:
    return [
        ToolCallDelta(index=index, id=call_id, name=name),
        ToolCallDelta(index=index, arguments=arguments[: len(arguments) // 2]),
        ToolCallDelta(index=index, arguments=arguments[len(arguments) // 2 :]),
    ]


# ---------------------------------------------------------------------------
# 发现与注册
# ---------------------------------------------------------------------------
def test_discovers_skills_from_startup_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_skill(tmp_path, "pdf", description="处理 PDF 文件")
    write_skill(tmp_path, "excel", description="读写表格")
    monkeypatch.chdir(tmp_path)

    catalog = SkillCatalog.discover()

    assert catalog.names == ("excel", "pdf")
    assert catalog.get("pdf") is not None
    assert catalog.get("pdf").description == "处理 PDF 文件"
    assert catalog.project_root == tmp_path.resolve()


def test_invalid_skills_are_skipped(tmp_path: Path) -> None:
    write_skill(tmp_path, "good")
    write_skill(
        tmp_path,
        "no-frontmatter",
        frontmatter="",
        body="# 没有 Frontmatter\n",
    )
    write_skill(tmp_path, "no-name", frontmatter="---\ndescription: 缺少 name\n---\n")
    write_skill(tmp_path, "no-description", frontmatter="---\nname: no-description\n---\n")
    write_skill(tmp_path, "unclosed", frontmatter="---\nname: unclosed\ndescription: 没有结束线\n", body="")
    (tmp_path / ".agents" / "skills" / "missing").mkdir()
    (tmp_path / ".agents" / "skills" / "missing" / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / ".agents" / "skills" / "not-a-dir.txt").write_text("x", encoding="utf-8")

    catalog = SkillCatalog.discover(tmp_path)

    assert catalog.names == ("good",)


def test_init_does_not_decode_skill_body(tmp_path: Path) -> None:
    directory = tmp_path / ".agents" / "skills" / "binary"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(
        b"---\nname: binary\ndescription: \xe5\x8f\xaa\xe8\xaf\xbb\xe5\x85\x83\xe6\x95\xb0\xe6\x8d\xae\n---\n"
        + b"\xff\xfe" * 4096
    )

    catalog = SkillCatalog.discover(tmp_path)

    assert catalog.names == ("binary",)
    assert catalog.get("binary").description == "只读元数据"


def test_duplicate_skill_names_keep_first_in_directory_order(tmp_path: Path) -> None:
    first = write_skill(tmp_path, "a-first", name="same")
    write_skill(tmp_path, "b-second", name="same")
    (first / "marker.txt").write_text("first", encoding="utf-8")

    catalog = SkillCatalog.discover(tmp_path)

    assert catalog.names == ("same",)
    assert catalog.get("same").directory == first.resolve()


def test_init_loads_only_metadata_into_prompt(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf", description="处理 PDF 文件", body=BODY)

    catalog = SkillCatalog.discover(tmp_path)
    prompt = render_skills_prompt(catalog)

    assert "- `pdf`: 处理 PDF 文件" in prompt
    assert "只在调用 loadSkill 后才应该进入上下文" not in prompt
    assert BODY not in prompt


def test_prompt_is_appended_to_existing_system_prompt(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")

    catalog = SkillCatalog.discover(tmp_path)
    prompt = compose_system_prompt("你是测试助手", catalog)

    assert prompt.startswith("你是测试助手\n\nYou have access to the following skills:")
    assert "`loadSkill`" in prompt
    assert compose_system_prompt("你是测试助手", SkillCatalog(tmp_path)) == "你是测试助手"


# ---------------------------------------------------------------------------
# loadSkill：正文与附加文件
# ---------------------------------------------------------------------------
def test_registry_discovers_workspace_skills_by_default(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")

    registry = build_tool_registry(tmp_path)

    outcome = invoke(registry, name="pdf")
    assert outcome.ok
    assert "只在调用 loadSkill 后才应该进入上下文" in outcome.content


def test_load_skill_returns_full_skill_md(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="pdf")

    assert outcome.ok
    assert outcome.content.startswith("---\nname: pdf")
    assert "只在调用 loadSkill 后才应该进入上下文" in outcome.content


def test_load_attachment_supports_skill_project_and_absolute_paths(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, "pdf")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("guide body", encoding="utf-8")
    registry = registry_for(tmp_path)

    by_skill_path = invoke(registry, name="pdf", filename="references/guide.md")
    by_project_path = invoke(registry, name="pdf", filename=".agents/skills/pdf/references/guide.md")
    by_absolute_path = invoke(registry, name="pdf", filename=str(skill_dir / "references" / "guide.md"))

    assert by_skill_path.content == "guide body"
    assert by_project_path.content == "guide body"
    assert by_absolute_path.content == "guide body"


def test_bare_filename_is_searched_recursively(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, "pdf")
    nested = skill_dir / "references" / "deep"
    nested.mkdir(parents=True)
    (nested / "guide.md").write_text("nested guide", encoding="utf-8")
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="pdf", filename="guide.md")

    assert outcome.ok
    assert outcome.content == "nested guide"


def test_project_file_with_same_name_does_not_block_skill_search(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, "pdf")
    (tmp_path / "guide.md").write_text("项目根目录的同名文件", encoding="utf-8")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("技能目录内的文件", encoding="utf-8")
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="pdf", filename="guide.md")

    assert outcome.ok
    assert outcome.content == "技能目录内的文件"


def test_ambiguous_filenames_return_candidates(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, "pdf")
    (skill_dir / "a").mkdir()
    (skill_dir / "b").mkdir()
    (skill_dir / "a" / "guide.md").write_text("A", encoding="utf-8")
    (skill_dir / "b" / "guide.md").write_text("B", encoding="utf-8")
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="pdf", filename="guide.md")

    assert outcome.ok
    assert "找到多个名为 guide.md 的文件" in outcome.content
    assert ".agents/skills/pdf/a/guide.md" in outcome.content
    assert ".agents/skills/pdf/b/guide.md" in outcome.content


def test_unknown_skill_lists_available_names(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="missing")

    assert outcome.ok is False
    assert "未找到 Skill" in outcome.content
    assert "`pdf`" in outcome.content


def test_missing_attachment_reports_available_files(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, "pdf")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="pdf", filename="references/missing.md")

    assert outcome.ok is False
    assert "文件不存在" in outcome.content
    assert "references/guide.md" in outcome.content


def test_missing_or_non_string_arguments_fail_cleanly(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")
    registry = registry_for(tmp_path)

    assert invoke(registry, filename="guide.md").ok is False
    assert invoke(registry, name="", filename="guide.md").ok is False
    assert invoke(registry, name="pdf", filename=123).ok is False


# ---------------------------------------------------------------------------
# 安全边界
# ---------------------------------------------------------------------------
def test_attachment_cannot_escape_skill_directory(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")
    write_skill(tmp_path, "other")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    registry = registry_for(tmp_path)

    other_skill_file = invoke(registry, name="pdf", filename=".agents/skills/other/SKILL.md")
    absolute_outside = invoke(registry, name="pdf", filename=str(tmp_path / "secret.txt"))
    parent_escape = invoke(registry, name="pdf", filename="../other/SKILL.md")

    for outcome in (other_skill_file, absolute_outside, parent_escape):
        assert outcome.ok is False
        assert "Skill 目录之外" in outcome.content


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, "pdf")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (skill_dir / "leak.md").symlink_to(secret)
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="pdf", filename="leak.md")

    assert outcome.ok is False
    assert "Skill 目录之外" in outcome.content


# ---------------------------------------------------------------------------
# 与 AgentService / composition 协作
# ---------------------------------------------------------------------------
def test_skill_body_enters_context_only_after_load_skill(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")
    catalog = SkillCatalog.discover(tmp_path)
    registry = build_tool_registry(tmp_path, skills=catalog)
    system_prompt = compose_system_prompt("你是测试助手", catalog)

    arguments = json.dumps({"name": "pdf"}, ensure_ascii=False)
    first = [*tool_call_chunks(0, "call_1", "loadSkill", arguments), StreamCompleted("tool_calls")]
    second = [TextDelta("已加载技能"), StreamCompleted("stop")]
    llm = FakeLLM([first, second])
    agent = AgentService(llm=llm, tools=registry, settings=AgentSettings(system_prompt=system_prompt))
    session = ChatSession.create(system_prompt)

    async def run() -> list[object]:
        return [event async for event in agent.run_turn(session, "用 pdf 技能处理文件")]

    asyncio.run(run())

    first_request = llm.calls[0]
    assert first_request[0].role is Role.SYSTEM
    assert "处理 PDF 文件" in first_request[0].content
    assert "只在调用 loadSkill 后才应该进入上下文" not in first_request[0].content

    tool_result = llm.calls[1][-1]
    assert tool_result.role is Role.TOOL
    assert "只在调用 loadSkill 后才应该进入上下文" in tool_result.content

    history = session.conversation.history()
    assert [message.role for message in history] == [Role.USER, Role.ASSISTANT]
    assert history[-1].content == "已加载技能"


def test_composition_injects_skills_and_registers_load_skill(tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")
    config = AppConfig(
        llm=LLMSettings(base_url="http://localhost:1/v1", api_key="sk-test", model="test-model"),
        workspace=tmp_path,
    )

    runtime = build_runtime(config=config, project_root=tmp_path)

    try:
        assert "- `pdf`: 处理 PDF 文件" in runtime.agent.settings.system_prompt
        assert runtime.session.conversation.system_prompt == runtime.agent.settings.system_prompt
        assert "loadSkill" in {definition.name for definition in runtime.info.tools}
    finally:
        asyncio.run(runtime.aclose())


def test_composition_uses_startup_cwd_as_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_skill(tmp_path, "pdf")
    monkeypatch.chdir(tmp_path)
    config = AppConfig(
        llm=LLMSettings(base_url="http://localhost:1/v1", api_key="sk-test", model="test-model"),
        workspace=tmp_path,
    )

    runtime = build_runtime(config=config)

    try:
        assert "- `pdf`: 处理 PDF 文件" in runtime.agent.settings.system_prompt
    finally:
        asyncio.run(runtime.aclose())


def test_composition_without_skills_keeps_default_prompt(tmp_path: Path) -> None:
    config = AppConfig(
        llm=LLMSettings(base_url="http://localhost:1/v1", api_key="sk-test", model="test-model"),
        workspace=tmp_path,
    )

    runtime = build_runtime(config=config, project_root=tmp_path)

    try:
        assert runtime.agent.settings.system_prompt == DEFAULT_SYSTEM_PROMPT
        assert "You have access to the following skills" not in runtime.agent.settings.system_prompt
    finally:
        asyncio.run(runtime.aclose())

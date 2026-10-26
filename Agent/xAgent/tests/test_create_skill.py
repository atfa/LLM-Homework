"""createSkill：生成规范 SKILL.md、防覆盖与运行期技能列表刷新。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.fakes import FakeLLM, make_runtime
from xagent.core.domain.events import StreamCompleted, TextDelta, ToolCallDelta
from xagent.core.domain.messages import Role
from xagent.core.domain.tools import ToolCall, ToolOutcome
from xagent.core.infrastructure.skills import SkillCatalog
from xagent.core.infrastructure.tools.catalog import build_tool_registry


def registry_for(root: Path):
    return build_tool_registry(root, skills=SkillCatalog.discover(root))


def invoke(registry, **arguments: object) -> ToolOutcome:
    call = ToolCall(id="call_skill", name="createSkill", arguments=json.dumps(arguments, ensure_ascii=False))
    return asyncio.run(registry.invoke(call))


def tool_call_chunks(index: int, call_id: str, name: str, arguments: str) -> list[object]:
    return [
        ToolCallDelta(index=index, id=call_id, name=name),
        ToolCallDelta(index=index, arguments=arguments[: len(arguments) // 2]),
        ToolCallDelta(index=index, arguments=arguments[len(arguments) // 2 :]),
    ]


def skill_file(root: Path, name: str) -> Path:
    return root / ".agents" / "skills" / name / "SKILL.md"


# ---------------------------------------------------------------------------
# 生成 SKILL.md
# ---------------------------------------------------------------------------
def test_create_skill_from_body_generates_frontmatter(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="PDF Forms", skillContext="# PDF Forms\n\n填写 PDF 表单。")

    assert outcome.ok, outcome.content
    text = skill_file(tmp_path, "pdf-forms").read_text(encoding="utf-8")
    assert text.startswith('---\nname: pdf-forms\ndescription: "PDF Forms"\n---\n')
    assert "# PDF Forms\n\n填写 PDF 表单。" in text
    assert "已创建 Skill" in outcome.content

    # 同一个 registry 中的 loadSkill 已经能看到新 Skill
    load_call = ToolCall(id="call_load", name="loadSkill", arguments=json.dumps({"name": "pdf-forms"}))
    loaded = asyncio.run(registry.invoke(load_call))
    assert loaded.ok
    assert "填写 PDF 表单" in loaded.content


def test_create_skill_keeps_valid_full_document_unchanged(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    document = "---\nname: pdf-forms\ndescription: 处理 PDF 表单\nlicense: MIT\n---\n# PDF Forms\n\n正文内容\n"

    outcome = invoke(registry, name="pdf-forms", skillContext=document)

    assert outcome.ok, outcome.content
    assert skill_file(tmp_path, "pdf-forms").read_text(encoding="utf-8") == document


def test_create_skill_fixes_frontmatter_name_and_keeps_extra_fields(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    document = "---\nname: old-name\ndescription: 处理 PDF 表单\nlicense: MIT\n---\n# 正文\n"

    outcome = invoke(registry, name="new-name", skillContext=document)

    assert outcome.ok, outcome.content
    text = skill_file(tmp_path, "new-name").read_text(encoding="utf-8")
    assert "name: new-name" in text
    assert "name: old-name" not in text
    assert "description:" in text
    assert "license: MIT" in text
    assert "# 正文" in text


def test_create_skill_adds_missing_description(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    document = "---\nname: pdf-forms\n---\n# PDF Forms\n\n正文\n"

    outcome = invoke(registry, name="pdf-forms", skillContext=document)

    assert outcome.ok, outcome.content
    text = skill_file(tmp_path, "pdf-forms").read_text(encoding="utf-8")
    assert 'description: "PDF Forms"' in text


def test_name_is_normalized_to_lowercase_hyphens(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="My_New  Skill", skillContext="# New Skill\n正文")

    assert outcome.ok, outcome.content
    assert skill_file(tmp_path, "my-new-skill").exists()


def test_invalid_names_are_rejected(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)

    for bad_name in ("中文技能", "bad/name", "../escape", "bad.name", ""):
        outcome = invoke(registry, name=bad_name, skillContext="# 内容")
        assert outcome.ok is False, bad_name
        assert "名称" in outcome.content or "不能为空" in outcome.content or "非空字符串" in outcome.content

    assert not (tmp_path / ".agents" / "escape").exists()


def test_malformed_frontmatter_is_rejected_without_creating_files(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)

    outcome = invoke(
        registry,
        name="broken",
        skillContext="---\nname: broken\ndescription: 缺少结束线\n# 正文",
    )

    assert outcome.ok is False
    assert "结束分隔线" in outcome.content
    assert not skill_file(tmp_path, "broken").exists()


def test_empty_context_and_missing_arguments_fail(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)

    assert invoke(registry, name="empty", skillContext="   ").ok is False
    assert invoke(registry, name="empty").ok is False
    assert invoke(registry, skillContext="# 内容").ok is False


# ---------------------------------------------------------------------------
# 防覆盖
# ---------------------------------------------------------------------------
def test_existing_skill_directory_is_not_overwritten(tmp_path: Path) -> None:
    existing = skill_file(tmp_path, "existing")
    existing.parent.mkdir(parents=True)
    existing.write_text("original content", encoding="utf-8")
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="existing", skillContext="# 新内容")

    assert outcome.ok is False
    assert "已存在" in outcome.content
    assert existing.read_text(encoding="utf-8") == "original content"


def test_existing_catalog_skill_name_is_not_overwritten(tmp_path: Path) -> None:
    original = tmp_path / ".agents" / "skills" / "original-dir"
    original.mkdir(parents=True)
    (original / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: 已有技能\n---\n# 原文\n",
        encoding="utf-8",
    )
    registry = registry_for(tmp_path)

    outcome = invoke(registry, name="pdf", skillContext="# 覆盖")

    assert outcome.ok is False
    assert "已存在" in outcome.content
    assert not skill_file(tmp_path, "pdf").exists()
    assert "# 原文" in (original / "SKILL.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 当前会话无需重启即可发现新 Skill
# ---------------------------------------------------------------------------
def test_create_skill_refreshes_session_prompt_and_is_loadable_in_same_turn(tmp_path: Path) -> None:
    create_args = json.dumps(
        {"name": "auto-skill", "skillContext": "# Auto Skill\n\n自动创建的技能。"},
        ensure_ascii=False,
    )
    load_args = json.dumps({"name": "auto-skill"}, ensure_ascii=False)
    first = [*tool_call_chunks(0, "call_create", "createSkill", create_args), StreamCompleted("tool_calls")]
    second = [*tool_call_chunks(0, "call_load", "loadSkill", load_args), StreamCompleted("tool_calls")]
    third = [TextDelta("已完成"), StreamCompleted("stop")]
    llm = FakeLLM([first, second, third])
    runtime = make_runtime(tmp_path, [], llm=llm)

    async def run() -> list[object]:
        return [event async for event in runtime.agent.run_turn(runtime.session, "创建一个技能并使用它")]

    events = asyncio.run(run())

    assert skill_file(tmp_path, "auto-skill").exists()
    prompt = runtime.session.conversation.system_prompt
    assert "- `auto-skill`: Auto Skill" in prompt
    assert runtime.agent.settings.system_prompt == prompt

    # 创建后的下一次模型调用已经带上了新技能列表
    assert llm.calls[1][0].role is Role.SYSTEM
    assert "- `auto-skill`: Auto Skill" in llm.calls[1][0].content

    # 同一轮里紧接着调用 loadSkill，正文已经可以读取
    tool_result = llm.calls[2][-1]
    assert tool_result.role is Role.TOOL
    assert "自动创建的技能" in tool_result.content

    history = runtime.session.conversation.history()
    assert [message.role for message in history] == [Role.USER, Role.ASSISTANT]
    assert history[-1].content == "已完成"
    assert events

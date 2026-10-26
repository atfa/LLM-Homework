"""工具注册表 + 10 个文件工具的行为测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from xagent.core.domain.tools import ToolCall, ToolOutcome
from xagent.core.infrastructure.tools.catalog import build_tool_registry
from xagent.core.infrastructure.tools.registry import ToolRegistryImpl


def invoke(registry: ToolRegistryImpl, name: str, **arguments: object) -> ToolOutcome:
    call = ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments, ensure_ascii=False))
    return asyncio.run(registry.invoke(call))


def test_default_registry_has_seventeen_tools(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    assert len(registry) == 17
    assert registry.names() == (
        "getDate",
        "isLeapYear",
        "getCwd",
        "listDir",
        "getFileType",
        "readFile",
        "createFile",
        "editFile",
        "createDir",
        "deleteDir",
        "deleteFile",
        "renamePath",
        "webSearch",
        "fetchWeb",
        "runCli",
        "loadSkill",
        "createSkill",
    )
    assert all(definition.parameters["type"] == "object" for definition in registry.definitions())


def test_get_cwd_and_leap_year(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    cwd = invoke(registry, "getCwd")
    assert cwd.ok and str(tmp_path) in cwd.content

    leap = invoke(registry, "isLeapYear", year=2024)
    assert leap.ok and "是闰年" in leap.content
    not_leap = invoke(registry, "isLeapYear", year=2023)
    assert not_leap.ok and "不是闰年" in not_leap.content
    assert invoke(registry, "isLeapYear", year="abc").ok is False


def test_file_lifecycle(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    created = invoke(registry, "createFile", directory=".", filename="note.txt", content="第一行\n")
    assert created.ok, created.content
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "第一行\n"

    # 不覆盖
    duplicate = invoke(registry, "createFile", directory=".", filename="note.txt", content="x")
    assert not duplicate.ok and "已存在" in duplicate.content

    read = invoke(registry, "readFile", path="note.txt")
    assert read.ok and read.content == "第一行\n"

    appended = invoke(registry, "editFile", path="note.txt", content="第二行\n", mode="append")
    assert appended.ok
    assert invoke(registry, "readFile", path="note.txt").content == "第一行\n第二行\n"

    overwritten = invoke(registry, "editFile", path="note.txt", content="新内容", mode="overwrite")
    assert overwritten.ok
    assert invoke(registry, "readFile", path="note.txt").content == "新内容"

    file_type = invoke(registry, "getFileType", path="note.txt")
    assert file_type.ok
    assert "纯文本（utf-8）" in file_type.content
    assert ".txt" in file_type.content

    listing = invoke(registry, "listDir", path=".")
    assert listing.ok and "note.txt" in listing.content

    renamed = invoke(registry, "renamePath", path="note.txt", new_name="renamed.txt")
    assert renamed.ok
    assert not (tmp_path / "note.txt").exists()
    assert (tmp_path / "renamed.txt").exists()

    deleted = invoke(registry, "deleteFile", path="renamed.txt")
    assert deleted.ok
    assert not (tmp_path / "renamed.txt").exists()


def test_nested_directories_and_recursive_delete(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)

    created = invoke(registry, "createDir", path="a/b/c")
    assert created.ok
    assert (tmp_path / "a" / "b" / "c").is_dir()

    assert invoke(registry, "createDir", path="a/b/c").ok is False
    assert invoke(registry, "createFile", directory="a/b/c", filename="x.txt").ok

    blocked = invoke(registry, "deleteDir", path="a")
    assert not blocked.ok and "recursive=true" in blocked.content

    removed = invoke(registry, "deleteDir", path="a", recursive=True)
    assert removed.ok and "连同 3 项内容" in removed.content
    assert not (tmp_path / "a").exists()

    assert invoke(registry, "deleteDir", path=".").ok is False


def test_path_escape_and_validation(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    (tmp_path.parent / "outside.txt").write_text("secret", encoding="utf-8")

    escaped = invoke(registry, "readFile", path="../outside.txt")
    assert not escaped.ok and "工作目录之外" in escaped.content

    absolute = invoke(registry, "readFile", path=str(tmp_path.parent / "outside.txt"))
    assert not absolute.ok

    bad_name = invoke(registry, "createFile", directory=".", filename="a/b.txt", content="x")
    assert not bad_name.ok and "路径分隔符" in bad_name.content

    assert invoke(registry, "createFile", directory="missing", filename="x.txt").ok is False


def test_binary_and_oversized_files_are_rejected(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
    (tmp_path / "big.txt").write_text("x" * (256 * 1024 + 1), encoding="utf-8")

    binary = invoke(registry, "readFile", path="bin.dat")
    assert not binary.ok and "不是纯文本" in binary.content

    too_big = invoke(registry, "readFile", path="big.txt")
    assert not too_big.ok and "文件过大" in too_big.content

    file_type = invoke(registry, "getFileType", path="bin.dat")
    assert file_type.ok and "二进制" in file_type.content


def test_edit_does_not_create_missing_file(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    outcome = invoke(registry, "editFile", path="missing.txt", content="x")
    assert not outcome.ok and "不存在" in outcome.content
    assert not (tmp_path / "missing.txt").exists()


def test_rename_rejects_existing_target_and_path_separators(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")

    conflict = invoke(registry, "renamePath", path="a.txt", new_name="b.txt")
    assert not conflict.ok and "拒绝覆盖" in conflict.content

    separator = invoke(registry, "renamePath", path="a.txt", new_name="sub/a.txt")
    assert not separator.ok and "路径分隔符" in separator.content


def test_registry_handles_bad_json_and_unknown_tool(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    bad = asyncio.run(registry.invoke(ToolCall(id="1", name="listDir", arguments="not-json")))
    assert not bad.ok and "JSON" in bad.content

    unknown = asyncio.run(registry.invoke(ToolCall(id="2", name="nope", arguments="{}")))
    assert not unknown.ok and "未知工具" in unknown.content

    non_object = asyncio.run(registry.invoke(ToolCall(id="3", name="listDir", arguments="[1,2]")))
    assert not non_object.ok and "JSON 对象" in non_object.content


def test_registry_duplicate_registration(tmp_path: Path) -> None:
    registry = build_tool_registry(tmp_path)
    definition = registry.definitions()[0]
    with pytest.raises(ValueError):
        registry.register(definition, lambda _: "x")
    registry.register(definition, lambda _: "x", overwrite=True)
    registry.unregister(definition.name)
    assert definition.name not in registry

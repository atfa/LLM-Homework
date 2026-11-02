"""本地 JSONL 归档和有界全文检索。每行一条原始消息，行号从 1 开始。"""

from __future__ import annotations

import heapq
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..domain.messages import ChatMessage
from ..domain.tools import ToolDefinition
from ..ports.tools import ToolHandler


class LocalContextArchive:
    def __init__(self, project_root: str | Path) -> None:
        self.directory = Path(project_root).resolve() / ".agents" / "context"

    def save(self, messages: Sequence[ChatMessage]) -> tuple[str, str]:
        self.directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone()
        archive_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:12]
        path = self.directory / f"{archive_id}.jsonl"
        created = False
        try:
            # x 模式防覆盖；flush/fsync 成功后才将编号交给调用方。
            with path.open("x", encoding="utf-8") as stream:
                created = True
                for message in messages:
                    stream.write(json.dumps(message.to_wire(), ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        return archive_id, now.strftime("%Y-%m-%d %H:%M:%S")

    def recall(self, arguments: Mapping[str, Any]) -> str:
        text = arguments.get("text")
        archive_id = arguments.get("id")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text 必须是非空字符串")
        if len(text) > 500:
            raise ValueError("text 不能超过 500 个字符")
        if archive_id is not None and (
            not isinstance(archive_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", archive_id)
        ):
            raise ValueError("id 必须是归档编号，不允许包含路径")
        paths = (
            [self.directory / f"{archive_id}.jsonl"]
            if archive_id is not None
            else sorted(self.directory.glob("*.jsonl"))
        )
        if archive_id is not None and not paths[0].is_file():
            return "归档不存在"
        query = text.strip().casefold()
        words = list(dict.fromkeys(query.split()))
        best: list[tuple[int, int, dict[str, Any]]] = []
        serial = 0
        warnings: list[str] = []
        for path in paths:
            if path.is_symlink():
                continue
            try:
                with path.open(encoding="utf-8") as stream:
                    for line_number, line in enumerate(stream, 1):
                        try:
                            message = json.loads(line)
                        except ValueError:
                            if len(warnings) < 5:
                                warnings.append(f"{path.stem}:{line_number} 无效 JSON")
                            continue
                        original = json.dumps(message, ensure_ascii=False)
                        folded = original.casefold()
                        score = 10 * (query in folded) + sum(word in folded for word in words)
                        if not score:
                            continue
                        # 片段围绕首个匹配位置；不是仅返回消息开头。
                        match = min((folded.find(word) for word in words if word in folded), default=0)
                        start = max(0, match - 200)
                        hit = {
                            "id": path.stem,
                            "line": line_number,
                            "offset": start,
                            "snippet": original[start : start + 1100],
                        }
                        serial += 1
                        item = (score, serial, hit)
                        if len(best) < 6:
                            heapq.heappush(best, item)
                        elif item[:2] > best[0][:2]:
                            heapq.heapreplace(best, item)
            except (OSError, UnicodeError) as exc:
                if len(warnings) < 5:
                    warnings.append(f"{path.stem}: {type(exc).__name__}")
        return json.dumps(
            {
                "results": [item[2] for item in sorted(best, reverse=True)],
                "note": "最多返回 6 个片段，每片段 1100 字符；可缩小关键词或指定 id。",
                "warnings": warnings[:5],
            },
            ensure_ascii=False,
        )


def context_tools(archive: LocalContextArchive) -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    definition = ToolDefinition(
        name="recallContext",
        description="摘要缺少历史细节时，全文关键词检索原始上下文归档，返回有来源行号的有限片段。",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "可选的归档编号；省略则搜索所有归档"},
                "text": {"type": "string", "description": "必需的关键词或文本，空格分隔支持多关键词"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    return ((definition, archive.recall),)

"""网页正文提取工具：通过 subprocess 调用本地 curl 获取 HTML，再用 trafilatura 提取并转为 Markdown。"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from typing import Any

from ....domain.errors import ToolError
from ....domain.tools import ToolDefinition
from ....ports.tools import ToolHandler

try:
    import trafilatura
except Exception:  # pragma: no cover - 运行时依赖
    trafilatura = None  # type: ignore[assignment]

FETCH_WEB = ToolDefinition(
    name="fetchWeb",
    description="获取指定网页的主要内容。通过本地 curl 抓取 HTML，使用 trafilatura 提取正文并转换为 Markdown，"
    "去除导航栏、广告、页脚等无关内容，返回 Markdown 正文及原始 URL。",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要获取内容的网页 URL（http/https）",
            },
        },
        "required": ["url"],
    },
)

DEFAULT_TIMEOUT_S = 30
CURL_USER_AGENT = "xAgent/0.1.0 (fetchWeb)"


def _validate_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ToolError("url 必须是非空字符串")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ToolError("url 必须是 http:// 或 https:// 开头")
    return url


def fetch_web(arguments: Mapping[str, Any]) -> str:
    url = arguments.get("url")
    if not isinstance(url, str):
        raise ToolError("url 必须是字符串")
    url = _validate_url(url)

    if trafilatura is None:
        raise ToolError("trafilatura 未安装，无法提取网页正文")

    # 使用参数列表方式调用 curl，不使用 shell=True
    command = [
        "curl",
        "-L",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "10",
        "--max-time",
        str(DEFAULT_TIMEOUT_S),
        "-A",
        CURL_USER_AGENT,
        "-H",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H",
        "Accept-Language: zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        url,
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_S + 5,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError("未找到本地 curl 程序") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("网页访问超时") from exc
    except OSError as exc:
        raise ToolError(f"网页访问失败：{exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        if stderr:
            raise ToolError(f"curl 获取网页失败（{completed.returncode}）：{stderr}")
        raise ToolError(f"curl 获取网页失败（{completed.returncode}）")

    html = completed.stdout or ""
    if not html.strip():
        raise ToolError("获取到空的网页内容")

    try:
        # 尽量保留标题、段落、列表、表格、链接，去除导航、广告、页脚等
        extracted = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            include_images=False,
            favor_precision=True,
        )
    except Exception as exc:
        raise ToolError(f"HTML 解析失败：{exc}") from exc

    if not extracted or not extracted.strip():
        raise ToolError("无法从网页中提取正文内容")

    content = extracted.strip()
    return f"# 来源 URL\n{url}\n\n# 正文内容\n\n{content}"


def fetch_web_tools() -> tuple[tuple[ToolDefinition, ToolHandler], ...]:
    return ((FETCH_WEB, fetch_web),)


__all__ = ["FETCH_WEB", "fetch_web", "fetch_web_tools"]

"""AnythingLLM MCP 服务端 —— 只暴露一个 list_workspaces 工具（Streamable HTTP）。

后端地址可用环境变量 ANYTHINGLLM_API 覆盖（默认 http://127.0.0.1:3001）。
服务端监听可用 ANYTHINGLLM_MCP_HOST / ANYTHINGLLM_MCP_PORT 覆盖（默认 127.0.0.1:8091）。
"""

import json
import os
import urllib.request

from mcp.server.mcpserver import MCPServer  # mcp 2.x 中 FastMCP 已改名为 MCPServer

API_BASE = os.environ.get("ANYTHINGLLM_API", "http://127.0.0.1:3001").rstrip("/")

mcp = MCPServer(
    name="anythingllm",
    instructions="AnythingLLM 数据服务：列出当前的所有工作区（slug）。",
)


@mcp.tool()
def list_workspaces() -> dict:
    """列出 AnythingLLM 当前的所有工作区，返回每个工作区的 id、name、slug、创建时间。"""
    url = f"{API_BASE}/api/workspaces"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"AnythingLLM 服务不可用（{API_BASE}）：{exc}") from exc
    return {"workspaces": body.get("workspaces", [])}


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("ANYTHINGLLM_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("ANYTHINGLLM_MCP_PORT", "8091")),
        streamable_http_path="/mcp",
        stateless_http=True,
    )

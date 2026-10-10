# AnythingLLM MCP

极简 AnythingLLM MCP 服务端：只暴露一个 `list_workspaces` 工具，通过 **Streamable HTTP** 提供服务，运行在 **8091** 端口，后端调用本机 AnythingLLM（默认 <http://127.0.0.1:3001>）的 `GET /api/workspaces`。

> 注：官方 `mcp` SDK 2.x 把 `FastMCP` 改名为 `MCPServer`（同一个类），本项目使用 2.x。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 启动

```powershell
.\.venv\Scripts\python.exe server.py
```

MCP 端点：<http://127.0.0.1:8091/mcp>

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ANYTHINGLLM_API` | `http://127.0.0.1:3001` | AnythingLLM 服务地址 |
| `ANYTHINGLLM_MCP_HOST` | `127.0.0.1` | MCP 服务监听地址 |
| `ANYTHINGLLM_MCP_PORT` | `8091` | MCP 服务监听端口 |

## 客户端接入

在 MCP 客户端（如 Claude Desktop / OpenCode）里用 `http` 类型接入，URL 填 `<http://127.0.0.1:8091/mcp>`。

## 工具

### `list_workspaces`

列出当前 AnythingLLM 的所有工作区。无参数。

返回：

```json
{ "workspaces": [ { "id": 1, "name": "我的工作区", "slug": "…", "createdAt": "…" } ] }
```

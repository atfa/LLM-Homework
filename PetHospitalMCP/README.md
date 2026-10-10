# 宠物医院 MCP

一个极简的宠物医院 MCP 服务端：只暴露一个 `list_pets` 工具，通过 **Streamable HTTP** 提供服务，后端调用 `PetHospitalServer` 的 REST 接口。

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

MCP 端点：<http://127.0.0.1:8090/mcp>

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PET_HOSPITAL_API` | `http://127.0.0.1:8080` | 宠物医院后端 REST 地址 |
| `PET_HOSPITAL_MCP_HOST` | `127.0.0.1` | MCP 服务监听地址 |
| `PET_HOSPITAL_MCP_PORT` | `8090` | MCP 服务监听端口 |

## 客户端接入

在 MCP 客户端（如 Claude Desktop / OpenCode）里用 `http` 类型接入，URL 填 `<http://127.0.0.1:8090/mcp>`。

## 工具

### `list_pets`

查询宠物档案列表，支持过滤 / 排序 / 分页。参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | str | - | 关键词全文检索（空格分词，含病历） |
| `species` | str | - | 种类：犬/猫/兔/鸟/仓鼠/爬宠/其他 |
| `status` | str | - | 就诊状态：待就诊/就诊中/住院中/已康复/慢性病随访 |
| `doctor` | str | - | 主治医生姓名 |
| `owner_name` | str | - | 主人姓名 |
| `owner_phone` | str | - | 主人电话 |
| `disease` | str | - | 疾病 / 主要诊断 |
| `min_cost` / `max_cost` | float | - | 总花费区间 |
| `sort_by` | str | `createdAt` | 排序字段：totalCost/visitCount/createdAt/name… |
| `order` | str | `desc` | `asc` / `desc` |
| `page` | int | `1` | 页码 |
| `page_size` | int | `10` | 每页条数 |

返回信封里的 `data`：`{ items, total, page, pageSize, totalPages, totalCost }`。

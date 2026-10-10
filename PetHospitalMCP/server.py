"""宠物医院 MCP 服务端 —— 只暴露一个 list_pets 工具（Streamable HTTP）。

后端 REST 地址可用环境变量 PET_HOSPITAL_API 覆盖（默认 http://127.0.0.1:8080）。
服务端监听可用 PET_HOSPITAL_MCP_HOST / PET_HOSPITAL_MCP_PORT 覆盖（默认 127.0.0.1:8090）。
"""

import json
import os
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer  # mcp 2.x 中 FastMCP 已改名为 MCPServer

API_BASE = os.environ.get("PET_HOSPITAL_API", "http://127.0.0.1:8080").rstrip("/")

mcp = MCPServer(
    name="pet-hospital",
    instructions="宠物医院数据查询服务：按条件查询宠物医院的宠物档案列表。",
)


def _get(path: str, params: dict) -> dict:
    """GET 后端接口，返回响应信封里的 data 字段。"""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{API_BASE}{path}" + (f"?{query}" if query else "")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"后端服务不可用（{API_BASE}）：{exc}") from exc
    if body.get("code") != 200:
        raise RuntimeError(f"后端返回错误：{body.get('message')}")
    return body.get("data")


@mcp.tool()
def list_pets(
    q: str | None = None,
    species: str | None = None,
    status: str | None = None,
    doctor: str | None = None,
    owner_name: str | None = None,
    owner_phone: str | None = None,
    disease: str | None = None,
    min_cost: float | None = None,
    max_cost: float | None = None,
    sort_by: str = "createdAt",
    order: str = "desc",
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """查询宠物医院的宠物档案列表（支持过滤、排序、分页）。

    Args:
        q: 关键词全文检索（跨字段，空格分词，含病历全文）
        species: 种类，如 犬/猫/兔/鸟/仓鼠/爬宠/其他
        status: 就诊状态：待就诊/就诊中/住院中/已康复/慢性病随访
        doctor: 主治医生姓名
        owner_name: 主人姓名
        owner_phone: 主人电话
        disease: 疾病 / 主要诊断
        min_cost: 总花费下限
        max_cost: 总花费上限
        sort_by: 排序字段，如 totalCost/visitCount/createdAt/name
        order: asc 升序 或 desc 降序
        page: 页码，从 1 开始
        page_size: 每页条数
    """
    params = {
        "q": q,
        "species": species,
        "status": status,
        "doctor": doctor,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "disease": disease,
        "min": min_cost,
        "max": max_cost,
        "sortBy": sort_by,
        "order": order,
        "page": page,
        "pageSize": page_size,
    }
    return _get("/api/v1/pets", params)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("PET_HOSPITAL_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("PET_HOSPITAL_MCP_PORT", "8090")),
        streamable_http_path="/mcp",
        stateless_http=True,
    )

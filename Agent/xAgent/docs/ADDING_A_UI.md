# 新增界面（Web / Desktop）

xAgent 的 core 不依赖 Textual。任何界面只要满足下面的契约，就可以复用
`AgentService`、15 个工具、流式事件与会话管理。

## 1. 契约

```python
from xagent.core.composition import build_runtime
from xagent.core.application.events import (
    TurnStarted, IterationStarted, AssistantTextDelta, IterationCompleted,
    ToolCallStarted, ToolCallFinished, TurnCompleted, TurnFailed,
)

runtime = build_runtime()          # 读取 config.ini / 环境变量
agent = runtime.agent
session = runtime.session
info = runtime.info                # model / workspace / tools（只读，无 API Key）
```

一轮对话：

```python
async for event in agent.run_turn(session, prompt):
    ...
```

取消：对消费任务调用 `task.cancel()`（或界面侧的停止按钮），
`run_turn` 会抛 `CancelledError`，且不写入历史。

关闭：`await runtime.aclose()`。

## 2. 必须处理的 8 个事件

| 事件 | 界面行为 |
| --- | --- |
| `TurnStarted` | 显示用户消息 |
| `IterationStarted` | 显示「思考中 / 第 N 次」 |
| `AssistantTextDelta` | 追加流式文本 |
| `IterationCompleted` | 关闭当前回答块 |
| `ToolCallStarted` | 显示工具运行中（名称 + 参数） |
| `ToolCallFinished` | 显示结果 / 错误 + 耗时 |
| `TurnCompleted` | 完成状态、耗时、工具次数 |
| `TurnFailed` | 可读错误提示 |

注意：`AssistantTextDelta` 可能出现在工具调用之前、之后，甚至多轮之间；
每次 `IterationCompleted` 都应该结束当前文本块，下一次 delta 再开新块。

## 3. FastAPI + SSE 示例（草图）

```python
# web/server.py —— 仅示意，未包含鉴权 / 并发控制 / 断线重连
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from xagent.core.application.events import AssistantTextDelta, ToolCallFinished, TurnCompleted, TurnFailed
from xagent.core.composition import build_runtime

app = FastAPI()
runtime = build_runtime()


class Ask(BaseModel):
    prompt: str


@app.post("/chat")
async def chat(body: Ask) -> StreamingResponse:
    async def events():
        try:
            async for event in runtime.agent.run_turn(runtime.session, body.prompt):
                if isinstance(event, AssistantTextDelta):
                    yield f"event: delta\ndata: {event.text!r}\n\n"
                elif isinstance(event, ToolCallFinished):
                    yield f"event: tool\ndata: {event.call.name}:{event.outcome.ok}\n\n"
                elif isinstance(event, TurnCompleted):
                    yield f"event: done\ndata: {event.elapsed_s:.2f}s\n\n"
                elif isinstance(event, TurnFailed):
                    yield f"event: error\ndata: {event.message!r}\n\n"
        finally:
            pass

    return StreamingResponse(events(), media_type="text/event-stream")


@app.on_event("shutdown")
async def shutdown() -> None:
    await runtime.aclose()
```

生产环境还需要：会话隔离（每个用户一个 `ChatSession`）、请求排队、
断线取消、鉴权、输出转义（不要把模型文本直接当 HTML）。

## 4. Desktop（PySide6 / Tk）示例（草图）

```python
import asyncio
from PySide6.QtCore import QThread, Signal

from xagent.core.application.events import AssistantTextDelta, TurnCompleted, TurnFailed
from xagent.core.composition import build_runtime


class AgentThread(QThread):
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt
        self._runtime = build_runtime()

    def run(self) -> None:
        asyncio.run(self._consume())

    async def _consume(self) -> None:
        try:
            async for event in self._runtime.agent.run_turn(self._runtime.session, self._prompt):
                if isinstance(event, AssistantTextDelta):
                    self.delta.emit(event.text)
                elif isinstance(event, TurnCompleted):
                    self.finished.emit(event.text)
                elif isinstance(event, TurnFailed):
                    self.failed.emit(event.message)
        finally:
            await self._runtime.aclose()
```

若桌面框架本身支持 asyncio（如 qasync），可以直接在当前事件循环中消费，
无需额外线程。关键是：**QThread / 主线程只做渲染，不直接发 HTTP**。

## 5. 规则

- 不要在界面层 import `xagent.core.infrastructure.*`；只通过 `build_runtime()` 拿运行时。
- 不要在界面层自己拼 JSON 消息或解析 SSE；那是基础设施层的职责。
- 不要绕开 `AgentService` 直接调工具；否则历史、轮数上限、截断都会失效。
- 新界面应放在 `xagent/<name>/`（如 `xagent/web/`、`xagent/desktop/`），与 `tui/` 平级；
  并在 `tests/test_layering.py` 里为它补一个与 `test_tui_only_depends_on_core_contract`
  同构的检查（允许依赖 core 契约层 + 它需要的第三方包，如 `fastapi`、`PySide6`），
  同时确认 `test_core_never_imports_tui` 的命名已涵盖新界面包（core 不得依赖任何界面）。
- 会话状态由 `ChatSession` 持有；多个用户/窗口请各自创建 session，不要共享单例。

## 6. 检查清单

- [ ] 订阅全部 8 个事件，能区分「生成中 / 工具中 / 完成 / 失败 / 取消」
- [ ] 流式文本按 `IterationCompleted` 分块，不把多轮拼成一段
- [ ] 取消按钮调用 `Worker.cancel()` / `task.cancel()`，不直接杀进程
- [ ] 启动失败（配置错误）给出可操作提示
- [ ] 退出时 `await runtime.aclose()`
- [ ] 不出现 `requests` / `open()` / `xagent.core.infrastructure` 的界面层 import
- [ ] 新增第三方依赖已写入 `pyproject.toml` 与分层测试白名单

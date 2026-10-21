# Practice02 · 带工具调用的流式对话脚本

在 Practice01（流式输出 + 多轮历史）的基础上加入 function calling：模型只负责决定「调用哪个工具、参数是什么」，真正执行由本地完成。完整说明见 [README.full.md](README.full.md)。

## 工具一览（共 12 个）

| 工具 | 作用 |
| --- | --- |
| `getDate` | 本机 `date` 命令取当前时间 |
| `isLeapYear` | Python 计算判断闰年 |
| `getCwd` | 当前工作目录（沙箱根） |
| `listDir` | 列出目录内容（不递归） |
| `getFileType` | 扩展名 / MIME / 文本或二进制 |
| `readFile` | 读取纯文本（限 256KB） |
| `createFile` | 新建文件，不覆盖 |
| `editFile` | 覆盖或追加纯文本 |
| `createDir` | 创建目录（可多级） |
| `deleteDir` | 删除目录（非空需 `recursive=true`） |
| `deleteFile` | 删除单个文件 |
| `renamePath` | 原地重命名，不移动、不覆盖 |

## 快速开始

```bash
pip install requests
cp config.ini.example config.ini   # 填入 base_url / model / api_key

python chat-stream-tool-getDate.py       # 1 个工具
python chat-stream-tool-isLeapYear.py    # 2 个工具
python chat-stream-tool-fileOps.py       # 12 个工具
```

缺少 `config.ini` 时脚本会直接抛 `KeyError: 'llm'`，请先复制模板并填写。

## 关键设计

- **工具调用循环**：模型返回 `tool_calls` → 本地执行 → 结果以 `role: "tool"` 追加 → 再次请求，直到给出最终回答；只有最终问答写入 `history`。
- **流式拼接**：`delta.tool_calls` 分块下发，按 `index` 累积 `arguments` 后再拼成完整 JSON。
- **路径沙箱**：所有文件操作经 `realpath` 解析后必须落在启动目录内，`../` 与外部符号链接均被拒绝。
- **纯文本限定**：含 `\x00` 或 UTF-8 / GB18030 均解码失败即视为二进制，`readFile` / `editFile` 拒绝处理。
- **破坏性操作需显式确认**：非空目录删除要 `recursive=true`；新建与编辑互不覆盖；重命名目标已存在则报错。

## 注意事项

- ⚠️ 工具循环是 `while True`，**没有最大轮数限制**，模型反复调用时只能 Ctrl+C 中断；生产环境建议加上限。
- `getDate` 输出即本机 `date` 命令格式，随系统 locale 变化；Windows 无 `date` 命令，需改用 `powershell -c Get-Date`。
- `config.ini` 含真实 API Key，请勿提交到公开仓库。

## 文件说明

```text
chat-stream-tool-getDate.py        # 第 1 版：getDate
chat-stream-tool-isLeapYear.py     # 第 2 版：+ isLeapYear
chat-stream-tool-fileOps.py        # 本版：+ 10 个文件工具
config.ini / config.ini.example    # 模型配置 / 模板
README.md                          # 本文件：精简版
README.full.md                     # 完整版说明（含实现细节与常见问题）
*.mmd                              # 架构图、沙箱图、时序图
```

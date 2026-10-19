# Practice02 · 带工具调用的流式对话脚本

> 本文件是 `README.md` 的修订版（README2），修正了与代码不一致的描述，并补充了几处风险说明。
> 主要修订见文末「相对 README.md 的修订记录」。

在 `Practice01/chat-stream-loop-history.py`（流式输出 + 多轮历史）的基础上加入 function calling：
模型只负责判断“该调用哪个工具、参数是什么”，真正执行在本地完成。目前有十二个工具：

| 工具 | 实现方式 | 作用 |
| --- | --- | --- |
| `getDate` | 本机 `date` 命令 | 当前日期时间 |
| `isLeapYear` | Python 计算 | 判断闰年 |
| `getCwd` | `os.getcwd()` | 当前工作目录（沙箱根） |
| `listDir` | `os.scandir()` | 列出目录下的文件/子目录 |
| `getFileType` | 扩展名 + `mimetypes` + 内容嗅探 | 扩展名、MIME、文本/二进制 |
| `readFile` | Python 读文件 | 读取纯文本内容（限 256KB） |
| `createFile` | `open(..., "x")` | 按目录+文件名新建，不覆盖 |
| `editFile` | Python 写文件 | 覆盖或追加纯文本 |
| `createDir` | `os.makedirs()` | 创建目录（可多级） |
| `deleteDir` | `shutil.rmtree()` / `os.rmdir()` | 删除目录，非空需 `recursive=true` |
| `deleteFile` | `os.remove()` | 删除单个文件 |
| `renamePath` | `os.rename()` | 重命名文件或目录（原地改名） |

可以确定计算结果、需要真实环境状态的内容（时间、闰年、文件系统）交给工具，
而不是让模型“凭感觉”回答。

## 文件说明

```text
Practice02/
├── chat-stream-tool-getDate.py     # 只带 getDate 工具（第 1 版）
├── chat-stream-tool-isLeapYear.py  # + isLeapYear（第 2 版）
├── chat-stream-tool-fileOps.py     # + 10 个文件工具（本版，共 12 个工具）
├── config.ini                      # 模型配置（本地使用，勿提交）
├── config.ini.example              # 配置模板
├── README.md                       # 初版说明
└── README2.md                      # 本文件：修订版说明
```

> 三个脚本都会读取**与本脚本同目录**的 `config.ini`。若该文件不存在，
> 脚本会在读取 `cfg["llm"]` 时直接抛出 `KeyError` 退出，不会给出友好提示——
> 请务必先执行下面的 `cp config.ini.example config.ini` 并填写内容。

## 快速开始

```bash
pip install requests          # 仅依赖 requests
cp config.ini.example config.ini   # 首次使用：填入 base_url / model / api_key
python chat-stream-tool-getDate.py       # 只有 getDate（1 个工具）
python chat-stream-tool-isLeapYear.py    # getDate + isLeapYear（2 个工具）
python chat-stream-tool-fileOps.py       # 12 个工具，含 10 个文件工具
```

输入「现在几点了？」，可以看到类似输出：

```text
please input your prompt: 现在几点了？
---

[调用工具] getDate({})
[工具结果] Fri Nov 14 10:23:41 CST 2025
---
现在是 2025 年 11 月 14 日（星期五）10:23:41，CST 时间。
```

> 注意：`getDate` 的输出就是本机 `date` 命令的默认输出，**格式随系统 locale 变化**：
> macOS/Linux 英文环境下形如 `Fri Nov 14 10:23:41 CST 2025`，
> 中文 locale 下才会出现 `2025年11月14日 星期五 10:23:41 CST` 这类中文格式。
> 中文回答里的“年月日”是模型根据工具结果自行措辞的，不是工具的输出格式。

输入「1900 年是闰年吗？」，可以看工具如何给出可验证的结论（而不是模型自己算）：

```text
please input your prompt: 1900年是闰年吗？
---

[调用工具] isLeapYear({"year": 1900})
[工具结果] 1900 年不是闰年（是整百年但不能被 400 整除）。
---
不是，1900 年是整百年，必须能被 400 整除才是闰年。
```

问「今年是闰年吗？」时，模型会先调 `getDate` 拿到当前年份，再调 `isLeapYear`，两个工具自动串联。

文件工具示例（在运行脚本的目录里操作）：

```text
please input your prompt: 看看当前目录，然后新建 demo.txt 写三行内容，再读出来
---
[调用工具] getCwd({})
[工具结果] /private/tmp/e2e
[调用工具] listDir({"path": "."})
[工具结果] 目录 .：共 2 项（含 0 个隐藏项）
docs/  [目录]
readme.txt  [文件, 5 B]
[调用工具] createFile({"directory": ".", "filename": "demo.txt", "content": "第一行\n第二行\n第三行\n"})
[工具结果] 已创建文件：demo.txt（写入 12 字符，30 B）
[调用工具] readFile({"path": "demo.txt"})
[工具结果] 文件：demo.txt（编码 UTF-8，共 30 B）
---
第一行
第二行
第三行
```

## 工具是怎么落地的

### 1. 声明工具（告诉模型“能做什么”）

```python
tools = [{
    "type": "function",
    "function": {
        "name": "getDate",
        "description": "获取当前电脑的日期和时间（通过执行本地 date 命令获得）",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]
```

请求时把 `tools` 一起发给模型。模型不会真的执行任何东西，只会返回「我想调用 `getDate`」。

第二个工具带参数，声明方式多一个 `parameters.properties.year`：

```python
tools.append({
    "type": "function",
    "function": {
        "name": "isLeapYear",
        "description": "判断指定年份是否为闰年，返回判断结果及依据（用本地 Python 代码精确计算，不靠猜测）",
        "parameters": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "要判断的年份，例如 2024；问“今年”时先调用 getDate 取当前年份",
                }
            },
            "required": ["year"],
        },
    },
})
```

### 2. 真正执行：调用本地 `date` 命令

```python
result = subprocess.run(["date"], capture_output=True, text=True, timeout=10)
return result.stdout.strip()
```

时间信息完全来自操作系统的 `date` 命令，Python 代码不参与计算日期。
（Windows 的 cmd 没有 `date` 命令，需要自行改用 `powershell -c Get-Date` 等。）
命令执行失败（非 0 退出码或无法启动）时，错误信息会作为工具结果交回模型，而不是让脚本崩溃。

### 2.1 用 Python 精确算闰年

```python
def is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
```

模型返回的 `arguments` 是 JSON 字符串（如 `{"year": 1900}`），先解析再计算；
若模型把年份写成字符串，也会尝试 `int()` 转换，失败则把错误信息作为工具结果交回模型。
另外 `parse_year()` 会把 `bool` 单独排除（`True` 是 `int` 的子类，容易误当 1 年），
并限制年份在公历 1–9999 年范围内。
`describe_leap_year()` 额外说明命中的是“不能被 4 整除”“能被 4 整除且不能被 100 整除”
“是整百年且能被 400 整除”“是整百年但不能被 400 整除”哪条规则，
让模型的解释有据可依，而不是复述一个它自己也不确定的答案。

### 2.2 文件工具：实现方式与边界

这十个工具全部用 Python 标准库完成（`os` / `shutil` / `mimetypes`），没有引入新依赖：

| 工具 | 关键实现 |
| --- | --- |
| `getCwd` | 启动时 `os.path.realpath(os.getcwd())` 存为 `WORKSPACE` |
| `listDir` | `os.scandir()` 一次不递归；目录在名前加 `/`；最多 200 项；目录优先排序 |
| `getFileType` | 扩展名 + `mimetypes.guess_type()` + 前 4KB 内容嗅探 |
| `readFile` | 先嗅探再解码（UTF-8 → GB18030），上限 256KB |
| `createFile` | `open(path, "x")` 独占创建；文件名禁止含路径分隔符 |
| `editFile` | `overwrite` / `append`；写入前先确认是纯文本 |
| `createDir` | `os.makedirs()` 自动建中间目录；已存在即报错 |
| `deleteDir` | 先 `count_entries()` 统计；非空必须 `recursive=true` 才 `shutil.rmtree()` |
| `deleteFile` | `os.remove()`；是目录则拒绝并提示改用 `deleteDir` |
| `renamePath` | `os.rename()`；只改同目录下的名字，目标已存在则拒绝 |

几个刻意加的防护：

1. **路径沙箱**：`resolve_path()` 先 `expanduser` 展开 `~`，再用 `realpath` 解析，
   结果必须落在 `WORKSPACE` 内；`../../etc/passwd` 和指向外部的符号链接都会被拒绝。
   注意 `~` 会被展开成 home 目录，若 home 不在工作目录内，同样会被沙箱拒绝。
2. **纯文本判定**：含 `\x00` 即判为二进制；再依次尝试 UTF-8 / GB18030 解码，
   两次都失败则视为二进制，`readFile` / `editFile` 直接拒绝，避免把二进制写坏；
3. **大文件保护**：只读前 256KB，截断时退回到最后一个换行符，避免把多字节字符切成两半；
4. **不覆盖**：`createFile` 已存在即报错（提示改用 `editFile`），`editFile` 不存在的文件也报错（提示改用 `createFile`）；
5. **删除需显式确认**：`deleteDir` 遇到非空目录不会直接删，而是返回“共 N 项，若确认请传 recursive=true”；
   且拒绝删除工作目录本身；`deleteFile` 与 `deleteDir` 各管各的，传错类型会得到明确提示；
6. **重命名不移动、不覆盖**：`renamePath` 只接受名字本身（拒绝 `/`、`\`、`.`、`..`），
   因此只能原地改名，不能跨目录移动；目标名已存在时报错，**大小写不敏感**的文件系统上仅改大小写会特例放行；
7. **统一换行**：`createFile` / `editFile` 均以 `newline="\n"` 写入，避免跨平台产生 `\r\n` 混杂。

工具内部抛出的 `ValueError` / `OSError` 会由 `run_tool()` 统一转成文本交回模型，
模型能看到“为什么失败”并自行调整参数，而不是脚本直接崩掉。

### 3. 流式响应里拼接工具调用

流式模式下工具调用也是分块下发的：

- 第一块带 `id` 和 `function.name`；
- 后续块只带 `function.arguments` 的片段，必须按 `index` 累积后拼成完整 JSON。

### 4. 工具调用循环

```text
用户提问
  └─> 请求模型 ──> 有 tool_calls？
                      ├─ 是：执行工具 → 追加 {"role": "tool", ...} → 再次请求模型
                      └─ 否：打印回答，本轮结束
```

只有最终问答会写进 `history`，工具调用的中间消息只存在于本轮，历史不会被工具细节污染。

> ⚠️ 当前实现里的循环是 `while True`，**没有最大迭代次数限制**。
> 正常情况下模型拿到工具结果就会给出最终回答，但如果模型反复调用工具（例如参数总是不对、
> 反复重试同一工具），这一轮就会一直循环下去，只能靠 Ctrl+C 中断。
> 生产环境建议加一个上限，例如：

```python
MAX_TOOL_ROUNDS = 8
for _ in range(MAX_TOOL_ROUNDS):
    ...
else:
    print("工具调用轮数超过上限，已中止本轮。")
```

## 与 Practice01 的差异

| 项目 | Practice01 | Practice02 |
| --- | --- | --- |
| 请求参数 | 只有 `model/messages/stream` | 增加 `tools` |
| 响应处理 | 只取 `delta.content` | 同时累积 `delta.tool_calls` |
| 消息角色 | user / assistant | 增加 assistant(tool_calls) / tool |
| 本地执行 | 无 | `subprocess.run(["date"])`、闰年计算、文件/目录的增删改查 |
| 输入结束 | Ctrl+C 退出 | Ctrl+C 或 EOF 均正常退出 |
| 工具循环上限 | — | 无上限（见上方风险说明，建议自行加） |

## 常见问题

- **模型一直不调用工具** → 检查 `description` 是否说清了用途，或直接问「用 getDate 查一下现在的时间」。
- **模型反复调用工具、停不下来** → 本轮循环没有迭代上限，先用 Ctrl+C 中断；再收紧 `description` 或降低 temperature，必要时改代码加最大轮数。
- **闰年结论和模型口算不一致** → 以工具结果为准，模型偶尔会自行口算（如 1900 年），可在提示里要求「必须调用 isLeapYear」。
- **`date` 命令不存在（Windows）** → 把命令换成 `["powershell", "-NoProfile", "-Command", "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"]`。
- **启动就报 `KeyError: 'llm'`** → 缺少 `config.ini` 或缺少 `[llm]` 段；照 `config.ini.example` 补全。
- **`deleteDir` 报“目录非空”** → 这是有意设计：确认要连内容一起删，再传 `recursive=true`；删除不可恢复，建议先 `listDir` 看一眼。
- **文件工具报“路径超出工作目录范围”** → 工具只能操作启动脚本时所在目录及其子目录；要操作别的目录，先 `cd` 过去再运行脚本。
- **`readFile` 报“不是纯文本文件”** → 该文件含二进制内容（如 `.png`、`.xlsx`），换用 `getFileType` 查看类型；本套工具只处理纯文本。
- **`renamePath` 报“目标已存在”或“不能包含路径分隔符”** → 本工具只做原地改名：`new_name` 只能是名字，想换目录请先建目录再移文件；同名文件不会覆盖。
- **HTTP 400 提示 tools 不支持** → 换用支持 function calling 的模型（如 `deepseek-chat`）。
- **401 / 超时 / 中文乱码** → 同 Practice01，检查 `config.ini` 与编码。

## 安全提示

`config.ini` 含真实 API Key，请勿提交到公开仓库。工具会执行本机命令、读写和删除本地文件，实际项目中应对工具名与参数做白名单校验。
这里的文件操作已限制在启动目录内、只支持纯文本，删除还需 `recursive=true` 确认，但删除不可恢复——建议先在测试目录验证再用于重要文件。
路径中的 `~` 会被 `os.path.expanduser()` 展开，但展开后仍要经过沙箱校验，不会因此访问到家目录之外。
另外，工具参数完全由模型生成，模型可能调用你没预料到的工具——重要操作前请人工确认。

## 相对 README.md 的修订记录

1. **修正快速开始中的工具数量**：「`chat-stream-tool-fileOps.py  # 8 个工具`」→「12 个工具，含 10 个文件工具」（代码中 `TOOL_HANDLERS` 共 12 项）。
2. **补全目录树**：增加 `README.md`、`README2.md`；并说明缺少 `config.ini` 时的 `KeyError` 行为。
3. **修正 `getDate` 示例输出**：原示例写死中文格式 `2026年10月19日 星期一 09:39:50 CST`，而 `date` 实际输出格式随系统 locale 变化，改为英文环境的真实形式并加注说明。
4. **补全 `isLeapYear` 实现说明**：补充 `parse_year()` 对 `bool` 的排除与 1–9999 年范围限制，以及四条理由分支的准确措辞。
5. **`getFileType` 描述**：从「`mimetypes` + 内容嗅探」明确为「扩展名 + `mimetypes.guess_type()` + 前 4KB 内容嗅探」。
6. **`listDir` 补充排序规则**（目录优先、忽略大小写）。
7. **新增工具循环无上限的风险说明**与加最大轮数的示例代码，并同步到「常见问题」和「与 Practice01 的差异」表。
8. **安全提示补充** `~` 被 `expanduser` 展开后仍受沙箱校验，以及模型可能调用未预期工具的提醒。
9. **新增「启动就报 `KeyError: 'llm'`」** 与「模型反复调用工具」两条常见问题。
10. **新增 `renamePath` 工具（第 12 个）**：更新工具总表、目录树、快速开始、`2.2` 实现表与防护说明、常见问题；它的设计边界（原地改名、不覆盖、不改工作目录）已在文中说明。

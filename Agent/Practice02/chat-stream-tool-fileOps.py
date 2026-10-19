# -*- coding: utf-8 -*-
import configparser
import io
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import warnings
from typing import Any

# 屏蔽 requests 对 chardet/urllib3 的版本误报（环境里 reportlab 装的 chardet 版本超范围，不影响功能）
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*")

import requests

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 读取与本文件同目录下的 config.ini
cfg = configparser.ConfigParser()
cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")
base_url = cfg["llm"]["base_url"].strip().rstrip("/")
api_key = cfg["llm"]["api_key"].strip()
model = cfg["llm"]["model"].strip()

# 文件工具的沙箱根目录：启动脚本时所在的目录。所有文件操作都被限制在它下面，
# 防止模型读写项目之外的系统文件（路径会先 realpath 解析，符号链接也绕不出去）。
WORKSPACE = os.path.realpath(os.getcwd())

MAX_READ_BYTES = 256 * 1024  # 单次读入上限，避免把巨大的日志塞进上下文
MAX_LIST_ENTRIES = 200  # 单次列目录上限

# 告诉模型「你有哪些工具可用」。模型只会返回“想调用谁、参数是什么”，
# 真正的执行由下面 run_tool() 在本地完成。
tools = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间（通过执行本地 date 命令获得）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "getCwd",
            "description": "获取当前工作目录（也是所有文件工具允许访问的根目录）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDir",
            "description": "列出指定目录下的所有文件和子目录（含隐藏项、类型和大小），不会递归",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对工作目录的路径，默认为当前目录 \".\"；如 \"docs\" 或 \"./src\"",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的类型：扩展名、MIME 类型、内容属于纯文本还是二进制，以及大小",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径，例如 \"note.txt\""}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "读取指定纯文本文件的完整内容（UTF-8/GB18030）。二进制文件或超过 256KB 的文件会被拒绝或截断",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "纯文本文件路径，例如 \"note.txt\""}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "在指定目录下按文件名新建一个纯文本文件；文件已存在时报错，不会覆盖",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "目标目录，必须已存在，如 \".\" 或 \"notes\""},
                    "filename": {"type": "string", "description": "新文件名（不含路径分隔符），如 \"todo.txt\""},
                    "content": {"type": "string", "description": "初始内容，可为空", "default": ""},
                },
                "required": ["directory", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFile",
            "description": "编辑已存在的纯文本文件：overwrite 整体覆盖，append 追加到末尾。不会创建新文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要编辑的纯文本文件路径，如 \"note.txt\""},
                    "content": {"type": "string", "description": "写入的完整内容（overwrite）或追加内容（append）"},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "写入方式，默认 overwrite（整体替换）",
                        "default": "overwrite",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDir",
            "description": "创建新目录；路径可以是多级（如 a/b/c），中间目录会自动创建。已存在时报错",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径，相对工作目录，如 notes/2026"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDir",
            "description": "删除目录，支持非空目录（需 recursive=true 才会连内容一起删）；不可恢复，不能删工作目录本身",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的目录路径，相对工作目录"},
                    "recursive": {
                        "type": "boolean",
                        "description": "目录非空时必须设为 true，表示连同内部所有文件和子目录一起删除",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除指定的单个文件（不可恢复）；目录请用 deleteDir",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "要删除的文件路径，相对工作目录"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renamePath",
            "description": "重命名文件或目录（原地改名，不移动位置，也不支持移动跨目录）；目标名已存在时报错，不会覆盖",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要重命名的文件或目录路径，如 old.txt 或 docs/old"},
                    "new_name": {
                        "type": "string",
                        "description": "新名称，只能是名字本身，不能包含路径分隔符（不会改变所在目录）",
                    },
                },
                "required": ["path", "new_name"],
            },
        },
    },
]

TOOL_HANDLERS: dict[str, Any] = {}


def get_date():
    """执行本地 date 命令，返回它的标准输出。"""
    try:
        result = subprocess.run(["date"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return "执行 date 命令失败：%s" % e
    if result.returncode != 0:
        return "执行 date 命令失败（退出码 %s）：%s" % (result.returncode, result.stderr.strip())
    return result.stdout.strip()


def is_leap_year(year):
    """闰年规则：能被 4 整除，且（不能被 100 整除 或 能被 400 整除）。"""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def describe_leap_year(year):
    """返回给模型的可读结论，附带命中的分支，方便模型解释。"""
    if year % 4 != 0:
        reason = "不能被 4 整除"
    elif year % 100 != 0:
        reason = "能被 4 整除且不能被 100 整除"
    elif year % 400 == 0:
        reason = "是整百年且能被 400 整除"
    else:
        reason = "是整百年但不能被 400 整除"
    verdict = "是闰年" if is_leap_year(year) else "不是闰年"
    return "%d 年%s（%s）。" % (year, verdict, reason)


def parse_year(args):
    """从参数里取出 year，兼容模型传字符串数字的情况。"""
    year = args.get("year")
    if isinstance(year, bool):  # bool 是 int 的子类，先排除
        return None, "参数 year 必须是整数，收到 %r" % (year,)
    if not isinstance(year, int):
        try:
            year = int(str(year).strip())
        except (TypeError, ValueError):
            return None, "参数 year 无效：%r" % (year,)
    if not 1 <= year <= 9999:
        return None, "参数 year 超出范围（公历 1-9999 年）：%d" % year
    return year, None


def resolve_path(path):
    """把相对路径解析到 WORKSPACE 下，并拒绝逃出沙箱的路径。"""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("路径不能为空")
    target = os.path.realpath(os.path.join(WORKSPACE, os.path.expanduser(path.strip())))
    if target != WORKSPACE and not target.startswith(WORKSPACE + os.sep):
        raise ValueError("路径超出工作目录范围：%s" % path)
    return target


def relative(target):
    """显示用路径：尽量用相对工作目录的形式，更短更好读。"""
    return os.path.relpath(target, WORKSPACE)


def format_size(size):
    if size < 1024:
        return "%d B" % size
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit)
    return "%d B" % size


def sniff_text(data: bytes) -> tuple[bool, str]:
    """判断字节串是不是纯文本，返回 (是否文本, 编码)；二进制时编码为空串。"""
    if b"\x00" in data:
        return False, ""
    for encoding in ("utf-8", "gb18030"):
        try:
            data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return True, encoding
    return False, ""


def read_text_file(target):
    """读取纯文本文件，返回 (文本, 编码, 是否截断, 总字节数)。非文本抛 ValueError。"""
    if not os.path.exists(target):
        raise ValueError("文件不存在：%s" % relative(target))
    if not os.path.isfile(target):
        raise ValueError("不是普通文件：%s" % relative(target))

    total = os.path.getsize(target)
    try:
        with open(target, "rb") as f:
            data = f.read(MAX_READ_BYTES + 1)
    except OSError as e:
        raise ValueError("无法读取文件 %s：%s" % (relative(target), e)) from e
    truncated = len(data) > MAX_READ_BYTES
    if truncated:
        data = data[:MAX_READ_BYTES]
        # 截断点落在多字节字符中间会让解码误判，退回到最后一个换行符
        cut = data.rfind(b"\n")
        if cut > 0:
            data = data[: cut + 1]

    is_text, encoding = sniff_text(data)
    if not is_text:
        raise ValueError("不是纯文本文件（检测到二进制内容）：%s" % relative(target))
    return data.decode(encoding), encoding, truncated, total


def tool_get_date(args):
    return get_date()


def tool_is_leap_year(args):
    year, error = parse_year(args)
    return error if error else describe_leap_year(year)


def tool_get_cwd(args):
    return WORKSPACE


def tool_list_dir(args):
    target = resolve_path(args.get("path") or ".")
    if not os.path.exists(target):
        raise ValueError("目录不存在：%s" % relative(target))
    if not os.path.isdir(target):
        raise ValueError("不是目录：%s" % relative(target))

    entries = sorted(
        os.scandir(target),
        key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
    )
    lines = []
    for entry in entries[:MAX_LIST_ENTRIES]:
        if entry.is_symlink():
            kind = "链接"
        elif entry.is_dir(follow_symlinks=False):
            kind = "目录"
        else:
            kind = "文件"
        name = entry.name + ("/" if kind == "目录" else "")
        if kind == "文件":
            try:
                line = "%s  [%s, %s]" % (name, kind, format_size(entry.stat().st_size))
            except OSError:
                line = "%s  [%s, 大小未知]" % (name, kind)
        else:
            line = "%s  [%s]" % (name, kind)
        lines.append(line)

    hidden = sum(1 for e in entries if e.name.startswith("."))
    summary = "目录 %s：共 %d 项（含 %d 个隐藏项）" % (relative(target), len(entries), hidden)
    if len(entries) > MAX_LIST_ENTRIES:
        summary += "，仅显示前 %d 项" % MAX_LIST_ENTRIES
    return summary + "\n" + ("\n".join(lines) if lines else "（空目录）")


def tool_get_file_type(args):
    target = resolve_path(args.get("path"))
    if not os.path.exists(target):
        raise ValueError("文件不存在：%s" % relative(target))
    if not os.path.isfile(target):
        raise ValueError("不是普通文件：%s" % relative(target))

    name = os.path.basename(target)
    ext = os.path.splitext(name)[1].lower() or "（无扩展名）"
    mime = mimetypes.guess_type(name)[0] or "未知"

    try:
        with open(target, "rb") as f:
            head = f.read(4096)
    except OSError as e:
        raise ValueError("无法读取文件 %s：%s" % (relative(target), e)) from e
    is_text, encoding = sniff_text(head)
    kind = "纯文本（可用 readFile 读取）" if is_text else "二进制（不支持 readFile）"
    if is_text:
        kind += "，编码 %s" % encoding.upper()

    return "文件：%s\n扩展名：%s\nMIME：%s\n内容类型：%s\n大小：%s" % (
        relative(target),
        ext,
        mime,
        kind,
        format_size(os.path.getsize(target)),
    )


def tool_read_file(args):
    target = resolve_path(args.get("path"))
    text, encoding, truncated, total = read_text_file(target)
    header = "文件：%s（编码 %s，共 %s）" % (relative(target), encoding.upper(), format_size(total))
    if truncated:
        header += "，内容较长，仅显示前 %d 字节" % MAX_READ_BYTES
    return header + "\n---\n" + text


def tool_create_file(args):
    directory = resolve_path(args.get("directory") or ".")
    filename = args.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("filename 必须是非空字符串")
    filename = filename.strip()
    if filename in (".", "..") or os.sep in filename or (os.altsep and os.altsep in filename):
        raise ValueError("filename 只能是文件名，不能包含路径分隔符：%s" % filename)

    content = args.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串")

    if not os.path.isdir(directory):
        raise ValueError("目标目录不存在：%s" % relative(directory))
    target = os.path.join(directory, filename)
    if os.path.exists(target):
        raise ValueError("文件已存在，拒绝覆盖：%s（如需修改请用 editFile）" % relative(target))

    # "x" 模式 = 独占创建，天然避免覆盖已有文件
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        raise ValueError("创建文件失败 %s：%s" % (relative(target), e)) from e
    return "已创建文件：%s（写入 %d 字符，%s）" % (
        relative(target),
        len(content),
        format_size(os.path.getsize(target)),
    )


def tool_edit_file(args):
    target = resolve_path(args.get("path"))
    content = args.get("content")
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串")
    mode = args.get("mode") or "overwrite"
    if mode not in ("overwrite", "append"):
        raise ValueError("mode 只能是 overwrite 或 append，收到 %r" % (mode,))

    if not os.path.exists(target):
        raise ValueError("文件不存在：%s（如需新建请用 createFile）" % relative(target))
    # 覆盖前先确认它是纯文本，避免把二进制文件写坏
    read_text_file(target)

    # 统一用 LF 换行，避免跨平台产生 \r\n 混杂
    try:
        with open(target, "a" if mode == "append" else "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        raise ValueError("写入文件失败 %s：%s" % (relative(target), e)) from e
    action = "追加写入" if mode == "append" else "覆盖写入"
    return "已%s：%s（本次 %d 字符，当前共 %s）" % (
        action,
        relative(target),
        len(content),
        format_size(os.path.getsize(target)),
    )


def count_entries(root):
    """统计目录下所有文件和子目录的数量（用于删除前告知影响范围）。"""
    total = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        total += len(dirnames) + len(filenames)
    return total


def tool_create_dir(args):
    target = resolve_path(args.get("path"))
    if os.path.exists(target):
        raise ValueError("已存在，不会覆盖：%s" % relative(target))
    try:
        os.makedirs(target)  # 默认会创建中间目录
    except OSError as e:
        raise ValueError("创建目录失败 %s：%s" % (relative(target), e)) from e
    return "已创建目录：%s" % relative(target)


def tool_delete_dir(args):
    target = resolve_path(args.get("path"))
    if target == WORKSPACE:
        raise ValueError("不能删除工作目录本身：%s（可删它的子目录）" % WORKSPACE)
    if not os.path.lexists(target):
        raise ValueError("目录不存在：%s" % relative(target))
    if not os.path.isdir(target) or os.path.islink(target):
        raise ValueError("不是普通目录：%s（删除文件请用 deleteFile）" % relative(target))

    recursive = args.get("recursive", False)
    if not isinstance(recursive, bool):
        raise ValueError("recursive 必须是布尔值，收到 %r" % (recursive,))

    total = count_entries(target)
    # 非空目录必须先显式确认，避免模型/用户误删一大片
    if total and not recursive:
        raise ValueError(
            "目录非空（%d 项），确认要连同内容一起删除请传 recursive=true：%s" % (total, relative(target))
        )

    try:
        if recursive:
            shutil.rmtree(target)
        else:
            os.rmdir(target)
    except OSError as e:
        raise ValueError("删除目录失败 %s：%s" % (relative(target), e)) from e
    return "已删除目录：%s（连同 %d 项内容）" % (relative(target), total)


def tool_delete_file(args):
    target = resolve_path(args.get("path"))
    if not os.path.lexists(target):
        raise ValueError("文件不存在：%s" % relative(target))
    if os.path.isdir(target):
        raise ValueError("是目录，不是文件：%s（删除目录请用 deleteDir）" % relative(target))

    size = os.path.getsize(target) if not os.path.islink(target) else 0
    try:
        os.remove(target)
    except OSError as e:
        raise ValueError("删除文件失败 %s：%s" % (relative(target), e)) from e
    return "已删除文件：%s（%s）" % (relative(target), format_size(size))


def tool_rename_path(args):
    source = resolve_path(args.get("path"))
    if source == WORKSPACE:
        raise ValueError("不能重命名工作目录本身：%s" % WORKSPACE)
    if not os.path.lexists(source):
        raise ValueError("路径不存在：%s" % relative(source))

    new_name = args.get("new_name")
    if not isinstance(new_name, str) or not new_name.strip():
        raise ValueError("new_name 必须是非空字符串")
    new_name = new_name.strip()
    if new_name in (".", "..") or os.sep in new_name or (os.altsep and os.altsep in new_name):
        raise ValueError("new_name 只能是名称本身，不能包含路径分隔符：%s" % new_name)
    if new_name == os.path.basename(source):
        raise ValueError("新旧名称相同：%s" % new_name)

    # 只改同目录下的名字，目标路径由“原父目录 + 新名称”拼出，天然不会跑出沙箱
    target = os.path.join(os.path.dirname(source), new_name)
    if os.path.lexists(target):
        # 大小写不敏感的文件系统上，仅改大小写会命中同一个文件，这种情况允许
        try:
            same_file = os.path.samefile(source, target)
        except OSError:
            same_file = False
        if not same_file:
            raise ValueError("目标已存在，拒绝覆盖：%s" % relative(target))

    if os.path.islink(source):
        kind = "符号链接"
    elif os.path.isdir(source):
        kind = "目录"
    else:
        kind = "文件"

    try:
        os.rename(source, target)
    except OSError as e:
        raise ValueError("重命名失败 %s：%s" % (relative(source), e)) from e
    return "已重命名%s：%s → %s" % (kind, relative(source), relative(target))


TOOL_HANDLERS = {
    "getDate": tool_get_date,
    "isLeapYear": tool_is_leap_year,
    "getCwd": tool_get_cwd,
    "listDir": tool_list_dir,
    "getFileType": tool_get_file_type,
    "readFile": tool_read_file,
    "createFile": tool_create_file,
    "editFile": tool_edit_file,
    "createDir": tool_create_dir,
    "deleteDir": tool_delete_dir,
    "deleteFile": tool_delete_file,
    "renamePath": tool_rename_path,
}


def run_tool(name, arguments):
    """工具分发：解析参数 JSON，调用对应函数，把异常也变成可读文本交回模型。"""
    try:
        args = json.loads(arguments or "{}")
    except ValueError as e:
        return "参数不是合法 JSON：%s（%s）" % (arguments, e)
    if not isinstance(args, dict):
        return "参数必须是 JSON 对象：%s" % arguments

    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return "未知工具：%s。可用工具：%s" % (name, "、".join(sorted(TOOL_HANDLERS)))
    try:
        return handler(args)
    except (OSError, ValueError) as e:
        return "工具执行失败：%s" % e


def chat_stream(messages):
    """发一次流式请求，边打印正文边收集工具调用，返回 (正文, 工具调用列表)。"""
    payload: Any = {"model": model, "messages": messages, "tools": tools, "stream": True}
    resp = requests.post(
        base_url + "/chat/completions",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        json=payload,
        stream=True,
        timeout=60,
    )
    if resp.status_code != 200:
        print("HTTP %s: %s" % (resp.status_code, resp.text[:300]))
        sys.exit(1)

    # SSE 响应头不带 charset，requests 会按 ISO-8859-1 解码，这里强制用 UTF-8，否则中文乱码
    resp.encoding = "utf-8"

    reply = []
    calls = {}  # index -> {"id": ..., "name": ..., "arguments": ...}
    for raw in resp.iter_lines(decode_unicode=True):
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"] or {}
        except (ValueError, KeyError, IndexError):
            continue

        if delta.get("content"):
            print(delta["content"], end="", flush=True)
            reply.append(delta["content"])

        # 工具调用同样是流式下发的：第一块带 id 和 name，后续块只带 arguments 的片段
        for tc in delta.get("tool_calls") or []:
            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]

    return "".join(reply), [calls[i] for i in sorted(calls)]


history = []  # 存已完成的问答，每一轮都把全部历史一起发给模型

try:
    while True:
        try:
            prompt = input("please input your prompt: ")
        except EOFError:  # 输入流结束（如管道喂入）时正常退出
            break
        print("---")

        try:
            # 本轮的工作消息：历史 + 本轮提问。工具调用产生的中间消息只加在这里，
            # 任务结束后再把最终问答写进 history，避免历史里堆积工具细节。
            messages = history + [{"role": "user", "content": prompt}]

            while True:
                content, calls = chat_stream(messages)
                print()

                if not calls:
                    history.append({"role": "user", "content": prompt})
                    history.append({"role": "assistant", "content": content})
                    break

                # 先把模型「要调用工具」这一步写回消息列表
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": [
                            {
                                "id": c["id"],
                                "type": "function",
                                "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
                            }
                            for c in calls
                        ],
                    }
                )

                for call in calls:
                    args = call["arguments"] or "{}"
                    print("[调用工具] %s(%s)" % (call["name"], args))
                    result = run_tool(call["name"], args)
                    print("[工具结果] %s" % result)
                    # 用 tool 角色把执行结果交回模型，模型据此继续生成
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

                print("---")
        except requests.RequestException as e:
            print("请求失败：%s" % e)
            sys.exit(1)
except KeyboardInterrupt:
    print()

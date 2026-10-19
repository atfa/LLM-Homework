# -*- coding: utf-8 -*-
import configparser
import io
import json
import os
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
]


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


def parse_year(arguments):
    """从工具参数里取出 year，兼容模型传字符串数字的情况。"""
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        return None, "参数不是合法 JSON：%s" % arguments
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


def run_tool(name, arguments):
    """工具分发：把模型请求的工具名映射到本地函数。"""
    if name == "getDate":
        return get_date()
    if name == "isLeapYear":
        year, error = parse_year(arguments)
        return error if error else describe_leap_year(year)
    return "未知工具：%s" % name


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

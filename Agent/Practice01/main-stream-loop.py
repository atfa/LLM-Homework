import configparser
from pathlib import Path

from openai import OpenAI

# 读取同目录下的 config.ini
config = configparser.ConfigParser()
config.read(Path(__file__).parent / "config.ini", encoding="utf-8")
cfg = config["llm"]

client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])

messages = []
print("开始聊天，按 Ctrl+C 退出。")
try:
    while True:
        prompt = input("\n你: ").strip()
        if not prompt:
            continue
        messages.append({"role": "user", "content": prompt})

        stream = client.chat.completions.create(
            model=cfg["model"],
            messages=messages,
            stream=True,
        )
        print("AI: ", end="", flush=True)
        reply = ""
        for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                print(content, end="", flush=True)
                reply += content
        print()
        messages.append({"role": "assistant", "content": reply})
except KeyboardInterrupt:
    print("\n已退出。")

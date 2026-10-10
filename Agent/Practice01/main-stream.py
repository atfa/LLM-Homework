import configparser
from pathlib import Path

from openai import OpenAI

# 读取同目录下的 config.ini
config = configparser.ConfigParser()
config.read(Path(__file__).parent / "config.ini", encoding="utf-8")
cfg = config["llm"]

client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])

prompt = input("请输入你的问题: ").strip()
if not prompt:
    raise SystemExit("问题不能为空")

stream = client.chat.completions.create(
    model=cfg["model"],
    messages=[{"role": "user", "content": prompt}],
    stream=True,
)
for chunk in stream:
    content = chunk.choices[0].delta.content
    if content:
        print(content, end="", flush=True)
print()

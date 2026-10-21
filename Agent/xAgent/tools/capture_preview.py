#!/usr/bin/env python3
"""生成 docs/tui-preview.png。

做法：在伪终端里运行 tools/demo_app.py 自动播放一段对话，捕获 ANSI 输出，
用 pyte 还原屏幕，再用 Sarasa Mono SC（CJK 等宽 2:1）渲染成 PNG。
不需要 API Key，也不会访问网络。

依赖（仅开发用）：pyte、Pillow
用法：python tools/capture_preview.py
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pyte
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
COLS, ROWS = 124, 42
FONT_PATH = Path.home() / "Library/Fonts/SarasaMonoSC-Regular.ttf"
FALLBACK_FONT = Path("/System/Library/Fonts/Menlo.ttc")

# 与 Textual dark 主题一致的近似配色
DEFAULT_FG = (224, 224, 224)
DEFAULT_BG = (30, 30, 36)
NAMED_COLORS = {
    "black": (0, 0, 0),
    "red": (205, 49, 49),
    "green": (13, 188, 121),
    "brown": (229, 229, 16),
    "yellow": (229, 229, 16),
    "blue": (36, 114, 200),
    "magenta": (188, 63, 188),
    "cyan": (17, 168, 205),
    "white": (229, 229, 229),
    "default": None,
}


def capture(cols: int, rows: int, timeout: float = 30.0) -> bytes:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    env = dict(
        os.environ,
        TERM="xterm-256color",
        COLUMNS=str(cols),
        LINES=str(rows),
        PYTHONPATH=str(ROOT),
        PYTHONIOENCODING="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "tools.demo_app"],
        cwd=ROOT,
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)

    chunks: list[bytes] = []
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            if process.poll() is not None:
                # 读干净剩余输出
                while True:
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if not readable:
                        break
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    chunks.append(data)
                break
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)
    return b"".join(chunks)


def parse_color(value: str) -> tuple[int, int, int] | None:
    if value in ("default", ""):
        return None
    if value in NAMED_COLORS:
        return NAMED_COLORS[value]
    if value.startswith("#") and len(value) == 7:
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    if len(value) == 6:
        try:
            return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            return None
    return None


def render_screen(screen: pyte.Screen, cols: int, rows: int, target: Path) -> None:
    font_path = FONT_PATH if FONT_PATH.exists() else FALLBACK_FONT
    font_size = 16
    font = ImageFont.truetype(str(font_path), font_size)
    cell_w = max(1, round(font.getlength("M")))
    cell_h = round(font_size * 1.45)
    padding = 10

    width = cols * cell_w + padding * 2
    height = rows * cell_h + padding * 2
    image = Image.new("RGB", (width, height), DEFAULT_BG)
    draw = ImageDraw.Draw(image)

    for y in range(rows):
        line = screen.buffer[y]
        x = 0
        while x < cols:
            cell = line[x]
            if cell.data == "":
                x += 1
                continue
            # 背景色连续段
            bg = parse_color(cell.bg)
            if bg and bg != DEFAULT_BG:
                run_end = x + 1
                while run_end < cols and parse_color(line[run_end].bg) == bg and line[run_end].data != "":
                    run_end += 1
                draw.rectangle(
                    [
                        padding + x * cell_w,
                        padding + y * cell_h,
                        padding + run_end * cell_w - 1,
                        padding + (y + 1) * cell_h - 1,
                    ],
                    fill=bg,
                )
                x_end = run_end
            else:
                x_end = x + 1

            for cx in range(x, x_end):
                current = line[cx]
                if not current.data or current.data == " ":
                    continue
                fg = parse_color(current.fg) or DEFAULT_FG
                if current.reverse:
                    fg = parse_color(current.bg) or DEFAULT_BG
                draw.text(
                    (padding + cx * cell_w, padding + y * cell_h),
                    current.data,
                    font=font,
                    fill=fg,
                )
            x = x_end

    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    print(f"已生成 {target}（{width}x{height}）")


def main() -> None:
    raw = capture(COLS, ROWS)
    if not raw:
        raise SystemExit("未捕获到 TUI 输出，预览生成失败")
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.ByteStream(screen)
    stream.feed(raw)
    render_screen(screen, COLS, ROWS, ROOT / "docs" / "tui-preview.png")


if __name__ == "__main__":
    main()

"""Textual 输入兼容补丁。

Textual 8.x 的终端解析器在拼装 CSI-u（Kitty 键盘协议）序列时，只允许
序列增长到 32 个字符。中文输入法快速上屏时，一个事件可能同时携带多个汉字，
序列长度很容易超过 32；一旦超限，Textual 会把整段序列当成普通按键插入，
输入框里就会出现 ``^[[32;;25104:...u`` 这样的乱码。

这里放宽缓冲区上限，让解析器有足够空间读完整个 IME 事件。补丁只上调、
不会降低未来的默认值，因此升级 Textual 后保持无害。
"""

from __future__ import annotations

import textual._xterm_parser as _xterm_parser

_SEQUENCE_BUFFER_CHARS = 512

_threshold = getattr(_xterm_parser, "_MAX_SEQUENCE_SEARCH_THRESHOLD", None)
if isinstance(_threshold, int) and _threshold < _SEQUENCE_BUFFER_CHARS:
    _xterm_parser._MAX_SEQUENCE_SEARCH_THRESHOLD = _SEQUENCE_BUFFER_CHARS


__all__ = ["_SEQUENCE_BUFFER_CHARS"]

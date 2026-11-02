"""最近会话选择面板。"""

from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option


class SessionScreen(ModalScreen[str | None]):
    """按最近修改时间选择会话；取消时不改变当前会话。"""

    BINDINGS = [Binding("escape", "cancel", "取消", priority=True)]
    DEFAULT_CSS = """
    SessionScreen { align: center middle; }
    SessionScreen > Vertical {
        width: 72; max-width: 90%; height: auto; max-height: 80%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    SessionScreen OptionList { height: auto; max-height: 20; }
    """

    def __init__(self, paths: list[Path], current_id: str) -> None:
        super().__init__()
        self.paths = paths
        self.current_id = current_id

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("最近会话 · ↑↓ 选择 · Enter 确认 · Esc 取消")
            if self.paths:
                yield OptionList(
                    *[
                        Option(
                            f"{path.stem}  {datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M:%S}"
                            + ("  （当前会话）" if path.stem == self.current_id else ""),
                            id=path.stem,
                        )
                        for path in self.paths
                    ]
                )
            else:
                yield Label("没有已保存会话。")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

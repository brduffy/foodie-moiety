"""Floating overlay showing available voice commands for the current view."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


_RECIPE_DETAIL_COMMANDS = [
    ("next / previous", "Navigate steps"),
    ("step [N] / intro", "Jump to step"),
    ("more / less [pane]", "Scroll content"),
    ("ingredients / directions", "Switch view"),
    ("image / tags", "Switch view"),
    ("double / halve", "Scale recipe"),
    ("scale by [N]", "Scale recipe"),
    ("convert [qty] [unit] to [unit]", "Unit conversion"),
    ("bigger / smaller font", "Adjust text size"),
    ("play video", "Open video player"),
]

_VIDEO_PLAYER_COMMANDS = [
    ("pause / play / stop", "Playback control"),
    ("skip forward / skip back", "Seek"),
    ("mute / unmute", "Volume"),
    ("next / previous", "Navigate steps"),
    ("step [N]", "Jump to step"),
]

_GLOBAL_COMMANDS = [
    ("commands", "Show this reference"),
    ("close / dismiss", "Hide overlay panel"),
    ("pause listening", "Mute microphone"),
    ("resume listening", "Unmute microphone"),
]

_VIEW_COMMANDS = {
    "recipe_detail": _RECIPE_DETAIL_COMMANDS,
    "video_player": _VIDEO_PLAYER_COMMANDS,
}

_VIEW_TITLES = {
    "recipe_detail": "Recipe Commands",
    "video_player": "Video Commands",
}


class CommandReference(QWidget):
    """Semi-transparent overlay listing available voice commands."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CommandReference")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.setStyleSheet("""
            QWidget#CommandReference {
                background-color: rgba(20, 20, 20, 220);
                border: 1px solid #555555;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(0)

        self._title = QLabel()
        self._title.setStyleSheet(
            "color: #cccccc; font-size: 14px; font-weight: 600; "
            "background: transparent; padding-bottom: 6px;"
        )
        layout.addWidget(self._title)

        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._body.setStyleSheet(
            "color: #bbbbbb; background: transparent; padding: 0px;"
        )
        font = QFont()
        font.setPixelSize(13)
        self._body.setFont(font)
        layout.addWidget(self._body)

        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(15000)
        self._auto_hide_timer.timeout.connect(self.hide)

        self._active_view = "recipe_detail"
        self.hide()

    def set_view(self, view_name: str) -> None:
        """Update displayed commands for the given view."""
        self._active_view = view_name
        if self.isVisible():
            self._rebuild()

    def toggle(self) -> None:
        """Toggle visibility."""
        if self.isVisible():
            self.hide()
        else:
            self._rebuild()
            self.show()
            self.raise_()
            self._auto_hide_timer.start()

    def mousePressEvent(self, event):
        """Dismiss on tap/click."""
        self.hide()

    def _rebuild(self) -> None:
        """Rebuild the command list HTML for the current view."""
        view_cmds = _VIEW_COMMANDS.get(self._active_view, [])
        title = _VIEW_TITLES.get(self._active_view, "Voice Commands")
        self._title.setText(title)

        rows = []
        for cmd, desc in view_cmds:
            rows.append(
                f'<tr>'
                f'<td style="padding: 2px 12px 2px 0; color: #ffffff; '
                f'font-weight: 500; white-space: nowrap;">{cmd}</td>'
                f'<td style="padding: 2px 0; color: #888888;">{desc}</td>'
                f'</tr>'
            )
        # Separator
        rows.append(
            '<tr><td colspan="2" style="padding: 6px 0 4px 0; '
            'border-bottom: 1px solid #444444;"></td></tr>'
        )
        for cmd, desc in _GLOBAL_COMMANDS:
            rows.append(
                f'<tr>'
                f'<td style="padding: 2px 12px 2px 0; color: #ffffff; '
                f'font-weight: 500; white-space: nowrap;">{cmd}</td>'
                f'<td style="padding: 2px 0; color: #888888;">{desc}</td>'
                f'</tr>'
            )

        html = f'<table style="border-collapse: collapse;">{"".join(rows)}</table>'
        self._body.setText(html)
        self.adjustSize()

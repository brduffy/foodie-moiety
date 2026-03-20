"""Full-window overlay panel for help, scaled ingredients, and conversions."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


_RECIPE_DETAIL_COMMANDS = [
    ("next / previous", "Navigate steps"),
    ("intro", "Go to intro step"),
    ("step [N]", "Jump to step N"),
    ("more / less", "Scroll active pane"),
    ("more / less ingredients", "Scroll ingredients"),
    ("more / less directions", "Scroll directions"),
    ("ingredients / directions / image / details", "Switch view"),
    ("ingredients and directions", "Show both panes"),
    ("scale by [N / half / quarter]", "Scale recipe"),
    ("bigger / smaller font", "Adjust text size"),
    ("max / min font", "Set text size limit"),
    ("play video", "Open video player"),
]

_VIDEO_PLAYER_COMMANDS = [
    ("pause", "Pause video"),
    ("play", "Resume video"),
    ("stop", "Stop video"),
    ("skip forward / skip back", "Seek 10 seconds"),
    ("mute / unmute", "Toggle video audio"),
    ("next / previous", "Navigate steps"),
    ("step [N]", "Jump to step N"),
]

_GLOBAL_COMMANDS = [
    ("commands", "Show this reference"),
    ("close / dismiss", "Hide overlay"),
    ("pause listening", "Mute microphone"),
    ("resume listening", "Unmute microphone"),
    ("disable voice responses", "Turn off TTS"),
    ("enable voice responses", "Turn on TTS"),
]

_VIEW_COMMANDS = {
    "recipe_detail": _RECIPE_DETAIL_COMMANDS,
    "video_player": _VIDEO_PLAYER_COMMANDS,
}

_VIEW_TITLES = {
    "recipe_detail": "Voice Commands",
    "video_player": "Video Commands",
}


class InfoPanel(QWidget):
    """Overlay that fills the full window behind the command bar.

    Displays one of three content types:
    - **help**: voice command reference for the active view
    - **scale**: scaled ingredient list
    - **conversion**: unit conversion result (single line)

    Dismissed via close button or "close"/"dismiss" voice command.
    """

    # Base font sizes (normal window) and scale factor for fullscreen/maximized
    _BASE_TITLE_SIZE = 20
    _BASE_BODY_SIZE = 20
    _BASE_CMD_SIZE = 20
    _BASE_DESC_SIZE = 18
    _BASE_CONV_SIZE = 24
    _BASE_CLOSE_SIZE = 22
    _LARGE_SCALE = 2.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._large_mode = False

        self._apply_stylesheet()

        # Computed sizes (updated by set_large_mode)
        self._sz_title = self._BASE_TITLE_SIZE
        self._sz_body = self._BASE_BODY_SIZE
        self._sz_cmd = self._BASE_CMD_SIZE
        self._sz_desc = self._BASE_DESC_SIZE
        self._sz_conv = self._BASE_CONV_SIZE

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        # Header row: title + close button
        header = QHBoxLayout()
        header.setSpacing(0)
        self._title = QLabel()
        self._title.setObjectName("InfoTitle")
        header.addWidget(self._title)
        header.addStretch()

        close_btn = QPushButton("\u2715")
        close_btn.setObjectName("InfoCloseBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Scrollable body
        self._body = QLabel()
        self._body.setObjectName("InfoBody")
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("InfoScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._body)
        layout.addWidget(self._scroll, stretch=1)

        self._active_view = "recipe_detail"
        self._content_type = None  # "help", "scale", "conversion"
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_large_mode(self, large: bool) -> None:
        """Switch between normal and large (fullscreen/maximized) font sizes.

        Re-applies the stylesheet and refreshes visible content so all
        font sizes update immediately.
        """
        if large == self._large_mode:
            return
        self._large_mode = large
        scale = self._LARGE_SCALE if large else 1.0
        self._sz_title = int(self._BASE_TITLE_SIZE * scale)
        self._sz_body = int(self._BASE_BODY_SIZE * scale)
        self._sz_cmd = int(self._BASE_CMD_SIZE * scale)
        self._sz_desc = int(self._BASE_DESC_SIZE * scale)
        self._sz_conv = int(self._BASE_CONV_SIZE * scale)
        self._apply_stylesheet()
        # Refresh visible content with new sizes
        if self.isVisible() and self._content_type:
            if self._content_type == "help":
                self._show_help_content()
            elif self._content_type == "scale":
                self._refresh_scale_content()

    def set_view(self, view_name: str) -> None:
        """Update the active view (affects help content)."""
        self._active_view = view_name
        if self.isVisible() and self._content_type == "help":
            self._show_help_content()

    def show_help(self) -> None:
        """Show the voice command reference for the current view."""
        self._content_type = "help"
        self._show_help_content()
        self.show()
        self.raise_()

    def show_scale(self, text: str) -> None:
        """Show scaled ingredient list."""
        self._content_type = "scale"
        self._scale_text = text  # Stash for refresh on font size change
        self._render_scale(text)
        self.show()
        self.raise_()

    def show_conversion(self, text: str) -> None:
        """Show a unit conversion result."""
        self._content_type = "conversion"
        self._title.setText("Unit Conversion")
        self._body.setText(
            f'<p style="font-size: {self._sz_conv}px; color: #ffffff; '
            f'padding-top: 16px;">{text}</p>'
        )
        self.show()
        self.raise_()

    def toggle_help(self) -> None:
        """Toggle the help display. If showing other content, switch to help."""
        if self.isVisible() and self._content_type == "help":
            self.hide()
        else:
            self.show_help()

    def scroll_by_page(self, direction: str) -> None:
        """Scroll the panel content by one viewport page.

        Args:
            direction: "down" (more) or "up" (less).
        """
        sb = self._scroll.verticalScrollBar()
        page = self._scroll.viewport().height()
        if direction == "down":
            sb.setValue(sb.value() + page)
        else:
            sb.setValue(sb.value() - page)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_stylesheet(self) -> None:
        """Apply the stylesheet with current font sizes."""
        scale = self._LARGE_SCALE if self._large_mode else 1.0
        title_sz = int(self._BASE_TITLE_SIZE * scale)
        body_sz = int(self._BASE_BODY_SIZE * scale)
        close_sz = int(self._BASE_CLOSE_SIZE * scale)
        self.setStyleSheet(f"""
            QWidget#InfoPanel {{
                background-color: rgba(20, 20, 20, 240);
            }}
            QPushButton#InfoCloseBtn {{
                background: transparent;
                color: #888888;
                border: none;
                font-size: {close_sz}px;
                font-weight: bold;
                padding: 0px;
                min-width: 36px; max-width: 36px;
                min-height: 36px; max-height: 36px;
            }}
            QPushButton#InfoCloseBtn:hover {{
                color: white;
            }}
            QLabel#InfoTitle {{
                color: #cccccc;
                font-size: {title_sz}px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#InfoBody {{
                color: #dddddd;
                background: transparent;
                font-size: {body_sz}px;
            }}
            QScrollArea#InfoScroll {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                width: 6px;
                background-color: transparent;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background-color: white;
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    _ZEBRA_BG = "rgba(255, 255, 255, 0.06)"

    def _show_help_content(self) -> None:
        """Build the help command table for the active view."""
        view_cmds = _VIEW_COMMANDS.get(self._active_view, [])
        title = _VIEW_TITLES.get(self._active_view, "Voice Commands")
        hint_sz = self._sz_desc
        self._title.setText(
            f'{title}'
            f'<span style="color: #ffffff; font-size: {hint_sz}px; font-weight: 400;">'
            f'  \u2014  say "more" or "less" to scroll</span>'
        )

        cmd_sz = self._sz_cmd
        desc_sz = self._sz_desc
        rows = []
        row_idx = 0
        for cmd, desc in view_cmds:
            bg = f' background-color: {self._ZEBRA_BG};' if row_idx % 2 else ''
            rows.append(
                f'<tr>'
                f'<td style="padding: 4px 16px 4px 8px; color: #ffffff; '
                f'font-weight: 500; font-size: {cmd_sz}px; white-space: nowrap;{bg}">{cmd}</td>'
                f'<td style="padding: 4px 8px 4px 0; color: #5EAAFF; '
                f'font-size: {desc_sz}px;{bg}">{desc}</td>'
                f'</tr>'
            )
            row_idx += 1
        # Separator
        rows.append(
            '<tr><td colspan="2" style="padding: 8px 0 6px 0; '
            'border-bottom: 1px solid #444444;"></td></tr>'
        )
        row_idx = 0
        for cmd, desc in _GLOBAL_COMMANDS:
            bg = f' background-color: {self._ZEBRA_BG};' if row_idx % 2 else ''
            rows.append(
                f'<tr>'
                f'<td style="padding: 4px 16px 4px 8px; color: #ffffff; '
                f'font-weight: 500; font-size: {cmd_sz}px; white-space: nowrap;{bg}">{cmd}</td>'
                f'<td style="padding: 4px 8px 4px 0; color: #5EAAFF; '
                f'font-size: {desc_sz}px;{bg}">{desc}</td>'
                f'</tr>'
            )
            row_idx += 1

        html = f'<table style="border-collapse: collapse;">{"".join(rows)}</table>'
        self._body.setText(html)

    def _render_scale(self, text: str) -> None:
        """Render scaled ingredients with current font sizes."""
        lines = text.split("\n")
        title = lines[0] if lines else "Scaled Ingredients"
        self._title.setText(title)

        body_sz = self._sz_body
        rows = []
        row_idx = 0
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            bg = f' background-color: {self._ZEBRA_BG};' if row_idx % 2 else ''
            rows.append(
                f'<tr><td style="padding: 4px 8px; color: #dddddd; '
                f'font-size: {body_sz}px;{bg}">{stripped}</td></tr>'
            )
            row_idx += 1
        html = f'<table style="border-collapse: collapse;">{"".join(rows)}</table>'
        self._body.setText(html)

    def _refresh_scale_content(self) -> None:
        """Re-render scale content with updated font sizes."""
        text = getattr(self, "_scale_text", None)
        if text:
            self._render_scale(text)

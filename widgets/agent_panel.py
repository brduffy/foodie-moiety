"""Floating AI prompt/response panel for the recipe detail view."""

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class AgentPanel(QWidget):
    """Floating panel with a prompt input and response area.

    Designed to overlay the right half of the recipe detail view,
    centered over the unblurred background image.
    """

    # Fraction of the available right-half space to occupy
    WIDTH_RATIO = 0.85
    HEIGHT_RATIO = 0.55
    MIN_WIDTH = 250
    MIN_HEIGHT = 160

    submitted = Signal(str)
    hidden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AgentPanel")

        self.setStyleSheet("""
            QWidget#AgentPanel {
                background-color: rgba(20, 20, 20, 230);
                border: 1px solid #555555;
                border-radius: 12px;
            }
            QLabel#AgentTitle {
                color: #cccccc;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }
            QLabel#ResponseLabel {
                color: #dddddd;
                background-color: #333333;
                border-radius: 6px;
                padding: 8px 10px;
            }
            QLineEdit {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 6px;
                padding: 8px 10px;
            }
            QLineEdit:focus {
                border-color: #0078d4;
                background-color: #333333;
            }
            QScrollArea#ResponseScroll {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 6px;
                background-color: transparent;
                border: none;
            }
            QScrollBar::handle:vertical {
                background-color: white;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QPushButton#DismissButton {
                background: transparent;
                color: #888888;
                border: none;
                font-size: 16px;
                font-weight: bold;
                padding: 0px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
            }
            QPushButton#DismissButton:hover {
                color: white;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(0)
        title = QLabel("Ask AI")
        title.setObjectName("AgentTitle")
        header.addWidget(title)
        header.addStretch()

        dismiss_btn = QPushButton("\u2715")
        dismiss_btn.setObjectName("DismissButton")
        dismiss_btn.setCursor(Qt.PointingHandCursor)
        dismiss_btn.clicked.connect(self.hide)
        header.addWidget(dismiss_btn)
        layout.addLayout(header)

        # Prompt input
        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask me anything...")
        self._input.returnPressed.connect(self._on_submit)
        layout.addWidget(self._input)

        self._dot_count = 1
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(400)
        self._thinking_timer.timeout.connect(self._animate_dots)

        # Font size tracking (matches ingredient/directions editors: 14-24px range)
        self._font_size = 14

        # Response area (scrollable)
        self._response_label = QLabel("")
        self._response_label.setObjectName("ResponseLabel")
        self._response_label.setWordWrap(True)
        self._response_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._apply_font_size()

        scroll = QScrollArea()
        scroll.setObjectName("ResponseScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self._response_label)
        layout.addWidget(scroll, stretch=1)

        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_thinking(self):
        """Show the animated thinking indicator in the response area."""
        self._dot_count = 1
        self._response_label.setText("Thinking.")
        self._thinking_timer.start()

    def show_response(self, text):
        """Stop the thinking indicator and display the response text."""
        self._thinking_timer.stop()
        self._response_label.setText(self._format_response(text))

    def adjust_font_size(self, delta):
        """Adjust the response and input font size by delta pixels.

        Uses the same range as ingredient/directions editors: 14-24px.
        """
        self._font_size = max(14, min(24, self._font_size + delta))
        self._apply_font_size()

    def focus_input(self):
        """Give keyboard focus to the prompt input."""
        self._input.setFocus()

    def set_input_text(self, text):
        """Set the prompt input text (used for voice transcription display)."""
        self._input.setText(text)

    def show_recording(self, push_to_talk=True):
        """Show recording indicator in the response area.

        Args:
            push_to_talk: If True, show manual stop hint. If False, auto-stop mode.
        """
        self._thinking_timer.stop()
        if push_to_talk:
            self._response_label.setText("\U0001F3A4 Recording... (press V to stop)")
        else:
            self._response_label.setText("\U0001F3A4 Listening...")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def hide(self):
        """Hide the panel and clear response text."""
        self._thinking_timer.stop()
        self._response_label.setText("")
        super().hide()
        self.hidden.emit()

    def _on_submit(self):
        text = self._input.text().strip()
        if text:
            self.submitted.emit(text)
            self._input.clear()

    def _apply_font_size(self):
        """Apply the current font size to the response label and input."""
        font = QFont()
        font.setPixelSize(self._font_size)
        self._response_label.setFont(font)
        self._input.setFont(font)

    @staticmethod
    def _format_response(text: str) -> str:
        """Convert plain text with indented lines into an HTML bulleted list."""
        lines = text.split("\n")
        if len(lines) <= 1:
            return text

        # Check if any lines are indented (list items)
        has_list = any(line.startswith("  ") and line.strip() for line in lines)
        if not has_list:
            return text

        parts = []
        in_list = False
        for line in lines:
            if line.startswith("  ") and line.strip():
                if not in_list:
                    parts.append("<ul style='margin:4px 0; padding-left:18px;'>")
                    in_list = True
                parts.append(f"<li style='margin-bottom:6px;'>{line.strip()}</li>")
            else:
                if in_list:
                    parts.append("</ul>")
                    in_list = False
                if line.strip():
                    parts.append(f"<b>{line}</b><br/>")
        if in_list:
            parts.append("</ul>")
        return "".join(parts)

    def _animate_dots(self):
        self._dot_count = (self._dot_count % 3) + 1
        self._response_label.setText("Thinking" + "." * self._dot_count)

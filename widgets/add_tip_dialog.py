"""Dialog for submitting a community tip on a recipe."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

_MAX_CHARS = 280

_STYLESHEET = """
    QDialog { background-color: #2a2a2a; }
    QLabel { color: white; font-size: 15px; }
    QLabel#SectionHeader {
        color: #66ccff; font-size: 18px; font-weight: bold;
    }
    QLabel#HintText {
        color: #cccccc; font-size: 13px;
    }
    QLabel#CharCounter {
        color: #888888; font-size: 12px;
    }
    QTextEdit {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 8px 10px; font-size: 15px;
    }
    QTextEdit:focus { border-color: #0078d4; }
    QPushButton {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 8px 18px; min-width: 80px; font-size: 14px;
    }
    QPushButton:hover { background-color: #4a4a4a; }
    QPushButton:pressed { background-color: #2a2a2a; }
    QPushButton#PrimaryButton {
        background-color: #0078d4; border-color: #0078d4;
    }
    QPushButton#PrimaryButton:hover { background-color: #1084d8; }
    QPushButton#PrimaryButton:disabled {
        background-color: #555555; border-color: #555555; color: #888888;
    }
"""


class AddTipDialog(QDialog):
    """Modal dialog for submitting a tip on a community recipe."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a Tip")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        header = QLabel("Add a Tip")
        header.setObjectName("SectionHeader")
        layout.addWidget(header)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("Share a helpful tip for this recipe...")
        self._text_edit.setAcceptRichText(False)
        self._text_edit.setFixedHeight(100)
        self._text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._text_edit)

        self._char_label = QLabel(f"0 / {_MAX_CHARS}")
        self._char_label.setObjectName("CharCounter")
        self._char_label.setAlignment(Qt.AlignRight)
        layout.addWidget(self._char_label)

        info = QLabel(
            "Great tips share ingredient swaps, technique improvements, "
            "or lessons learned \u2014 anything that helps the next cook. "
            "Tips are reviewed by the recipe creator before appearing."
        )
        info.setObjectName("HintText")
        info.setWordWrap(True)
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._submit_btn = QPushButton("Submit Tip")
        self._submit_btn.setObjectName("PrimaryButton")
        self._submit_btn.setEnabled(False)
        self._submit_btn.setDefault(True)
        self._submit_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._submit_btn)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_text_changed(self):
        text = self._text_edit.toPlainText()
        count = len(text)
        if count > _MAX_CHARS:
            # Truncate and restore cursor position
            cursor = self._text_edit.textCursor()
            pos = cursor.position()
            self._text_edit.blockSignals(True)
            self._text_edit.setPlainText(text[:_MAX_CHARS])
            cursor.setPosition(min(pos, _MAX_CHARS))
            self._text_edit.setTextCursor(cursor)
            self._text_edit.blockSignals(False)
            count = _MAX_CHARS
        self._char_label.setText(f"{count} / {_MAX_CHARS}")
        self._submit_btn.setEnabled(count > 0)

    def get_tip_text(self):
        """Return the tip text if accepted, or None."""
        return self._text_edit.toPlainText().strip() or None

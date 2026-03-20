"""Article link dialog - enter a URL restricted to foodiemoiety.com."""

from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


_ALLOWED_DOMAIN = "foodiemoiety.com"


def _is_valid_fm_url(url_text: str) -> bool:
    """Return True if *url_text* is a valid foodiemoiety.com URL."""
    text = url_text.strip()
    if not text:
        return False
    # Accept bare paths like /recipes/abc — treat as relative
    if text.startswith("/"):
        return True
    # Ensure scheme present for urlparse
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    # Allow foodiemoiety.com and any subdomain (e.g. www.foodiemoiety.com)
    return host == _ALLOWED_DOMAIN or host.endswith("." + _ALLOWED_DOMAIN)


def _normalize_fm_url(url_text: str) -> str:
    """Return a full https URL for the given input."""
    text = url_text.strip()
    if text.startswith("/"):
        return f"https://{_ALLOWED_DOMAIN}{text}"
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    # Upgrade http to https
    if text.startswith("http://"):
        text = "https://" + text[7:]
    return text


class ArticleLinkDialog(QDialog):
    """Dialog for entering a foodiemoiety.com URL to link to."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Insert Link")
        self.setModal(True)
        self.setFixedWidth(420)

        self._url = ""

        self.setStyleSheet("""
            QDialog {
                background-color: #2a2a2a;
            }
            QLabel {
                color: white;
                font-size: 13px;
            }
            QLabel#ErrorLabel {
                color: #ff6b6b;
                font-size: 12px;
                padding: 4px 0;
            }
            QLabel#HintLabel {
                color: #888888;
                font-size: 11px;
                padding: 2px 0;
            }
            QLineEdit {
                background-color: #3a3a3a;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 8px 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
            QPushButton {
                background-color: #3a3a3a;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 6px 16px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QPushButton#PrimaryButton {
                background-color: #0078d4;
                border-color: #0078d4;
            }
            QPushButton#PrimaryButton:hover {
                background-color: #1084d8;
            }
            QPushButton#PrimaryButton:disabled {
                background-color: #555555;
                border-color: #555555;
                color: #888888;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        instruction = QLabel("Enter a foodiemoiety.com URL:")
        layout.addWidget(instruction)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://foodiemoiety.com/...")
        self._url_input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._url_input)

        hint = QLabel("Links must stay inside foodiemoiety.com")
        hint.setObjectName("HintLabel")
        layout.addWidget(hint)

        self._error_label = QLabel("")
        self._error_label.setObjectName("ErrorLabel")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        self._ok_btn = QPushButton("Create Link")
        self._ok_btn.setObjectName("PrimaryButton")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_ok_clicked)
        self._ok_btn.setDefault(True)
        button_layout.addWidget(self._ok_btn)

        layout.addLayout(button_layout)

    def _on_text_changed(self, text):
        """Validate URL as user types."""
        stripped = text.strip()
        if not stripped:
            self._error_label.hide()
            self._ok_btn.setEnabled(False)
            return
        if _is_valid_fm_url(stripped):
            self._error_label.hide()
            self._ok_btn.setEnabled(True)
        else:
            self._error_label.setText("URL must be inside foodiemoiety.com")
            self._error_label.show()
            self._ok_btn.setEnabled(False)

    def _on_ok_clicked(self):
        text = self._url_input.text().strip()
        if _is_valid_fm_url(text):
            self._url = _normalize_fm_url(text)
            self.accept()

    def selected_url(self) -> str:
        """Return the validated, normalized URL."""
        return self._url

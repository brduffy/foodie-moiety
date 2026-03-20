"""Review action dialog — reason text + optional refund checkbox."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

_STYLESHEET = """
    QDialog { background-color: #2a2a2a; }
    QLabel { color: white; font-size: 13px; background: transparent; }
    QLineEdit {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 8px 10px; font-size: 14px;
    }
    QLineEdit:focus { border-color: #0078d4; }
    QCheckBox { color: white; font-size: 13px; spacing: 6px; }
    QCheckBox::indicator {
        width: 16px; height: 16px; border: 1px solid #555555;
        border-radius: 3px; background-color: #3a3a3a;
    }
    QCheckBox::indicator:checked {
        background-color: #0078d4; border-color: #0078d4;
    }
    QPushButton {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 6px 16px; min-width: 80px;
    }
    QPushButton:hover { background-color: #4a4a4a; }
    QPushButton#PrimaryButton {
        background-color: #0078d4; border-color: #0078d4;
    }
    QPushButton#PrimaryButton:hover { background-color: #1084d8; }
    QPushButton#PrimaryButton:disabled {
        background-color: #555555; border-color: #555555; color: #888888;
    }
"""


class ReviewActionDialog(QDialog):
    """Dialog for review actions with optional reason and refund checkbox."""

    def __init__(self, parent=None, title="Review", label="Reason:",
                 show_refund=False, show_bom_candidate=False,
                 reason_required=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(420)
        self.setStyleSheet(_STYLESHEET)

        self._reason_required = reason_required

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(QLabel(label))

        self._reason_input = QLineEdit()
        self._reason_input.setPlaceholderText(
            "Required" if reason_required else "Optional"
        )
        if reason_required:
            self._reason_input.textChanged.connect(self._update_ok_button)
        self._reason_input.returnPressed.connect(self._try_accept)
        layout.addWidget(self._reason_input)

        self._refund_check = None
        if show_refund:
            self._refund_check = QCheckBox("Refund upload count")
            layout.addWidget(self._refund_check)

        self._bom_check = None
        if show_bom_candidate:
            self._bom_check = QCheckBox("Save as Book of Moiety candidate")
            self._bom_check.setChecked(True)
            layout.addWidget(self._bom_check)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._ok_btn = QPushButton("Confirm")
        self._ok_btn.setObjectName("PrimaryButton")
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        if reason_required:
            self._ok_btn.setEnabled(False)
        btn_row.addWidget(self._ok_btn)

        layout.addLayout(btn_row)
        self._reason_input.setFocus()

    def _update_ok_button(self):
        self._ok_btn.setEnabled(bool(self._reason_input.text().strip()))

    def _try_accept(self):
        if self._ok_btn.isEnabled():
            self.accept()

    def reason(self) -> str:
        return self._reason_input.text().strip()

    def refund_upload(self) -> bool:
        if self._refund_check is None:
            return False
        return self._refund_check.isChecked()

    def save_bom_candidate(self) -> bool:
        if self._bom_check is None:
            return False
        return self._bom_check.isChecked()

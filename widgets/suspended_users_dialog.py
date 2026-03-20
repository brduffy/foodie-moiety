"""Dialog listing suspended users with unsuspend / cancel subscription actions."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_STYLESHEET = """
    QDialog { background-color: #2a2a2a; }
    QLabel { color: white; font-size: 13px; background: transparent; }
    QLabel#Muted { color: #999999; font-size: 11px; }
    QLabel#Empty { color: #888888; font-size: 14px; }
    QScrollArea { border: none; background: transparent; }
    QPushButton {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 4px 12px; font-size: 12px;
    }
    QPushButton:hover { background-color: #4a4a4a; }
    QPushButton#Unsuspend {
        background-color: #2a5a2a; border-color: #3a7a3a;
    }
    QPushButton#Unsuspend:hover { background-color: #3a7a3a; }
    QPushButton#CancelSub {
        background-color: #6a4a1a; border-color: #8a6a2a;
    }
    QPushButton#CancelSub:hover { background-color: #8a6a2a; }
    QPushButton#Close {
        padding: 6px 16px; min-width: 80px; font-size: 13px;
    }
"""


class SuspendedUsersDialog(QDialog):
    """Modal dialog showing suspended users with management actions."""

    unsuspend_clicked = Signal(str)     # userId
    cancel_sub_clicked = Signal(str)    # userId

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Suspended Users")
        self.setModal(True)
        self.setFixedWidth(500)
        self.setMinimumHeight(200)
        self.setMaximumHeight(500)
        self.setStyleSheet(_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Scroll area for user rows
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        self._content_layout.addStretch()
        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll)

        # Loading label (shown initially)
        self._loading_label = QLabel("Loading...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_layout.insertWidget(0, self._loading_label)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._user_rows: dict[str, QWidget] = {}

    def set_users(self, users: list):
        """Populate the dialog with suspended user data."""
        self._loading_label.hide()

        if not users:
            empty = QLabel("No suspended users")
            empty.setObjectName("Empty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_layout.insertWidget(0, empty)
            return

        for user in users:
            self._add_user_row(user)

    def set_error(self, message: str):
        """Show error instead of user list."""
        self._loading_label.setText(f"Error: {message}")

    def remove_user(self, user_id: str):
        """Remove a user row after successful action."""
        row = self._user_rows.pop(user_id, None)
        if row:
            row.setParent(None)
            row.deleteLater()
        if not self._user_rows:
            empty = QLabel("No suspended users")
            empty.setObjectName("Empty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_layout.insertWidget(0, empty)

    def _add_user_row(self, user: dict):
        user_id = user.get("userId", "")
        email = user.get("email", "Unknown")
        suspended_at = user.get("suspendedAt", "")
        reason = user.get("suspendReason", "")

        row = QWidget()
        row.setStyleSheet(
            "QWidget { background-color: #333333; border-radius: 4px; }"
        )
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(4)

        # Email
        email_label = QLabel(email)
        email_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        row_layout.addWidget(email_label)

        # Date + reason
        detail_parts = []
        if suspended_at:
            date_str = suspended_at[:10] if len(suspended_at) >= 10 else suspended_at
            detail_parts.append(f"Suspended {date_str}")
        if reason:
            truncated = reason if len(reason) <= 60 else reason[:57] + "..."
            detail_parts.append(truncated)
        if detail_parts:
            detail_label = QLabel(" \u2014 ".join(detail_parts))
            detail_label.setObjectName("Muted")
            detail_label.setWordWrap(True)
            row_layout.addWidget(detail_label)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch()

        unsuspend_btn = QPushButton("Unsuspend")
        unsuspend_btn.setObjectName("Unsuspend")
        unsuspend_btn.clicked.connect(lambda _, uid=user_id: self.unsuspend_clicked.emit(uid))
        btn_row.addWidget(unsuspend_btn)

        cancel_btn = QPushButton("Cancel Subscription")
        cancel_btn.setObjectName("CancelSub")
        cancel_btn.clicked.connect(lambda _, uid=user_id: self.cancel_sub_clicked.emit(uid))
        btn_row.addWidget(cancel_btn)

        row_layout.addLayout(btn_row)

        idx = self._content_layout.count() - 1  # before stretch
        self._content_layout.insertWidget(idx, row)
        self._user_rows[user_id] = row

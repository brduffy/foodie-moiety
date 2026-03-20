"""Dialog listing all published items by a specific producer/uploader."""

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
    QLabel#Type { color: #7799bb; font-size: 11px; }
    QScrollArea { border: none; background: transparent; }
    QPushButton {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 4px 12px; font-size: 12px;
    }
    QPushButton:hover { background-color: #4a4a4a; }
    QPushButton#Compare {
        background-color: #2a4a6a; border-color: #3a6a8a;
    }
    QPushButton#Compare:hover { background-color: #3a6a8a; }
    QPushButton#Close {
        padding: 6px 16px; min-width: 80px; font-size: 13px;
    }
"""


class ProducerItemsDialog(QDialog):
    """Modal dialog showing all published items by a producer."""

    compare_clicked = Signal(dict)  # emits the full normalized item dict

    def __init__(self, producer_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            f"Items by {producer_name}" if producer_name else "Producer Items"
        )
        self.setModal(True)
        self.setFixedWidth(500)
        self.setMinimumHeight(200)
        self.setMaximumHeight(500)
        self.setStyleSheet(_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Scroll area for item rows
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

        # Loading label
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

    def set_items(self, items: list):
        """Populate the dialog with producer's published items."""
        self._loading_label.hide()

        if not items:
            empty = QLabel("No published items found")
            empty.setObjectName("Empty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_layout.insertWidget(0, empty)
            return

        for item in items:
            self._add_item_row(item)

    def set_error(self, message: str):
        """Show error instead of item list."""
        self._loading_label.setText(f"Error: {message}")

    def _add_item_row(self, item: dict):
        row = QWidget()
        row.setStyleSheet(
            "QWidget { background-color: #333333; border-radius: 4px; }"
        )
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(4)

        # Title
        title = item.get("title", "Untitled")
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        row_layout.addWidget(title_label)

        # Type + producer
        item_type = item.get("type", "recipe").capitalize()
        type_label = QLabel(item_type)
        type_label.setObjectName("Type")
        row_layout.addWidget(type_label)

        # Compare button
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch()

        compare_btn = QPushButton("Preview")
        compare_btn.setObjectName("Compare")
        compare_btn.clicked.connect(
            lambda _, i=item: self.compare_clicked.emit(i)
        )
        btn_row.addWidget(compare_btn)

        row_layout.addLayout(btn_row)

        idx = self._content_layout.count() - 1  # before stretch
        self._content_layout.insertWidget(idx, row)

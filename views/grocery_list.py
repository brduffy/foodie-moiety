"""Grocery list view — editable staging list for ingredients before sending to phone."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from utils.database import (
    add_grocery_item,
    delete_grocery_item,
    get_grocery_items,
    update_grocery_item,
    clear_grocery_items,
)


class GroceryItemRow(QWidget):
    """Single editable row in the grocery list."""

    removed = Signal(int)       # Emits item_id
    textEdited = Signal(int, str)  # Emits (item_id, new_text)

    def __init__(self, item_id, text, parent=None):
        super().__init__(parent)
        self.item_id = item_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(8)

        self._edit = QLineEdit(text)
        self._edit.setObjectName("ItemEdit")
        self._edit.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._edit, stretch=1)

        del_btn = QPushButton("✕")
        del_btn.setObjectName("DeleteBtn")
        del_btn.setFixedSize(24, 24)
        del_btn.clicked.connect(lambda: self.removed.emit(self.item_id))
        layout.addWidget(del_btn)

        self._last_text = text

    def _on_editing_finished(self):
        new_text = self._edit.text().strip()
        if new_text and new_text != self._last_text:
            self._last_text = new_text
            self.textEdited.emit(self.item_id, new_text)

    def get_text(self):
        return self._edit.text().strip()


class GroceryListView(QWidget):
    """Scrollable editable grocery list with add/remove and Pushover send."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(self._view_style())
        self._rows: list[GroceryItemRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Command bar container (populated by MainWindow.set_command_bar)
        self.command_bar_container = QWidget()
        self.command_bar_container.setObjectName("CommandBarContainer")
        self._command_bar_layout = QVBoxLayout(self.command_bar_container)
        self._command_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._command_bar_layout.setSpacing(0)
        root.addWidget(self.command_bar_container)

        # Content area
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(12)

        # Header row: count label
        self._count_label = QLabel("0 items")
        self._count_label.setObjectName("CountLabel")
        content_layout.addWidget(self._count_label)

        # Add-item row
        add_row = QWidget()
        add_layout = QHBoxLayout(add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(8)

        self._add_input = QLineEdit()
        self._add_input.setObjectName("AddInput")
        self._add_input.setPlaceholderText("Add item...")
        self._add_input.returnPressed.connect(self._on_add_clicked)
        add_layout.addWidget(self._add_input, stretch=1)

        add_btn = QPushButton("Add")
        add_btn.setObjectName("AddBtn")
        add_btn.setFixedHeight(32)
        add_btn.clicked.connect(self._on_add_clicked)
        add_layout.addWidget(add_btn)

        content_layout.addWidget(add_row)

        # Scroll area for items
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setObjectName("GroceryScrollArea")

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_widget)
        content_layout.addWidget(self._scroll, stretch=1)

        root.addWidget(content, stretch=1)

    # ── Command bar embed ─────────────────────────────────────────────

    def set_command_bar(self, command_bar):
        """Embed a command bar widget at the top of this view."""
        while self._command_bar_layout.count():
            item = self._command_bar_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        if command_bar:
            self._command_bar_layout.addWidget(command_bar)
            command_bar.show()

    # ── Data ──────────────────────────────────────────────────────────

    def load_items(self):
        """Reload all items from the database."""
        # Clear existing rows
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        for item in get_grocery_items():
            self._insert_row(item["id"], item["text"])
        self._update_count()

    def add_item(self, text):
        """Add a single item to the list and DB."""
        text = text.strip()
        if not text:
            return
        item_id = add_grocery_item(text)
        self._insert_row(item_id, text)
        self._update_count()

    def add_items(self, texts):
        """Add multiple items to the list and DB."""
        for t in texts:
            t = t.strip()
            if t:
                item_id = add_grocery_item(t)
                self._insert_row(item_id, t)
        self._update_count()

    def clear_all(self):
        """Remove all items from the list and DB."""
        clear_grocery_items()
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._update_count()

    def get_all_text(self):
        """Return all item texts as a list of strings."""
        return [row.get_text() for row in self._rows if row.get_text()]

    @property
    def item_count(self):
        return len(self._rows)

    # ── Internal ──────────────────────────────────────────────────────

    def _insert_row(self, item_id, text):
        """Create a row widget and insert it above the stretch."""
        row = GroceryItemRow(item_id, text)
        row.removed.connect(self._on_remove)
        row.textEdited.connect(self._on_text_edited)
        # Insert before the stretch (last item in layout)
        idx = self._list_layout.count() - 1
        self._list_layout.insertWidget(idx, row)
        self._rows.append(row)

    def _on_add_clicked(self):
        text = self._add_input.text().strip()
        if not text:
            return
        self.add_item(text)
        self._add_input.clear()
        self._add_input.setFocus()

    def _on_remove(self, item_id):
        delete_grocery_item(item_id)
        for row in self._rows:
            if row.item_id == item_id:
                self._rows.remove(row)
                row.setParent(None)
                row.deleteLater()
                break
        self._update_count()

    def _on_text_edited(self, item_id, text):
        update_grocery_item(item_id, text)

    def _update_count(self):
        n = len(self._rows)
        self._count_label.setText(f"{n} item{'s' if n != 1 else ''}")

    # ── Styling ───────────────────────────────────────────────────────

    @staticmethod
    def _view_style():
        return """
            QWidget {
                background-color: #1a1a1a;
            }
            QLabel#CountLabel {
                color: #888888;
                font-size: 14px;
                font-weight: bold;
            }
            QLineEdit#AddInput {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 15px;
                selection-background-color: #0078d4;
            }
            QLineEdit#AddInput:focus {
                border-color: #0078d4;
            }
            QLineEdit#ItemEdit {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 15px;
                selection-background-color: #0078d4;
            }
            QLineEdit#ItemEdit:focus {
                border-color: #0078d4;
            }
            QPushButton#AddBtn {
                background-color: #0078d4;
                color: white;
                border: 1px solid #0078d4;
                border-radius: 4px;
                font-size: 14px;
                padding: 4px 18px;
            }
            QPushButton#AddBtn:hover {
                background-color: #1084d8;
            }
            QPushButton#DeleteBtn {
                background-color: transparent;
                color: #888888;
                border: 1px solid #444444;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#DeleteBtn:hover {
                background-color: #cc0000;
                color: white;
                border-color: #cc0000;
            }
            QScrollArea#GroceryScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 6px;
                background-color: transparent;
            }
            QScrollBar::handle:vertical {
                background-color: #555555;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """

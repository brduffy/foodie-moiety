"""Right-half overlay panel for browsing and inserting moiety steps."""

from collections import OrderedDict

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

from utils.database import get_all_moieties

_ENTRY_NORMAL_SS = """
    QWidget#MoietyEntry {
        background-color: transparent;
        border-radius: 4px;
        padding: 4px 8px;
    }
"""
_ENTRY_HOVER_SS = """
    QWidget#MoietyEntry {
        background-color: rgba(255, 255, 255, 15);
        border-radius: 4px;
        padding: 4px 8px;
    }
"""
_ENTRY_SEL_SS = """
    QWidget#MoietyEntry {
        background-color: rgba(34, 102, 204, 0.5);
        border-radius: 4px;
        padding: 4px 8px;
    }
"""


class _MoietyEntry(QWidget):
    """A single clickable moiety row in the panel."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, title: str, attribution: str, parent=None):
        super().__init__(parent)
        self.setObjectName("MoietyEntry")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_ENTRY_NORMAL_SS)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color: #ffffff; font-size: 14px; background: transparent;"
        )
        title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(title_lbl)

        attr_lbl = QLabel(attribution)
        attr_lbl.setStyleSheet(
            "color: #888888; font-size: 11px; background: transparent;"
        )
        attr_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(attr_lbl)

        self._selected = False

    def set_selected(self, selected: bool):
        self._selected = selected
        self.setStyleSheet(_ENTRY_SEL_SS if selected else _ENTRY_NORMAL_SS)

    def enterEvent(self, event):
        if not self._selected:
            self.setStyleSheet(_ENTRY_HOVER_SS)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(_ENTRY_SEL_SS if self._selected else _ENTRY_NORMAL_SS)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class MoietyPanel(QWidget):
    """Overlay panel for browsing moieties and inserting their steps.

    Positioned on the right half of the recipe detail view.
    Shows user-created moieties under "My Moieties" and book-owned
    moieties grouped by book title and category.
    """

    insert_requested = Signal(int)
    preview_requested = Signal(int)
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MoietyPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#MoietyPanel {
                background-color: rgba(20, 20, 20, 230);
                border-left: 1px solid rgba(255, 255, 255, 40);
            }
            QPushButton#PanelCloseBtn {
                background: transparent;
                color: #888888;
                border: none;
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
                min-width: 32px; max-width: 32px;
                min-height: 32px; max-height: 32px;
            }
            QPushButton#PanelCloseBtn:hover {
                color: white;
            }
            QLabel#PanelTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 600;
                background: transparent;
            }
            QLineEdit#MoietySearch {
                background-color: rgba(255, 255, 255, 10);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 6px;
                color: #ffffff;
                font-size: 14px;
                padding: 6px 10px;
            }
            QLineEdit#MoietySearch:focus {
                border: 1px solid rgba(94, 170, 255, 0.6);
            }
            QScrollArea#PanelScroll {
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
            QPushButton#InsertBtn {
                background-color: #3a6a3a;
                color: white;
                border: 1px solid #5a8a5a;
                border-radius: 6px;
                font-size: 15px;
                font-weight: 600;
                padding: 10px 32px;
            }
            QPushButton#InsertBtn:hover {
                background-color: #4a8a4a;
            }
            QPushButton#InsertBtn:disabled {
                background-color: #2a3a2a;
                color: #666666;
                border: 1px solid #3a4a3a;
            }
            QPushButton#PreviewBtn {
                background-color: rgba(255, 255, 255, 8);
                color: #aaaaaa;
                border: 1px solid rgba(255, 255, 255, 25);
                border-radius: 6px;
                font-size: 15px;
                font-weight: 600;
                padding: 10px 24px;
            }
            QPushButton#PreviewBtn:hover {
                background-color: rgba(255, 255, 255, 15);
                color: #cccccc;
            }
            QPushButton#PreviewBtn:disabled {
                color: #555555;
                border: 1px solid rgba(255, 255, 255, 10);
            }
            QLabel#SectionHeader {
                color: #f0c040;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 8px 4px 8px;
                background: transparent;
            }
            QLabel#CategoryHeader {
                color: #aaaaaa;
                font-size: 12px;
                font-weight: 600;
                padding: 6px 8px 2px 16px;
                background: transparent;
            }
            QLabel#EmptyLabel {
                color: #888888;
                font-size: 14px;
                background: transparent;
            }
        """)

        self._moieties = []  # raw data from get_all_moieties()
        self._entries = []   # list of (recipe_id, _MoietyEntry)
        self._selected_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        # Header: title + close
        header = QHBoxLayout()
        header.setSpacing(0)
        title = QLabel("Moiety Panel")
        title.setObjectName("PanelTitle")
        header.addWidget(title)
        header.addStretch()

        close_btn = QPushButton("\u2715")
        close_btn.setObjectName("PanelCloseBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self._dismiss)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Search
        self._search = QLineEdit()
        self._search.setObjectName("MoietySearch")
        self._search.setPlaceholderText("Search moieties\u2026")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # Scrollable list
        self._scroll = QScrollArea()
        self._scroll.setObjectName("PanelScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content.setAttribute(Qt.WA_TranslucentBackground)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._content_layout.addStretch()
        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll, stretch=1)

        # Empty state label (shown when no moieties)
        self._empty_label = QLabel(
            "No moieties yet.\nSave a recipe as a moiety to see it here."
        )
        self._empty_label.setObjectName("EmptyLabel")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.hide()
        layout.addWidget(self._empty_label)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._insert_btn = QPushButton("Insert")
        self._insert_btn.setObjectName("InsertBtn")
        self._insert_btn.setCursor(Qt.PointingHandCursor)
        self._insert_btn.setEnabled(False)
        self._insert_btn.clicked.connect(self._on_insert)
        btn_row.addWidget(self._insert_btn)

        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setObjectName("PreviewBtn")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.setEnabled(False)
        self._preview_btn.setToolTip("Preview moiety steps")
        self._preview_btn.clicked.connect(self._on_preview)
        btn_row.addWidget(self._preview_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.hide()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def refresh(self):
        """Reload moiety list from the database and rebuild the UI."""
        self._moieties = get_all_moieties()
        self._selected_id = None
        self._insert_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._search.clear()
        self._rebuild_list(self._moieties)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild_list(self, moieties):
        """Clear and repopulate the scrollable list from *moieties*."""
        # Clear existing content
        self._entries.clear()
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not moieties:
            self._empty_label.show()
            self._scroll.hide()
            return

        self._empty_label.hide()
        self._scroll.show()

        # Split into user moieties and book moieties
        user_moieties = [m for m in moieties if m.get("book_id") is None]
        book_moieties = [m for m in moieties if m.get("book_id") is not None]

        # "My Moieties" section
        if user_moieties:
            hdr = QLabel("My Moieties")
            hdr.setObjectName("SectionHeader")
            self._content_layout.addWidget(hdr)
            for m in user_moieties:
                self._add_entry(m, "by You")

        # Book moiety sections grouped by book_title then category_name
        if book_moieties:
            books = OrderedDict()
            for m in book_moieties:
                bt = m.get("book_title") or "Unknown Book"
                cat = m.get("category_name") or "Uncategorized"
                books.setdefault(bt, OrderedDict()).setdefault(cat, []).append(m)

            for book_title, categories in books.items():
                bhdr = QLabel(book_title)
                bhdr.setObjectName("SectionHeader")
                self._content_layout.addWidget(bhdr)
                for cat_name, items in categories.items():
                    chdr = QLabel(cat_name)
                    chdr.setObjectName("CategoryHeader")
                    self._content_layout.addWidget(chdr)
                    for m in items:
                        producer = m.get("producer") or "Unknown"
                        self._add_entry(m, f"by {producer}")

        self._content_layout.addStretch()

    def _add_entry(self, moiety_dict, attribution):
        """Create and add a single moiety entry to the content layout."""
        rid = moiety_dict["id"]
        entry = _MoietyEntry(moiety_dict["title"], attribution)
        entry.clicked.connect(lambda r=rid: self._select(r))
        entry.double_clicked.connect(lambda r=rid: self._on_double_click(r))
        self._content_layout.addWidget(entry)
        self._entries.append((rid, entry))

    def _select(self, recipe_id):
        """Select a moiety entry and deselect others."""
        self._selected_id = recipe_id
        for rid, entry in self._entries:
            entry.set_selected(rid == recipe_id)
        self._insert_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)

    def _on_double_click(self, recipe_id):
        """Double-click triggers select + insert."""
        self._select(recipe_id)
        self._on_insert()

    def _on_insert(self):
        if self._selected_id is not None:
            self.insert_requested.emit(self._selected_id)

    def _on_preview(self):
        if self._selected_id is not None:
            self.preview_requested.emit(self._selected_id)

    def _on_search(self, text):
        """Filter the displayed moieties by title substring."""
        query = text.strip().lower()
        if not query:
            self._rebuild_list(self._moieties)
            return
        filtered = [m for m in self._moieties if query in m["title"].lower()]
        self._rebuild_list(filtered)

    def _dismiss(self):
        self.hide()
        self.dismissed.emit()

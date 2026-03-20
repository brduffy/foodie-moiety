"""Community detail view — preview a community recipe or book before downloading."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from widgets.tags_editor import FlowLayout

_IMAGE_WIDTH = 400


class CommunityDetailView(QWidget):
    """Read-only preview of a community recipe or book with download action."""

    download_requested = Signal(str)  # Emits community_id
    purchase_requested = Signal(str)  # Emits community_id for paid book purchase

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #1a1a1a;")
        self._community_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Command bar container (populated by MainWindow)
        self.command_bar_container = QWidget()
        self.command_bar_container.setObjectName("CommandBarContainer")
        self._command_bar_layout = QVBoxLayout(self.command_bar_container)
        self._command_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._command_bar_layout.setSpacing(0)
        layout.addWidget(self.command_bar_container)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background-color: transparent; }
            QScrollBar:vertical {
                width: 6px; background-color: #1a1a1a; border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #555555; border-radius: 3px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background-color: #777777; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        """)

        content = QWidget()
        content.setStyleSheet("background-color: #1a1a1a;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 20, 30, 30)
        content_layout.setSpacing(0)

        # ---- Top section: image left, info right ----
        top_row = QHBoxLayout()
        top_row.setSpacing(24)

        # Image (left, fixed width, aspect ratio preserved)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._image_label.setFixedWidth(_IMAGE_WIDTH)
        self._image_label.setMinimumHeight(250)
        self._image_label.setStyleSheet(
            "background-color: #2a2a2a; border-radius: 8px;"
        )
        self._image_label.setScaledContents(False)
        top_row.addWidget(self._image_label, stretch=0, alignment=Qt.AlignTop)

        # Info column (right)
        info_col = QVBoxLayout()
        info_col.setSpacing(0)

        # Title
        self._title_label = QLabel()
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet(
            "color: white; font-size: 22px; font-weight: bold; "
            "background: transparent;"
        )
        info_col.addWidget(self._title_label)
        info_col.addSpacing(6)

        # Producer + date
        self._meta_row = QLabel()
        self._meta_row.setWordWrap(True)
        self._meta_row.setStyleSheet(
            "color: #999999; font-size: 13px; font-style: italic; "
            "background: transparent;"
        )
        info_col.addWidget(self._meta_row)
        info_col.addSpacing(14)

        # Type + metadata badges
        self._badges_container = QWidget()
        self._badges_container.setStyleSheet("background: transparent;")
        self._badges_layout = FlowLayout(self._badges_container, h_spacing=8, v_spacing=6)
        info_col.addWidget(self._badges_container)
        info_col.addSpacing(10)

        # Price + action row
        self._price_row = QWidget()
        self._price_row.setStyleSheet("background: transparent;")
        price_layout = QHBoxLayout(self._price_row)
        price_layout.setContentsMargins(0, 0, 0, 0)
        price_layout.setSpacing(12)

        self._price_label = QLabel()
        self._price_label.setStyleSheet(
            "color: #4ade80; font-size: 18px; font-weight: bold; "
            "background: transparent;"
        )
        price_layout.addWidget(self._price_label)

        self._ownership_badge = QLabel()
        self._ownership_badge.setStyleSheet(
            "background-color: #1a3a1a; color: #4ade80; "
            "border: 1px solid #4ade80; border-radius: 10px; "
            "padding: 3px 10px; font-size: 12px;"
        )
        self._ownership_badge.hide()
        price_layout.addWidget(self._ownership_badge)

        self._buy_btn = QPushButton()
        self._buy_btn.setCursor(Qt.PointingHandCursor)
        self._buy_btn.setStyleSheet(
            "QPushButton { background-color: #1a6b3a; color: white; "
            "border: none; border-radius: 6px; padding: 8px 20px; "
            "font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #22874a; }"
        )
        self._buy_btn.clicked.connect(self._on_buy_clicked)
        self._buy_btn.hide()
        price_layout.addWidget(self._buy_btn)

        price_layout.addStretch()
        self._price_row.hide()
        info_col.addWidget(self._price_row)

        top_row.addLayout(info_col, stretch=1)
        content_layout.addLayout(top_row)
        content_layout.addSpacing(20)

        # ---- Divider ----
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #333333;")
        divider.setFixedHeight(1)
        content_layout.addWidget(divider)
        content_layout.addSpacing(16)

        # ---- Description (full width, below) ----
        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(
            "color: #e0e0e0; font-size: 16px; background: transparent;"
        )
        content_layout.addWidget(self._desc_label)
        content_layout.addSpacing(16)

        # ---- Table of Contents (books only) ----
        self._toc_header = QLabel("Contents")
        self._toc_header.setStyleSheet(
            "color: #888888; font-size: 12px; font-weight: bold; "
            "background: transparent;"
        )
        self._toc_header.hide()
        content_layout.addWidget(self._toc_header)
        content_layout.addSpacing(8)

        self._toc_container = QWidget()
        self._toc_container.setStyleSheet("background: transparent;")
        self._toc_layout = QVBoxLayout(self._toc_container)
        self._toc_layout.setContentsMargins(0, 0, 0, 0)
        self._toc_layout.setSpacing(4)
        self._toc_container.hide()
        content_layout.addWidget(self._toc_container)
        content_layout.addSpacing(16)

        # ---- Tags (full width, below) ----
        tags_header = QLabel("Tags")
        tags_header.setStyleSheet(
            "color: #888888; font-size: 12px; font-weight: bold; "
            "background: transparent; text-transform: uppercase;"
        )
        self._tags_header = tags_header
        content_layout.addWidget(tags_header)
        content_layout.addSpacing(8)

        self._tags_container = QWidget()
        self._tags_container.setStyleSheet("background: transparent;")
        self._tags_layout = FlowLayout(self._tags_container, h_spacing=6, v_spacing=6)
        content_layout.addWidget(self._tags_container)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

    def set_command_bar(self, command_bar):
        """Embed a command bar widget at the top of this view."""
        while self._command_bar_layout.count():
            item = self._command_bar_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        if command_bar:
            self._command_bar_layout.addWidget(command_bar)
            command_bar.show()

    def load_item(self, item: dict, pixmap: QPixmap | None = None):
        """Populate the view with community item data.

        Args:
            item: Normalized item dict from the API.
            pixmap: Optional pre-loaded thumbnail pixmap.
        """
        self._community_id = item.get("community_id", "")
        is_book = item.get("type") == "book"

        # Image — scale to fill width, preserve aspect ratio
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaledToWidth(
                _IMAGE_WIDTH, Qt.SmoothTransformation
            )
            self._image_label.setPixmap(scaled)
            self._image_label.setFixedHeight(scaled.height())
        else:
            self._image_label.clear()
            self._image_label.setText("No image available")
            self._image_label.setFixedHeight(250)
            self._image_label.setStyleSheet(
                "background-color: #2a2a2a; border-radius: 8px; "
                "color: #666666; font-size: 14px;"
            )

        # Title
        self._title_label.setText(item.get("title", "Untitled"))

        # Producer + date
        parts = []
        producer = item.get("producer", "")
        if producer:
            parts.append(f"by {producer}")
        uploaded = item.get("uploaded_at", "")
        if uploaded:
            date_str = uploaded[:10] if len(uploaded) >= 10 else uploaded
            parts.append(date_str)
        self._meta_row.setText("  \u00b7  ".join(parts) if parts else "")

        # Badges
        self._clear_layout(self._badges_layout)
        if is_book:
            self._add_badge("Book", "#1a2540", "#4a6a9a")
            rc = item.get("recipe_count")
            if rc:
                self._add_badge(f"{rc} recipes", "#2a2a2a", "#555555")
            cc = item.get("category_count")
            if cc:
                self._add_badge(f"{cc} categories", "#2a2a2a", "#555555")
        else:
            self._add_badge("Recipe", "#1a2e1a", "#4a8a4a")
            ic = item.get("ingredient_count")
            if ic:
                self._add_badge(f"{ic} ingredients", "#2a2a2a", "#555555")
        difficulty = item.get("difficulty", "")
        if difficulty:
            self._add_badge(difficulty, "#2a2a2a", "#555555")
        total_time = item.get("total_time_min")
        if total_time:
            self._add_badge(f"{total_time} min", "#2a2a2a", "#555555")
        prep_time = item.get("prep_time_min")
        if prep_time:
            self._add_badge(f"Prep {prep_time} min", "#2a2a2a", "#555555")
        cook_time = item.get("cook_time_min")
        if cook_time:
            self._add_badge(f"Cook {cook_time} min", "#2a2a2a", "#555555")
        cuisine = item.get("cuisine_type", "")
        if cuisine:
            self._add_badge(cuisine, "#2a2a2a", "#555555")

        # Price + Buy/Download
        price_type = item.get("price_type", "free")
        price_cents = item.get("price_cents", 0)
        is_purchased = item.get("is_purchased", False)
        is_creator = item.get("is_creator", False)

        if price_type == "paid" and price_cents > 0:
            price_str = f"${price_cents / 100:.2f}"
            self._price_label.setText(price_str)
            self._price_label.show()
            self._price_row.show()

            if is_purchased:
                self._ownership_badge.setText("Purchased")
                self._ownership_badge.show()
                self._buy_btn.hide()
            elif is_creator:
                self._ownership_badge.setText("Your Book")
                self._ownership_badge.show()
                self._buy_btn.hide()
            else:
                self._ownership_badge.hide()
                self._buy_btn.setText(f"Buy \u2014 {price_str}")
                self._buy_btn.show()
        else:
            self._price_row.hide()
            self._price_label.hide()
            self._ownership_badge.hide()
            self._buy_btn.hide()

        # Description
        desc = item.get("description", "")
        if desc:
            self._desc_label.setText(desc)
            self._desc_label.show()
        else:
            self._desc_label.hide()

        # Table of Contents (books only)
        self._clear_layout(self._toc_layout)
        categories = item.get("categories", [])
        if categories and is_book:
            self._toc_header.show()
            self._toc_container.show()
            for cat in categories:
                cat_label = QLabel(cat.get("name", ""))
                cat_label.setStyleSheet(
                    "color: #cccccc; font-size: 14px; font-weight: bold; "
                    "background: transparent; padding-top: 4px;"
                )
                self._toc_layout.addWidget(cat_label)
                for recipe_title in cat.get("recipes", []):
                    recipe_label = QLabel(f"  \u2022  {recipe_title}")
                    recipe_label.setStyleSheet(
                        "color: #aaaaaa; font-size: 13px; "
                        "background: transparent; padding-left: 12px;"
                    )
                    self._toc_layout.addWidget(recipe_label)
        else:
            self._toc_header.hide()
            self._toc_container.hide()

        # Tags
        self._clear_layout(self._tags_layout)
        tags = item.get("tags", [])
        if tags:
            self._tags_header.show()
            for tag in tags:
                pill = QFrame()
                pill.setStyleSheet(
                    "QFrame { background-color: #333333; border: 1px solid #555555; "
                    "border-radius: 12px; }"
                )
                pill_layout = QHBoxLayout(pill)
                pill_layout.setContentsMargins(10, 4, 10, 4)
                pill_label = QLabel(tag)
                pill_label.setStyleSheet(
                    "color: #cccccc; font-size: 12px; background: transparent;"
                )
                pill_layout.addWidget(pill_label)
                self._tags_layout.addWidget(pill)
        else:
            self._tags_header.hide()

    def _add_badge(self, text, bg_color, border_color):
        """Add a metadata badge to the badges row."""
        badge = QLabel(text)
        badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        badge.setStyleSheet(
            f"background-color: {bg_color}; color: #cccccc; "
            f"border: 1px solid {border_color}; border-radius: 10px; "
            f"padding: 3px 10px; font-size: 12px;"
        )
        self._badges_layout.addWidget(badge)

    def _on_buy_clicked(self):
        """Emit purchase signal for paid book."""
        if self._community_id:
            self.purchase_requested.emit(self._community_id)

    @staticmethod
    def _clear_layout(layout):
        """Remove all widgets from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()

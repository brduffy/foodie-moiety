"""Recipe list view - scrollable grid of fixed-size recipe cards from database."""

import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QCursor, QFont, QFontMetrics, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from utils.database import get_all_recipes, get_total_recipe_count, search_all_with_tags, search_recipes_with_tags
from utils.helpers import platform_icon
from widgets.tag_filter import TagSidePanel

# Cache card overlay icons (SF Symbol rendering is expensive — do it once)
_CARD_ICONS: dict[str, QIcon] = {}

# Cache decoded pixmaps so re-displaying the same recipes avoids disk I/O
_PIXMAP_CACHE: dict[str, QPixmap] = {}


def _get_card_icon(name, **kwargs):
    """Return a cached platform icon, creating it on first use."""
    key = f"{name}:{kwargs}"
    if key not in _CARD_ICONS:
        _CARD_ICONS[key] = platform_icon(name, **kwargs)
    return _CARD_ICONS[key]


# ---------- Flow Layout ----------

class FlowLayout(QLayout):
    """A layout that arranges widgets in a flowing left-to-right, top-to-bottom grid."""

    def __init__(self, parent=None, margin=20, h_spacing=16, v_spacing=16):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(Qt.Orientation(0), width, test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, rect.width())

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        from PySide6.QtCore import QSize
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect_or_dummy, width, test_only=False):
        from PySide6.QtCore import QRect

        margins = self.contentsMargins()
        effective_width = width - margins.left() - margins.right()

        if not self._items:
            return margins.top() + margins.bottom()

        # First pass: group items into rows based on available width
        rows = []
        current_row = []
        row_width = 0

        for item in self._items:
            item_width = item.sizeHint().width()
            needed = item_width if not current_row else item_width + self._h_spacing
            if current_row and row_width + needed > effective_width:
                rows.append(current_row)
                current_row = [item]
                row_width = item_width
            else:
                current_row.append(item)
                row_width += needed
        if current_row:
            rows.append(current_row)

        # Second pass: position each row centered horizontally
        y = margins.top()

        for row_items in rows:
            # Calculate total width of this row
            total_row_width = sum(it.sizeHint().width() for it in row_items)
            total_row_width += self._h_spacing * (len(row_items) - 1)
            # Center offset
            x_offset = margins.left() + (effective_width - total_row_width) // 2

            item_sizes = []
            for item in row_items:
                item_width = item.sizeHint().width()
                item_height = item.sizeHint().height()
                item_sizes.append((item_width, item_height))

            row_height = max(h for _, h in item_sizes)
            x = x_offset
            for item, (item_width, item_height) in zip(row_items, item_sizes):
                if not test_only:
                    item.setGeometry(QRect(x, y, item_width, item_height))
                x += item_width + self._h_spacing

            y += row_height + self._v_spacing

        return y - self._v_spacing + margins.bottom()


# ---------- Recipe Card ----------

CARD_WIDTH = 280
IMAGE_HEIGHT = 158  # 280 * 9/16 for 16:9 ratio
# X positions for 4 evenly spaced 28px buttons across CARD_WIDTH
_BTN_X = [CARD_WIDTH * i // 4 + (CARD_WIDTH // 4 - 28) // 2 for i in range(4)]


class RecipeCard(QFrame):
    """A fixed-size clickable recipe card with 16:9 image area."""

    clicked = Signal(int)  # Emits recipe_id
    book_clicked = Signal(int)  # Emits book_id when a book card is clicked
    copy_clicked = Signal(int)  # Emits recipe_id for clipboard copy
    export_clicked = Signal(int)  # Emits recipe_id for export
    book_export_clicked = Signal(int)  # Emits book_id for export
    delete_clicked = Signal(int, str)  # Emits (recipe_id, title) for delete confirmation
    book_delete_clicked = Signal(int, str)  # Emits (book_id, title) for delete confirmation
    add_to_book_clicked = Signal(int)  # Emits recipe_id for add-to-book mode
    upload_clicked = Signal(int)  # Emits recipe_id for community upload
    book_upload_clicked = Signal(int)  # Emits book_id for community upload
    download_clicked = Signal(str)  # Emits community_id for community download
    preview_clicked = Signal(str)  # Emits community_id for community preview
    approve_clicked = Signal(str)  # Emits community_id for admin approve
    reject_clicked = Signal(str)   # Emits community_id for admin reject
    quarantine_clicked = Signal(str)  # Emits community_id for admin quarantine

    def __init__(self, recipe, number=None, parent=None):
        super().__init__(parent)
        self._recipe_id = recipe["id"]
        self._recipe_title = recipe.get("title", "Untitled")
        self._is_book = recipe.get("type") == "book"
        self._is_bom_book = self._is_book and bool(recipe.get("is_book_of_moiety"))
        self._is_article = recipe.get("type") == "article"
        self._is_bom_candidate = bool(recipe.get("bookOfMoietyCandidate"))
        self._is_moiety = bool(recipe.get("is_moiety")) or self._is_bom_candidate
        self._is_paid_book = (
            self._is_book
            and recipe.get("community_origin_id")
            and recipe.get("community_price_type") == "paid"
        )
        self._card_number = number  # For voice command selection
        self._community_id = recipe.get("community_id")  # None for local
        self._community_mode = False
        self._review_mode = False
        self._has_thumbnail = False
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setObjectName("BookCard" if self._is_book else "ArticleCard" if self._is_article else "RecipeCard")
        self.setFixedWidth(CARD_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._title_label = None
        self._producer_label = None
        self._desc_label = None
        self._original_desc = None
        self._meta_label = None
        self._add_to_book_mode = False
        self._in_book = False
        self._book_upload_allowed = True  # Updated when subscription status known
        self._build_ui(recipe)

    def _build_ui(self, recipe):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Image area (fixed 16:9) ---
        self.image_label = QLabel()
        self.image_label.setFixedSize(CARD_WIDTH, IMAGE_HEIGHT)
        self.image_label.setAlignment(Qt.AlignCenter)

        pixmap = self._load_image(recipe.get("main_image_path"))
        if pixmap:
            self.image_label.setPixmap(pixmap)
            self.image_label.setScaledContents(True)
        else:
            if self._is_book:
                fallback_icon = platform_icon("book", weight="regular", point_size=36, color="#4a5a7a")
                if not fallback_icon.isNull():
                    self.image_label.setPixmap(fallback_icon.pixmap(48, 48))
                else:
                    self.image_label.setText("\U0001f4d6")
                self.image_label.setStyleSheet("""
                    background-color: #0f1a30;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    color: #4a5a7a;
                    font-size: 36px;
                """)
            elif self._is_article:
                fallback_icon = platform_icon("square.and.pencil", weight="regular", point_size=36, color="#5a6a4a")
                if not fallback_icon.isNull():
                    self.image_label.setPixmap(fallback_icon.pixmap(48, 48))
                else:
                    self.image_label.setText("\U0001f4dd")
                self.image_label.setStyleSheet("""
                    background-color: #151a0f;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    color: #5a6a4a;
                    font-size: 36px;
                """)
            else:
                self.image_label.setText("\U0001f37d")
                self.image_label.setStyleSheet("""
                    background-color: #0f1e0f;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    color: #4a6a4a;
                    font-size: 36px;
                """)
        layout.addWidget(self.image_label)

        # Type badge on image area (text label matching website badge colors)
        self._type_badge = None
        badge_text = None
        badge_color = None
        if self._is_bom_book:
            badge_text = "Book of Moiety"
            badge_color = "#7c3aed"
        elif self._is_book:
            badge_text = "Book"
            badge_color = "#2563eb"
        elif self._is_article:
            badge_text = "Article"
            badge_color = "#059669"
        elif self._is_moiety:
            badge_text = "Moiety"
            badge_color = "#0891b2"
        else:
            badge_text = "Recipe"
            badge_color = "#d97706"
        if badge_text:
            self._type_badge = QLabel(badge_text, self)
            self._type_badge.setAlignment(Qt.AlignCenter)
            self._type_badge.setStyleSheet(f"""
                QLabel {{
                    background-color: {badge_color};
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 2px 8px;
                }}
            """)
            self._type_badge.adjustSize()
            self._type_badge.move(6, IMAGE_HEIGHT - self._type_badge.height() - 6)
            self._type_badge.raise_()

        # Book of Moiety candidate badge (review mode)
        self._bom_badge = None
        if self._is_bom_candidate:
            self._bom_badge = QLabel("BOM Candidate", self)
            self._bom_badge.setAlignment(Qt.AlignCenter)
            self._bom_badge.setStyleSheet("""
                QLabel {
                    background-color: #7c3aed;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 10px;
                    font-weight: 600;
                    padding: 2px 6px;
                }
            """)
            self._bom_badge.adjustSize()
            type_top = IMAGE_HEIGHT - self._type_badge.height() - 6 if self._type_badge else IMAGE_HEIGHT - 6
            self._bom_badge.move(6, type_top - self._bom_badge.height() - 4)
            self._bom_badge.raise_()

        # Price badge (for paid community books)
        self._price_badge = QLabel(self)
        self._price_badge.setAlignment(Qt.AlignCenter)
        self._price_badge.setStyleSheet("""
            QLabel {
                background-color: rgba(26, 107, 58, 200);
                color: white;
                border: none;
                border-radius: 5px;
                padding: 2px 8px;
                font-size: 12px;
                font-weight: bold;
            }
        """)
        self._price_badge.hide()

        # Set price info from initial data (community items may have it)
        price_type = recipe.get("price_type", "free")
        price_cents = recipe.get("price_cents", 0)
        is_purchased = recipe.get("is_purchased", False)
        if price_type == "paid" and price_cents > 0:
            self.set_price_info("paid", price_cents, is_purchased)

        # --- Text area ---
        text_container = QWidget()
        text_container.setObjectName("BookCardTextArea" if self._is_book else "CardTextArea")
        text_layout = QVBoxLayout(text_container)
        text_layout.setContentsMargins(10, 8, 10, 18)  # Extra bottom padding for number badge
        text_layout.setSpacing(4)

        # Title
        title_label = QLabel(recipe.get("title", "Untitled"))
        title_label.setObjectName("CardTitle")
        title_label.setWordWrap(True)
        text_layout.addWidget(title_label)
        self._title_label = title_label

        # Producer byline (only shown if non-empty)
        producer = (recipe.get("producer") or "").strip()
        if producer:
            producer_label = QLabel(f"by {producer}")
            producer_label.setObjectName("CardProducer")
            text_layout.addWidget(producer_label)
            self._producer_label = producer_label

        # Description (max 2 lines with ellipsis)
        desc = recipe.get("description", "") or ""
        if desc:
            # Strip HTML tags if description contains HTML from RichTextEditor
            if desc.startswith("<!DOCTYPE") or desc.startswith("<html"):
                from PySide6.QtGui import QTextDocument
                doc = QTextDocument()
                doc.setHtml(desc)
                desc = doc.toPlainText().strip()
            if desc:  # Check again after stripping HTML
                desc_label = QLabel()
                desc_label.setObjectName("CardDescription")
                desc_label.setWordWrap(True)
                # Set font explicitly so elision calculation uses correct metrics
                # (stylesheet is applied after construction in load_recipes)
                font = QFont()
                font.setPixelSize(13)  # Match CardDescription stylesheet
                desc_label.setFont(font)
                text_layout.addWidget(desc_label)
                self._desc_label = desc_label
                self._original_desc = desc
                self._elide_description(self._original_desc)

        # Metadata row (cuisine_type removed - not editable in UI, use tags instead)
        meta_parts = []
        if recipe.get("difficulty"):
            meta_parts.append(recipe["difficulty"])
        total_time = (recipe.get("prep_time_min") or 0) + (recipe.get("cook_time_min") or 0)
        if total_time > 0:
            if total_time >= 60:
                hours = total_time // 60
                mins = total_time % 60
                time_str = f"{hours} hr {mins} min" if mins else f"{hours} hr"
            else:
                time_str = f"{total_time} min"
            meta_parts.append(time_str)

        if meta_parts:
            meta_label = QLabel(" \u00b7 ".join(meta_parts))
            meta_label.setObjectName("CardMeta")
            text_layout.addWidget(meta_label)
            self._meta_label = meta_label

        layout.addWidget(text_container)

        # Overlay button style
        overlay_btn_style = """
            QPushButton {
                background-color: rgba(0, 0, 0, 160);
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
        """

        # Copy-to-clipboard button overlaid on image top-right
        self._copy_btn = QPushButton(self)
        copy_icon = _get_card_icon("pencil.and.list.clipboard", weight="regular", point_size=48, color="white", windows_name="Copy")
        if not copy_icon.isNull():
            self._copy_btn.setIcon(copy_icon)
            self._copy_btn.setIconSize(QSize(20, 20))
        else:
            self._copy_btn.setText("\U0001f4cb")
        self._copy_btn.setFixedSize(28, 28)
        self._copy_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._copy_btn.setToolTip("Copy all steps to clipboard")
        self._copy_btn.setStyleSheet(overlay_btn_style)
        self._copy_btn.move(_BTN_X[3], 6)
        self._copy_btn.raise_()
        self._copy_btn.clicked.connect(self._on_copy_clicked)
        if self._is_book or self._is_article:
            self._copy_btn.hide()

        # Export button overlaid on image top-center
        self._export_btn = QPushButton(self)
        export_icon = _get_card_icon("square.and.arrow.up", weight="regular", point_size=48, color="white")
        if not export_icon.isNull():
            self._export_btn.setIcon(export_icon)
            self._export_btn.setIconSize(QSize(20, 20))
        else:
            self._export_btn.setText("\u2B06")
        self._export_btn.setFixedSize(28, 28)
        self._export_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._export_btn.setToolTip(
            "Export book" if self._is_book else "Export article" if self._is_article else "Export moiety" if self._is_moiety else "Export recipe"
        )
        self._export_btn.setStyleSheet(overlay_btn_style)
        self._export_btn.move(_BTN_X[1], 6)
        self._export_btn.raise_()
        self._export_btn.clicked.connect(self._on_export_clicked)
        if self._is_paid_book:
            self._export_btn.hide()

        # Upload-to-community button overlaid between export and copy
        self._upload_btn = QPushButton(self)
        upload_icon = _get_card_icon(
            "icloud.and.arrow.up", weight="regular", point_size=48,
            color="white", windows_name="Upload",
        )
        if not upload_icon.isNull():
            self._upload_btn.setIcon(upload_icon)
            self._upload_btn.setIconSize(QSize(20, 20))
        else:
            self._upload_btn.setText("\u2B06")
        self._upload_btn.setFixedSize(28, 28)
        self._upload_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._upload_btn.setToolTip("Share to community")
        self._upload_btn.setStyleSheet(overlay_btn_style)
        self._upload_btn.move(_BTN_X[2], 6)
        self._upload_btn.raise_()
        self._upload_btn.clicked.connect(self._on_upload_clicked)

        # Delete button overlaid on image top-left (away from copy button)
        self._delete_btn = QPushButton(self)
        delete_icon = _get_card_icon("delete.left", weight="regular", point_size=48, color="white")
        if not delete_icon.isNull():
            self._delete_btn.setIcon(delete_icon)
            self._delete_btn.setIconSize(QSize(20, 20))
        else:
            self._delete_btn.setText("\U0001f5d1")
        self._delete_btn.setFixedSize(28, 28)
        self._delete_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._delete_btn.setToolTip(
            "Delete book" if self._is_book else "Delete article" if self._is_article else "Delete moiety" if self._is_moiety else "Delete recipe"
        )
        self._delete_btn.setStyleSheet(overlay_btn_style)
        self._delete_btn.move(_BTN_X[0], 6)
        self._delete_btn.raise_()
        self._delete_btn.clicked.connect(self._on_delete_clicked)

        # Add-to-book button overlaid on image top-right (hidden by default)
        self._add_btn = QPushButton(self)
        add_icon = _get_card_icon("plus.circle", weight="regular", point_size=48, color="white")
        if not add_icon.isNull():
            self._add_btn.setIcon(add_icon)
            self._add_btn.setIconSize(QSize(20, 20))
        else:
            self._add_btn.setText("+")
        self._add_btn.setFixedSize(28, 28)
        self._add_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._add_btn.setToolTip("Add to book")
        self._add_btn.setStyleSheet(overlay_btn_style)
        self._add_btn.move(CARD_WIDTH - 34, 6)
        self._add_btn.raise_()
        self._add_btn.clicked.connect(self._on_add_to_book_clicked)
        self._add_btn.hide()

        # Download button overlaid on image top-right (community mode, hidden)
        self._download_btn = QPushButton(self)
        dl_icon = _get_card_icon(
            "arrow.down.circle", weight="regular", point_size=48,
            color="white",
        )
        if not dl_icon.isNull():
            self._download_btn.setIcon(dl_icon)
            self._download_btn.setIconSize(QSize(20, 20))
        else:
            self._download_btn.setText("\u2B07")
        self._download_btn.setFixedSize(28, 28)
        self._download_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._download_btn.setToolTip("Download to My Recipes")
        self._download_btn.setStyleSheet(overlay_btn_style)
        self._download_btn.move(CARD_WIDTH - 34, 6)
        self._download_btn.raise_()
        self._download_btn.clicked.connect(self._on_download_clicked)
        self._download_btn.hide()

        # Review buttons (admin mode, hidden by default)
        approve_style = """
            QPushButton {
                background-color: rgba(30, 80, 30, 200);
                color: white; border: none; border-radius: 4px;
                font-size: 14px; padding: 0px;
            }
            QPushButton:hover { background-color: rgba(50, 120, 50, 220); }
        """
        reject_style = """
            QPushButton {
                background-color: rgba(120, 70, 20, 200);
                color: white; border: none; border-radius: 4px;
                font-size: 14px; padding: 0px;
            }
            QPushButton:hover { background-color: rgba(160, 90, 30, 220); }
        """
        quarantine_style = """
            QPushButton {
                background-color: rgba(120, 30, 30, 200);
                color: white; border: none; border-radius: 4px;
                font-size: 14px; padding: 0px;
            }
            QPushButton:hover { background-color: rgba(160, 40, 40, 220); }
        """

        self._approve_btn = QPushButton(self)
        appr_icon = _get_card_icon("checkmark.circle", weight="regular", point_size=48, color="white")
        if not appr_icon.isNull():
            self._approve_btn.setIcon(appr_icon)
            self._approve_btn.setIconSize(QSize(20, 20))
        else:
            self._approve_btn.setText("\u2713")
        self._approve_btn.setFixedSize(28, 28)
        self._approve_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._approve_btn.setToolTip("Approve")
        self._approve_btn.setStyleSheet(approve_style)
        self._approve_btn.move(CARD_WIDTH - 34, 6)
        self._approve_btn.raise_()
        self._approve_btn.clicked.connect(self._on_approve_clicked)
        self._approve_btn.hide()

        self._reject_btn = QPushButton(self)
        rej_icon = _get_card_icon("xmark.circle", weight="regular", point_size=48, color="white")
        if not rej_icon.isNull():
            self._reject_btn.setIcon(rej_icon)
            self._reject_btn.setIconSize(QSize(20, 20))
        else:
            self._reject_btn.setText("\u2717")
        self._reject_btn.setFixedSize(28, 28)
        self._reject_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._reject_btn.setToolTip("Reject && Delete")
        self._reject_btn.setStyleSheet(reject_style)
        self._reject_btn.move(CARD_WIDTH - 66, 6)
        self._reject_btn.raise_()
        self._reject_btn.clicked.connect(self._on_reject_clicked)
        self._reject_btn.hide()

        self._quarantine_btn = QPushButton(self)
        quar_icon = _get_card_icon("exclamationmark.shield", weight="regular", point_size=48, color="white")
        if not quar_icon.isNull():
            self._quarantine_btn.setIcon(quar_icon)
            self._quarantine_btn.setIconSize(QSize(20, 20))
        else:
            self._quarantine_btn.setText("\u26a0")
        self._quarantine_btn.setFixedSize(28, 28)
        self._quarantine_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._quarantine_btn.setToolTip("Quarantine")
        self._quarantine_btn.setStyleSheet(quarantine_style)
        self._quarantine_btn.move(CARD_WIDTH - 98, 6)
        self._quarantine_btn.raise_()
        self._quarantine_btn.clicked.connect(self._on_quarantine_clicked)
        self._quarantine_btn.hide()

        # Checkmark badge overlaid on image top-right (hidden by default)
        self._check_badge = QLabel("\u2713", self)
        self._check_badge.setAlignment(Qt.AlignCenter)
        self._check_badge.setFixedSize(36, 36)
        self._check_badge.setStyleSheet("""
            QLabel {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 18px;
                font-size: 20px;
                font-weight: bold;
            }
        """)
        self._check_badge.move(CARD_WIDTH - 42, 4)
        self._check_badge.raise_()
        self._check_badge.hide()

        # Number badge overlaid on card bottom-right (for voice selection)
        self._number_badge = None
        if self._card_number is not None:
            self._number_badge = QLabel(str(self._card_number), self)
            self._number_badge.setAlignment(Qt.AlignCenter)
            self._number_badge.setFixedSize(28, 28)
            self._number_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(0, 0, 0, 180);
                    color: white;
                    border: none;
                    border-radius: 14px;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
            self._number_badge.raise_()
            # Position will be set in resizeEvent when card is laid out

    def _elide_description(self, text):
        """Truncate description to fit in 2 lines with ellipsis if needed."""
        if not self._desc_label or not text:
            return

        # Known constant: card is fixed-width, text margins are 10px each side
        text_width = CARD_WIDTH - 20

        fm = QFontMetrics(self._desc_label.font())
        max_height = fm.lineSpacing() * 2 + 4

        def fits(s):
            """Check if text fits in 2 lines using pure font metrics."""
            rect = fm.boundingRect(0, 0, text_width, 0, Qt.TextWordWrap, s)
            return rect.height() <= max_height

        # Check if full text fits
        if fits(text):
            self._desc_label.setText(text)
            self._desc_label.setMaximumHeight(max_height)
            return

        # Text is too long - binary search to find the right truncation point
        low, high = 0, len(text)
        best = "..."

        while low <= high:
            mid = (low + high) // 2
            test_text = text[:mid].rstrip() + "..."

            if fits(test_text):
                best = test_text
                low = mid + 1
            else:
                high = mid - 1

        self._desc_label.setText(best)
        self._desc_label.setMaximumHeight(max_height)

    def set_add_to_book_mode(self, enabled):
        """Switch card between normal mode and add-to-book mode."""
        self._add_to_book_mode = enabled
        if enabled:
            self._copy_btn.hide()
            self._export_btn.hide()
            self._upload_btn.hide()
            self._delete_btn.hide()
            if not self._in_book:
                self._add_btn.show()
        else:
            if not self._is_book:
                self._copy_btn.show()
            if not self._is_paid_book:
                self._export_btn.show()
            if not self._is_book or self._book_upload_allowed:
                self._upload_btn.show()
            self._delete_btn.show()
            self._add_btn.hide()
            self._check_badge.hide()
            self._in_book = False

    def set_in_book(self, in_book):
        """Show or hide the checkmark badge (add-to-book mode only)."""
        self._in_book = in_book
        if in_book:
            self._add_btn.hide()
            self._check_badge.show()
            self._check_badge.raise_()
        else:
            self._check_badge.hide()
            if self._add_to_book_mode:
                self._add_btn.show()

    def set_book_upload_allowed(self, allowed):
        """Show/hide upload button for book cards based on tier."""
        self._book_upload_allowed = allowed
        if self._is_book and not self._community_mode and not self._review_mode and not self._add_to_book_mode:
            self._upload_btn.setVisible(allowed)

    def _on_copy_clicked(self):
        """Emit copy signal without triggering card click."""
        self.copy_clicked.emit(self._recipe_id)

    def _on_export_clicked(self):
        """Emit export signal without triggering card click."""
        if self._is_book:
            self.book_export_clicked.emit(self._recipe_id)
        else:
            self.export_clicked.emit(self._recipe_id)

    def _on_delete_clicked(self):
        """Emit delete signal without triggering card click."""
        if self._is_book:
            self.book_delete_clicked.emit(self._recipe_id, self._recipe_title)
        else:
            self.delete_clicked.emit(self._recipe_id, self._recipe_title)

    def set_community_mode(self, enabled):
        """Switch card to community mode — download icon only."""
        self._community_mode = enabled
        if enabled:
            self._copy_btn.hide()
            self._export_btn.hide()
            self._upload_btn.hide()
            self._delete_btn.hide()
            self._download_btn.show()
        else:
            if not self._is_book:
                self._copy_btn.show()
            if not self._is_paid_book:
                self._export_btn.show()
            if not self._is_book or self._book_upload_allowed:
                self._upload_btn.show()
            self._delete_btn.show()
            self._download_btn.hide()

    def set_review_mode(self, enabled):
        """Switch card to review mode — approve/reject/quarantine buttons."""
        self._review_mode = enabled
        if enabled:
            self._copy_btn.hide()
            self._export_btn.hide()
            self._upload_btn.hide()
            self._delete_btn.hide()
            self._download_btn.hide()
            self._approve_btn.show()
            self._reject_btn.show()
            self._quarantine_btn.show()
        else:
            self._approve_btn.hide()
            self._reject_btn.hide()
            self._quarantine_btn.hide()

    def set_thumbnail_pixmap(self, pixmap):
        """Set the card image from an externally loaded pixmap."""
        self.image_label.setPixmap(pixmap)
        self.image_label.setScaledContents(True)
        self._has_thumbnail = True

    def set_price_info(self, price_type, price_cents, is_purchased=False):
        """Show or hide the price/purchased badge on the card image."""
        if price_type == "paid" and price_cents > 0:
            if is_purchased:
                self._price_badge.setText("Purchased")
                self._price_badge.setStyleSheet("""
                    QLabel {
                        background-color: rgba(26, 58, 26, 200);
                        color: #4ade80;
                        border: 1px solid #4ade80;
                        border-radius: 5px;
                        padding: 2px 8px;
                        font-size: 11px;
                        font-weight: bold;
                    }
                """)
            else:
                self._price_badge.setText(f"${price_cents / 100:.2f}")
                self._price_badge.setStyleSheet("""
                    QLabel {
                        background-color: rgba(26, 107, 58, 200);
                        color: white;
                        border: none;
                        border-radius: 5px;
                        padding: 2px 8px;
                        font-size: 12px;
                        font-weight: bold;
                    }
                """)
            self._price_badge.adjustSize()
            x = CARD_WIDTH - self._price_badge.width() - 6
            y = IMAGE_HEIGHT - self._price_badge.height() - 6
            self._price_badge.move(x, y)
            self._price_badge.raise_()
            self._price_badge.show()
        else:
            self._price_badge.hide()

    def _on_approve_clicked(self):
        if self._community_id:
            self.approve_clicked.emit(self._community_id)

    def _on_reject_clicked(self):
        if self._community_id:
            self.reject_clicked.emit(self._community_id)

    def _on_quarantine_clicked(self):
        if self._community_id:
            self.quarantine_clicked.emit(self._community_id)

    def _on_upload_clicked(self):
        """Emit upload signal for sharing to community."""
        if self._is_book:
            self.book_upload_clicked.emit(self._recipe_id)
        else:
            self.upload_clicked.emit(self._recipe_id)

    def _on_download_clicked(self):
        """Emit download signal for community mode."""
        if self._community_id:
            self.download_clicked.emit(self._community_id)

    def _on_add_to_book_clicked(self):
        """Emit add-to-book signal without triggering card click."""
        self.add_to_book_clicked.emit(self._recipe_id)

    def sizeHint(self):
        """Compute height manually so word-wrapped labels are accounted for."""
        # Text area margins: 10 left, 8 top, 10 right, 10 bottom; spacing 4
        text_width = CARD_WIDTH - 20  # 10px padding each side
        height = IMAGE_HEIGHT  # Fixed image area

        # Text container top/bottom margins
        height += 8 + 18  # top=8, bottom=18 (extra for number badge)

        def _wrapped_height(label):
            if label is None:
                return 0
            fm = QFontMetrics(label.font())
            rect = fm.boundingRect(0, 0, text_width, 0, Qt.TextWordWrap, label.text())
            return rect.height()

        title_h = _wrapped_height(self._title_label)
        producer_h = _wrapped_height(self._producer_label)
        desc_h = _wrapped_height(self._desc_label)
        meta_h = _wrapped_height(self._meta_label)

        height += title_h
        if producer_h > 0:
            height += 4 + producer_h
        if desc_h > 0:
            height += 4 + desc_h
        if meta_h > 0:
            height += 4 + meta_h

        return QSize(CARD_WIDTH, height)

    def _load_image(self, path):
        """Load image from path, return QPixmap or None.

        Uses a module-level cache so re-displaying the same recipes
        (e.g. after clearing filters) is instant.  On first load, uses
        QImageReader.setScaledSize() so the JPEG decoder only decodes
        at the target resolution.
        """
        if not path:
            return None
        if not os.path.isabs(path):
            project_root = os.path.dirname(os.path.dirname(__file__))
            path = os.path.join(project_root, path)
        if not os.path.isfile(path):
            return None
        if path in _PIXMAP_CACHE:
            return _PIXMAP_CACHE[path]
        reader = QImageReader(path)
        src = reader.size()
        if not src.isValid():
            return None
        # Scale to cover the card area (KeepAspectRatioByExpanding)
        scale = max(CARD_WIDTH / src.width(), IMAGE_HEIGHT / src.height())
        reader.setScaledSize(QSize(
            int(src.width() * scale + 0.5),
            int(src.height() * scale + 0.5),
        ))
        image = reader.read()
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        _PIXMAP_CACHE[path] = pixmap
        return pixmap

    def resizeEvent(self, event):
        """Reposition number badge on resize."""
        super().resizeEvent(event)
        if self._number_badge is not None:
            self._number_badge.move(
                self.width() - 34,
                self.height() - 31,  # 3px lower than default
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            # Don't trigger signals if clicking overlay buttons
            if (self._copy_btn.isVisible() and self._copy_btn.geometry().contains(pos)):
                pass
            elif (self._export_btn.isVisible() and self._export_btn.geometry().contains(pos)):
                pass
            elif (self._upload_btn.isVisible() and self._upload_btn.geometry().contains(pos)):
                pass
            elif (self._delete_btn.isVisible() and self._delete_btn.geometry().contains(pos)):
                pass
            elif (self._add_btn.isVisible() and self._add_btn.geometry().contains(pos)):
                pass
            elif (self._download_btn.isVisible() and self._download_btn.geometry().contains(pos)):
                pass
            elif (self._approve_btn.isVisible() and self._approve_btn.geometry().contains(pos)):
                pass
            elif (self._reject_btn.isVisible() and self._reject_btn.geometry().contains(pos)):
                pass
            elif (self._quarantine_btn.isVisible() and self._quarantine_btn.geometry().contains(pos)):
                pass
            elif self._review_mode:
                # In review mode, clicking the card body opens preview
                if self._community_id:
                    self.preview_clicked.emit(self._community_id)
            elif self._community_mode:
                # In community mode, clicking the card body opens preview
                if self._community_id:
                    self.preview_clicked.emit(self._community_id)
            elif self._add_to_book_mode:
                # In add-to-book mode, clicking the card body adds the recipe
                if not self._in_book:
                    self.add_to_book_clicked.emit(self._recipe_id)
            elif self._is_book:
                self.book_clicked.emit(self._recipe_id)
            else:
                self.clicked.emit(self._recipe_id)
        super().mousePressEvent(event)


# ---------- Recipe List View ----------

class RecipeListView(QWidget):
    """Scrollable flow layout of fixed-size recipe cards with search support."""

    recipe_selected = Signal(int)  # Emits recipe_id
    book_selected = Signal(int)  # Emits book_id
    copy_recipe = Signal(int)  # Emits recipe_id for clipboard copy
    export_recipe = Signal(int)  # Emits recipe_id for export
    export_book = Signal(int)  # Emits book_id for export
    delete_recipe = Signal(int, str)  # Emits (recipe_id, title) for delete confirmation
    delete_book = Signal(int, str)  # Emits (book_id, title) for delete confirmation
    add_to_book = Signal(int)  # Emits recipe_id for add-to-book mode
    filtersChanged = Signal()  # Emitted when tag filters change
    upload_recipe = Signal(int)  # Emits recipe_id for community upload
    upload_book = Signal(int)  # Emits book_id for community upload
    community_download = Signal(str)  # Emits community_id for download
    community_preview = Signal(str)  # Emits community_id for preview
    community_load_next_page = Signal()  # Triggers next page fetch
    review_approve = Signal(str)     # Emits community_id for admin approve
    review_reject = Signal(str)      # Emits community_id for admin reject
    review_quarantine = Signal(str)  # Emits community_id for admin quarantine
    review_preview = Signal(str)     # Emits community_id for review detail
    review_load_next_page = Signal() # Triggers next review page fetch

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #1a1a1a;")

        self._cards = []
        self._last_query = ""
        self._active_tags: list[str] = []
        self._active_producers: list[str] = []
        self._total_recipes = 0
        self._add_to_book_mode = False
        self._add_to_bom_book = False
        self._book_recipe_ids = set()
        self._community_mode = False
        self._community_cursor = None
        self._community_loading = False
        self._community_items: list[dict] = []
        self._review_mode = False
        self._review_cursor = None
        self._review_loading = False
        self._review_items: list[dict] = []
        self._book_upload_allowed = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Container for command bar (will be populated by MainWindow)
        self.command_bar_container = QWidget()
        self.command_bar_container.setObjectName("CommandBarContainer")
        self._command_bar_layout = QVBoxLayout(self.command_bar_container)
        self._command_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._command_bar_layout.setSpacing(0)
        layout.addWidget(self.command_bar_container)

        # Mode banner (hidden by default, shown in community/review modes)
        self._mode_banner = QLabel()
        self._mode_banner.setAlignment(Qt.AlignCenter)
        self._mode_banner.setFixedHeight(28)
        self._mode_banner.hide()
        layout.addWidget(self._mode_banner)

        # Tag panel (overlay — NOT in layout, positioned manually)
        self.tag_side_panel = TagSidePanel(self)
        self.tag_side_panel.selectionChanged.connect(self._on_tag_selection_changed)
        self.tag_side_panel.clearAll.connect(self._on_clear_all_filters)

        # Scroll area for recipe cards
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setObjectName("RecipeScrollArea")
        self.scroll_area.setStyleSheet(self._scroll_style())

        # Flow container
        self.flow_container = QWidget()
        self.flow_container.setObjectName("FlowContainer")
        self.flow_layout = FlowLayout(self.flow_container)
        self.flow_container.setStyleSheet(self._card_style())

        self.scroll_area.setWidget(self.flow_container)
        layout.addWidget(self.scroll_area, stretch=1)

        # Empty state label (hidden by default)
        self.empty_label = QLabel("No recipes yet")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setFixedHeight(200)
        self.empty_label.setStyleSheet("""
            color: #888888;
            font-size: 18px;
            background-color: transparent;
        """)
        self.empty_label.hide()

        # Cache total recipe count
        self._total_recipes = get_total_recipe_count()

    def load_recipes(self, recipes):
        """Populate the flow layout with recipe cards."""
        self._clear_cards()

        if not recipes:
            if self._last_query or self._active_tags:
                self.empty_label.setText("No recipes match your filters")
            else:
                self.empty_label.setText("No recipes yet")
            self.empty_label.show()
            self.flow_layout.addWidget(self.empty_label)
        else:
            self.empty_label.hide()
            for idx, recipe in enumerate(recipes, start=1):
                card = RecipeCard(recipe, number=idx)
                card.clicked.connect(self.recipe_selected.emit)
                card.book_clicked.connect(self.book_selected.emit)
                card.copy_clicked.connect(self.copy_recipe.emit)
                card.export_clicked.connect(self.export_recipe.emit)
                card.upload_clicked.connect(self.upload_recipe.emit)
                card.book_upload_clicked.connect(self.upload_book.emit)
                card.book_export_clicked.connect(self.export_book.emit)
                card.delete_clicked.connect(self.delete_recipe.emit)
                card.book_delete_clicked.connect(self.delete_book.emit)
                if self._add_to_book_mode:
                    card.set_add_to_book_mode(True)
                    card.add_to_book_clicked.connect(self.add_to_book.emit)
                    if card._recipe_id in self._book_recipe_ids:
                        card.set_in_book(True)
                if not self._book_upload_allowed:
                    card.set_book_upload_allowed(False)
                self.flow_layout.addWidget(card)
                self._cards.append(card)

        # Update side panel count
        self.tag_side_panel.update_count(
            len(recipes) if recipes else 0,
            self._total_recipes,
        )

    def filter_recipes(self, query: str = None):
        """Query the database with text and tag filters, then reload cards.

        Args:
            query: Text search query. If None, uses the last query.
        """
        if self._community_mode:
            return  # Community mode fetches from API, not local DB
        if query is not None:
            self._last_query = query
        if self._add_to_book_mode:
            # In add-to-book mode, only show recipes and moieties (not books or articles)
            # For Book of Moiety books, only show approved BOM candidates
            results = search_recipes_with_tags(
                self._last_query, self._active_tags, self._active_producers,
                bom_candidates_only=self._add_to_bom_book,
            )
            results = [r for r in results if r.get("content_type") != "article"]
        else:
            # Normal mode: show recipes + books mixed together
            results = search_all_with_tags(
                self._last_query, self._active_tags, self._active_producers,
            )
        self.load_recipes(results)

    # ------------------------------------------------------------------
    # Add-to-book mode
    # ------------------------------------------------------------------

    def enter_add_to_book_mode(self, recipe_ids_in_book, bom_book=False):
        """Switch to add-to-book mode with checkmarks for existing book recipes."""
        self._add_to_book_mode = True
        self._add_to_bom_book = bom_book
        self._book_recipe_ids = set(recipe_ids_in_book)
        for card in self._cards:
            card.set_add_to_book_mode(True)
            card.add_to_book_clicked.connect(self.add_to_book.emit)
            if card._recipe_id in self._book_recipe_ids:
                card.set_in_book(True)

    def exit_add_to_book_mode(self):
        """Return to normal mode, restoring all card overlays."""
        self._add_to_book_mode = False
        self._add_to_bom_book = False
        self._book_recipe_ids.clear()
        for card in self._cards:
            card.set_add_to_book_mode(False)

    def mark_recipe_in_book(self, recipe_id):
        """Mark a recipe as added to the book (shows checkmark)."""
        self._book_recipe_ids.add(recipe_id)
        for card in self._cards:
            if card._recipe_id == recipe_id:
                card.set_in_book(True)
                break

    # ------------------------------------------------------------------
    # Community mode
    # ------------------------------------------------------------------

    def enter_community_mode(self):
        """Switch to community browse mode."""
        self._community_mode = True
        self._community_cursor = None
        self._community_loading = False
        self._community_items.clear()
        self._clear_cards()
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_community_scroll
        )

    def exit_community_mode(self):
        """Return to local recipe mode."""
        self._community_mode = False
        self._community_cursor = None
        self._community_loading = False
        self._community_items.clear()
        try:
            self.scroll_area.verticalScrollBar().valueChanged.disconnect(
                self._on_community_scroll
            )
        except RuntimeError:
            pass
        self.hide_loading()
        self._clear_cards()

    def set_book_upload_allowed(self, allowed):
        """Update whether book cards show the upload button (tier-dependent)."""
        self._book_upload_allowed = allowed
        for card in self._cards:
            card.set_book_upload_allowed(allowed)

    def load_community_cards(self, items, append=False):
        """Create community-mode cards from API data.

        Args:
            items: List of normalized item dicts from the API.
            append: If True, add to existing cards (pagination).
        """
        if not append:
            self._clear_cards()
            self._community_items.clear()
        self._community_items.extend(items)

        if not self._community_items:
            self.empty_label.setText("No community recipes found")
            self.empty_label.show()
            self.flow_layout.addWidget(self.empty_label)
            return

        self.empty_label.hide()

        for item in items:
            card = RecipeCard(item)
            card.set_community_mode(True)
            card.download_clicked.connect(self.community_download.emit)
            card.preview_clicked.connect(self.community_preview.emit)
            self.flow_layout.addWidget(card)
            self._cards.append(card)

    def show_loading(self):
        """Show a loading indicator at the bottom of the card grid."""
        if not hasattr(self, "_loading_label") or self._loading_label is None:
            self._loading_label = QLabel("Loading...")
            self._loading_label.setAlignment(Qt.AlignCenter)
            self._loading_label.setFixedHeight(60)
            self._loading_label.setStyleSheet(
                "color: #888888; font-size: 16px; background-color: transparent;"
            )
        self.flow_layout.addWidget(self._loading_label)
        self._loading_label.show()

    def hide_loading(self):
        """Remove the loading indicator."""
        if hasattr(self, "_loading_label") and self._loading_label is not None:
            self._loading_label.hide()
            self._loading_label.setParent(None)

    def get_community_item(self, community_id: str) -> dict | None:
        """Look up a community item by its community_id."""
        for item in self._community_items:
            if item.get("community_id") == community_id:
                return item
        return None

    def _on_community_scroll(self, value):
        """Trigger next page fetch when near bottom of scroll area."""
        if not self._community_mode or self._community_loading:
            return
        sb = self.scroll_area.verticalScrollBar()
        if sb.maximum() - value < 200 and self._community_cursor is not None:
            self.community_load_next_page.emit()

    # ------------------------------------------------------------------
    # Review mode (admin)
    # ------------------------------------------------------------------

    def enter_review_mode(self):
        """Switch to admin review mode."""
        self._review_mode = True
        self._review_cursor = None
        self._review_loading = False
        self._review_items.clear()
        self._clear_cards()
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_review_scroll
        )

    def exit_review_mode(self):
        """Return from admin review mode."""
        self._review_mode = False
        self._review_cursor = None
        self._review_loading = False
        self._review_items.clear()
        try:
            self.scroll_area.verticalScrollBar().valueChanged.disconnect(
                self._on_review_scroll
            )
        except RuntimeError:
            pass
        self.hide_loading()
        self._clear_cards()

    def load_review_cards(self, items, append=False):
        """Create review-mode cards from admin pending data."""
        if not append:
            self._clear_cards()
            self._review_items.clear()
        self._review_items.extend(items)

        if not self._review_items:
            self.empty_label.setText("No pending uploads")
            self.empty_label.show()
            self.flow_layout.addWidget(self.empty_label)
            return

        self.empty_label.hide()
        for item in items:
            card = RecipeCard(item)
            card.set_review_mode(True)
            card.approve_clicked.connect(self.review_approve.emit)
            card.reject_clicked.connect(self.review_reject.emit)
            card.quarantine_clicked.connect(self.review_quarantine.emit)
            card.preview_clicked.connect(self.review_preview.emit)
            self.flow_layout.addWidget(card)
            self._cards.append(card)

    def remove_review_card(self, community_id: str):
        """Remove a card after review action."""
        for card in self._cards:
            if getattr(card, "_community_id", None) == community_id:
                self._cards.remove(card)
                self.flow_layout.removeWidget(card)
                card.deleteLater()
                break
        self._review_items = [
            i for i in self._review_items
            if i.get("community_id") != community_id
        ]
        if not self._cards:
            self.empty_label.setText("No pending uploads")
            self.empty_label.show()
            self.flow_layout.addWidget(self.empty_label)

    def get_review_item(self, community_id: str) -> dict | None:
        """Look up a review item by community_id."""
        for item in self._review_items:
            if item.get("community_id") == community_id:
                return item
        return None

    def _on_review_scroll(self, value):
        """Trigger next review page fetch when near bottom."""
        if not self._review_mode or self._review_loading:
            return
        sb = self.scroll_area.verticalScrollBar()
        if sb.maximum() - value < 200 and self._review_cursor is not None:
            self.review_load_next_page.emit()

    def set_command_bar(self, command_bar):
        """Embed a command bar widget at the top of this view.

        The command bar is reparented to this view's layout.
        """
        # Clear any existing widget in the container
        while self._command_bar_layout.count():
            item = self._command_bar_layout.takeAt(0)
            # Don't delete - just remove from layout
            if item.widget():
                item.widget().setParent(None)
        # Add the command bar to our container
        if command_bar:
            self._command_bar_layout.addWidget(command_bar)
            command_bar.show()

    def show_mode_banner(self, text, bg_color, text_color="#ffffff"):
        """Show a colored mode banner below the command bar."""
        self._mode_banner.setText(text)
        self._mode_banner.setStyleSheet(
            f"background-color: {bg_color}; color: {text_color}; "
            f"font-size: 12px; font-weight: bold; letter-spacing: 1px;"
        )
        self._mode_banner.show()

    def hide_mode_banner(self):
        """Hide the mode banner."""
        self._mode_banner.hide()

    def refresh_total_count(self):
        """Refresh the total recipe count from the database."""
        self._total_recipes = get_total_recipe_count()

    # ------------------------------------------------------------------
    # Tag Filter Methods
    # ------------------------------------------------------------------

    def toggle_tag_side_panel(self):
        """Toggle the tag side panel visibility."""
        if self.tag_side_panel.isVisible():
            self.tag_side_panel.hide()
        else:
            self._show_tag_panel()

    def show_tag_side_panel(self):
        """Show the tag side panel."""
        self._show_tag_panel()

    def _show_tag_panel(self):
        """Internal: refresh, position, and show the tag overlay."""
        self.tag_side_panel.set_selections(self._active_tags, self._active_producers)
        self.tag_side_panel.update_count(len(self._cards), self._total_recipes)
        self._position_tag_panel()
        self.tag_side_panel.show()
        self.tag_side_panel.raise_()

    def hide_tag_side_panel(self):
        """Hide the tag side panel."""
        self.tag_side_panel.hide()

    def is_tag_panel_visible(self) -> bool:
        """Return True if the tag side panel is visible."""
        return self.tag_side_panel.isVisible()

    def get_active_tags(self) -> list[str]:
        """Return list of currently active tag filters."""
        return list(self._active_tags)

    def set_active_tags(self, tags: list[str]):
        """Set the active tag filters and refresh the view."""
        self._active_tags = list(tags)
        self.tag_side_panel.set_selected_tags(self._active_tags)
        self.filter_recipes()
        self.filtersChanged.emit()

    def add_tag_filter(self, tag: str):
        """Add a tag to the active filters."""
        if tag not in self._active_tags:
            self._active_tags.append(tag)
            self.tag_side_panel.set_selected_tags(self._active_tags)
            self.filter_recipes()
            self.filtersChanged.emit()

    def remove_tag_filter(self, tag: str):
        """Remove a tag from the active filters."""
        if tag in self._active_tags:
            self._active_tags.remove(tag)
            self.tag_side_panel.set_selected_tags(self._active_tags)
            self.filter_recipes()
            self.filtersChanged.emit()

    def clear_all_filters(self):
        """Clear all filters (text, tags, and producers)."""
        self._active_tags.clear()
        self._active_producers.clear()
        self._last_query = ""
        self.tag_side_panel.set_selections([], [])
        self.filter_recipes()
        self.filtersChanged.emit()

    def has_active_filters(self) -> bool:
        """Return True if any filters are active."""
        return bool(self._last_query.strip() or self._active_tags
                     or self._active_producers)

    def get_recipe_id_by_number(self, number: int) -> int | None:
        """Get the recipe_id for a card by its display number.

        Used by voice commands to open a recipe by saying "open number 2".

        Args:
            number: The 1-based display number shown on the card badge.

        Returns:
            The recipe_id if the number is valid, None otherwise.
        """
        if 1 <= number <= len(self._cards):
            return self._cards[number - 1]._recipe_id
        return None

    def get_card_count(self) -> int:
        """Return the number of currently displayed recipe cards."""
        return len(self._cards)

    def get_visible_recipes(self) -> list[tuple[int, str, str]]:
        """Return (id, title, type) for each visible card in display order.

        *type* is ``"book"`` or ``"recipe"``.
        """
        return [
            (card._recipe_id, card._recipe_title, "book" if card._is_book else "recipe")
            for card in self._cards
        ]

    def refresh_description_elision(self):
        """Re-calculate description elision for all cards.

        Called when returning to recipe list view to ensure 2-line
        truncation is applied correctly after view transitions.
        """
        for card in self._cards:
            if card._desc_label and card._original_desc:
                card._elide_description(card._original_desc)

    def resizeEvent(self, event):
        """Reposition the tag panel overlay on resize."""
        super().resizeEvent(event)
        self._position_tag_panel()

    def _position_tag_panel(self):
        """Place the tag panel overlay at the bottom of the view, full width."""
        h = self.tag_side_panel.panel_height()
        y = self.height() - h
        self.tag_side_panel.setGeometry(0, y, self.width(), h)
        self.tag_side_panel.raise_()

    def _on_tag_selection_changed(self):
        """Handle tag or producer toggle — filter immediately."""
        self._active_tags = self.tag_side_panel.get_selected_tags()
        self._active_producers = self.tag_side_panel.get_selected_producers()
        self._position_tag_panel()  # Height may have changed
        self.filter_recipes()
        self.filtersChanged.emit()

    def _on_clear_all_filters(self):
        """Handle Clear All button in side panel."""
        self.clear_all_filters()

    def _clear_cards(self):
        """Remove all recipe cards from the flow layout.

        Clears card widgets in-place rather than replacing the entire
        flow container.  Replacing via takeWidget()/setWidget() triggers
        a deep layout recalculation that propagates through the
        QVBoxLayout → QStackedLayout chain and can cause macOS Cocoa
        to exit native full screen.
        """
        self.empty_label.hide()
        self._cards.clear()
        # Remove empty label from flow layout if present
        for i in range(self.flow_layout.count()):
            item = self.flow_layout.itemAt(i)
            if item and item.widget() is self.empty_label:
                self.flow_layout.takeAt(i)
                self.empty_label.setParent(None)
                break
        # Remove and delete all remaining widgets (cards)
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.deleteLater()

    def _card_style(self):
        return """
            QFrame#RecipeCard {
                background-color: #1a2e1a;
                border: 1px solid #2a4a2a;
                border-radius: 8px;
            }
            QFrame#RecipeCard:hover {
                background-color: #1f3620;
            }
            QFrame#BookCard {
                background-color: #1a2540;
                border: 1px solid #2a3a5a;
                border-radius: 8px;
            }
            QFrame#BookCard:hover {
                background-color: #1f2d4d;
            }
            QWidget#CardTextArea {
                background-color: #1a2e1a;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            QWidget#BookCardTextArea {
                background-color: #1a2540;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            QLabel#CardTitle {
                color: white;
                font-size: 14px;
                font-weight: bold;
                background-color: transparent;
            }
            QLabel#CardDescription {
                color: #e0e0e0;
                font-size: 13px;
                background-color: transparent;
            }
            QLabel#CardProducer {
                color: #999999;
                font-size: 11px;
                font-style: italic;
                background-color: transparent;
            }
            QLabel#CardMeta {
                color: #888888;
                font-size: 11px;
                background-color: transparent;
            }
            QToolTip {
                color: white;
                background-color: #333333;
                border: 1px solid #555555;
                padding: 4px;
            }
        """

    def _scroll_style(self):
        return """
            QScrollArea#RecipeScrollArea {
                border: none;
                background-color: transparent;
            }
            QWidget#FlowContainer {
                background-color: #1a1a1a;
            }
            QScrollBar:vertical {
                width: 6px;
                background-color: #1a1a1a;
                border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #555555;
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #777777;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """

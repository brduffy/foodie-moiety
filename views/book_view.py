"""Book view - full-screen cover image with frosted glass overlay."""

import os

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from widgets.book_toc_widget import BookTocWidget
from widgets.rich_text_editor import RichTextEditor
from widgets.tags_editor import TagsEditor


# ------------------------------------------------------------------
# Frosted glass overlay widget (duplicated from recipe_detail.py)
# ------------------------------------------------------------------

class FrostedOverlay(QWidget):
    """Widget that paints a cropped region of a blurred pixmap as background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._blurred_pixmap = None
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_blurred_pixmap(self, pixmap):
        self._blurred_pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)

        if self._blurred_pixmap and not self._blurred_pixmap.isNull():
            # Draw the blurred pixmap scaled to fill this overlay
            painter.drawPixmap(self.rect(), self._blurred_pixmap,
                               self._blurred_pixmap.rect())
        else:
            painter.fillRect(self.rect(), QColor(26, 26, 26))

        # Slight dark tint for extra text contrast
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        # Right edge border
        painter.setPen(QColor(255, 255, 255, 40))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

        painter.end()


# ------------------------------------------------------------------
# Book view
# ------------------------------------------------------------------

class BookView(QWidget):
    """Book view with full-screen cover image and frosted glass left-pane TOC."""

    recipe_clicked = Signal(int)  # Forwarded from BookTocWidget

    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_pixmap = None
        self._cover_pixmap = None
        self._blurred_source = None
        self._top_inset = 0
        self._bottom_inset = 0
        self._overlay_anim = None
        self._book_data = None
        self._layout_mode = "both"

        # --- Background image layer ---
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setScaledContents(True)
        self.image_label.setStyleSheet("background-color: #1a1a1a;")

        # --- Frosted overlay (left half) ---
        self.overlay = FrostedOverlay(self)

        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(20, 20, 20, 20)
        overlay_layout.setSpacing(12)

        # --- Book title (editable QTextEdit for word wrap, read-only by default) ---
        self._title_edit = QTextEdit()
        self._title_edit.setPlaceholderText("Book Title")
        self._title_edit.setAcceptRichText(False)
        self._title_edit.setReadOnly(True)
        self._title_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._title_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._TITLE_SS_VIEW = """
            QTextEdit {
                background-color: transparent;
                color: #ffffff;
                font-size: 16px;
                font-weight: 600;
                border: none;
                padding: 0px;
            }
        """
        self._TITLE_SS_EDIT = """
            QTextEdit {
                background-color: rgba(0, 0, 0, 60);
                color: #ffffff;
                font-size: 16px;
                font-weight: 600;
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 4px;
                padding: 4px 8px;
            }
            QTextEdit:focus {
                background-color: rgba(0, 0, 0, 110);
                border: 1px solid rgba(255, 255, 255, 35);
            }
        """
        self._title_edit.setStyleSheet(self._TITLE_SS_VIEW)
        # Auto-resize height to fit content (1-2 lines)
        def _resize_title():
            h = int(self._title_edit.document().size().height()) + \
                self._title_edit.frameWidth() * 2
            self._title_edit.setFixedHeight(max(24, min(h, 60)))
        self._title_edit.document().documentLayout().documentSizeChanged.connect(
            lambda size: _resize_title()
        )
        self._title_edit.setFixedHeight(28)
        overlay_layout.addWidget(self._title_edit)

        # --- Table of Contents (top section) ---
        self.toc_widget = BookTocWidget()
        self.toc_widget.recipe_clicked.connect(self.recipe_clicked)
        self.toc_widget.recipe_hovered.connect(self._on_recipe_hovered)
        overlay_layout.addWidget(self.toc_widget, stretch=2)

        # --- Book description (bottom section) ---
        self.description_editor = RichTextEditor(
            title="Description", placeholder="Book description..."
        )
        self.description_editor.set_read_only(True)
        self.description_editor.btn_link_step.hide()
        self.description_editor.btn_link_web.hide()
        overlay_layout.addWidget(self.description_editor, stretch=1)

        # --- Tags editor (hidden by default, shown in "tags" layout mode) ---
        self.tags_editor = TagsEditor()
        self.tags_editor.hide()
        overlay_layout.addWidget(self.tags_editor, stretch=1)

        # --- Details section (hidden by default, shown in "details" layout mode) ---
        self._details_container = QWidget()
        self._details_container.setAttribute(Qt.WA_TranslucentBackground)
        self._details_container.setStyleSheet("""
            QLabel.DetailsFieldLabel {
                color: #999999;
                font-size: 15px;
                background: transparent;
            }
            QLineEdit {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 15px;
            }
            QLineEdit:focus { border-color: #0078d4; }
            QLineEdit:read-only {
                background-color: transparent;
                border: none;
                padding: 0px;
                color: #ffffff;
                font-size: 15px;
            }
        """)
        details_layout = QVBoxLayout(self._details_container)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(10)

        def _details_row(label_text, widget, expand=False):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setProperty("class", "DetailsFieldLabel")
            lbl.setFixedWidth(85)
            row.addWidget(lbl)
            row.addWidget(widget, stretch=1 if expand else 0)
            if not expand:
                row.addStretch()
            details_layout.addLayout(row)

        details_layout.addStretch()
        self._details_container.hide()
        overlay_layout.addWidget(self._details_container, stretch=1)

        # Ensure overlay is above image
        self.overlay.raise_()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load_book(self, book_data):
        """Load a BookData instance for display."""
        self._book_data = book_data
        self._title_edit.setPlainText(book_data.title)
        self._load_cover_image(book_data.cover_image_path)
        self.toc_widget.load_toc(book_data.categories)
        if book_data.description:
            self.description_editor.set_html(book_data.description)
        else:
            self.description_editor.set_html("")
        self.tags_editor.refresh_available_tags()
        self.tags_editor.set_tags(book_data.tags)

    def set_layout_mode(self, mode):
        """Switch overlay layout between display modes.

        Args:
            mode: One of "both", "toc", "description", "tags", "details", "image"
        """
        self._layout_mode = mode
        self.tags_editor.hide()
        self._details_container.hide()

        if mode == "both":
            self.overlay.show()
            self.toc_widget.show()
            self.description_editor.show()
        elif mode == "toc":
            self.overlay.show()
            self.toc_widget.show()
            self.description_editor.hide()
        elif mode == "description":
            self.overlay.show()
            self.toc_widget.hide()
            self.description_editor.show()
        elif mode == "tags":
            self.overlay.show()
            self.toc_widget.hide()
            self.description_editor.hide()
            self.tags_editor.show()
            self.tags_editor.refresh_available_tags()
        elif mode == "details":
            self.overlay.show()
            self.toc_widget.hide()
            self.description_editor.hide()
            self._details_container.show()
        elif mode == "image":
            self.overlay.hide()

        self._update_overlay_blur()

    def _on_recipe_hovered(self, image_path):
        """Swap background to recipe image on hover, revert to cover on leave."""
        if image_path:
            self._set_background_pixmap(self._resolve_pixmap(image_path))
        else:
            self._set_background_pixmap(self._cover_pixmap)

    def _resolve_pixmap(self, image_path):
        """Resolve a path to a QPixmap, or None if missing/invalid."""
        if not image_path:
            return None
        if not os.path.isabs(image_path):
            project_root = os.path.dirname(os.path.dirname(__file__))
            image_path = os.path.join(project_root, image_path)
        if os.path.isfile(image_path):
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                return pixmap
        return None

    def _set_background_pixmap(self, pixmap):
        """Set a pixmap as the background image and rebuild blur."""
        self._original_pixmap = pixmap
        if pixmap and not pixmap.isNull():
            self.image_label.setPixmap(pixmap)
        else:
            self.image_label.clear()
            self.image_label.setStyleSheet("background-color: #1a1a1a;")
        self._build_blur_source()

    def _load_cover_image(self, image_path):
        """Load and display the book cover image as the background."""
        self._cover_pixmap = self._resolve_pixmap(image_path)
        self._set_background_pixmap(self._cover_pixmap)

    # ------------------------------------------------------------------
    # Blur pipeline (same as RecipeDetailView)
    # ------------------------------------------------------------------

    def _build_blur_source(self):
        """Create a small blurred version of the original image (once per image).

        Uses aggressive downscale + smooth upscale instead of pixel-level blur.
        The result is a small pixmap (~80x46) that gets scaled to overlay size
        during paint — fast because Qt handles the scaling in C++.
        """
        if not self._original_pixmap or self._original_pixmap.isNull():
            self._blurred_source = None
            self.overlay.set_blurred_pixmap(None)
            return

        # Multi-pass downscale/upscale to produce a smooth frosted blur.
        # Pass 1: shrink to tiny size (bilinear averaging = blur)
        tiny = self._original_pixmap.scaled(
            40, 23,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        # Pass 2: upscale to medium — SmoothTransformation interpolates
        # the blocky pixels into a smooth gradient
        medium = tiny.scaled(
            200, 115,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        # Pass 3: shrink back down — locks in the smoothed result
        self._blurred_source = medium.scaled(
            80, 46,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        self._update_overlay_blur()

    def _update_overlay_blur(self):
        """Scale the cached blur source to the overlay's current size."""
        if not self._blurred_source or self._blurred_source.isNull():
            self.overlay.set_blurred_pixmap(None)
            return

        ow = self.overlay.width() or 1
        oh = self.overlay.height() or 1

        # Crop the left half of the tiny blurred image before scaling up
        src = self._blurred_source
        half_w = max(src.width() // 2, 1)
        cropped = src.copy(0, 0, half_w, src.height())

        # Scale up to overlay size — Qt does this in C++, very fast
        scaled = cropped.scaled(
            ow, oh,
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        self.overlay.set_blurred_pixmap(scaled)

    # ------------------------------------------------------------------
    # Overlay positioning (same pattern as RecipeDetailView)
    # ------------------------------------------------------------------

    def set_bar_insets(self, top, bottom, animate=True):
        """Set top/bottom insets to avoid overlapping command bar / step nav."""
        self._top_inset = top
        self._bottom_inset = bottom
        if animate:
            self._animate_overlay()
        else:
            self._reposition_overlay()

    def _overlay_target_rect(self):
        """Compute the target geometry for the overlay given current insets."""
        w, h = self.width(), self.height()
        overlay_y = self._top_inset
        overlay_h = h - self._top_inset - self._bottom_inset
        return QRect(0, overlay_y, w // 2, max(overlay_h, 0))

    def _animate_overlay(self):
        """Smoothly animate the overlay to its target geometry."""
        w, h = self.width(), self.height()
        self.image_label.setGeometry(0, 0, w, h)
        target = self._overlay_target_rect()
        if self.overlay.geometry() == target:
            return

        if self._overlay_anim is not None:
            self._overlay_anim.stop()

        self._overlay_anim = QPropertyAnimation(self.overlay, b"geometry")
        self._overlay_anim.setDuration(300)
        self._overlay_anim.setStartValue(self.overlay.geometry())
        self._overlay_anim.setEndValue(target)
        self._overlay_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._overlay_anim.finished.connect(self._update_overlay_blur)
        self._overlay_anim.start()

    def _reposition_overlay(self):
        """Position the overlay immediately (no animation)."""
        w, h = self.width(), self.height()
        self.image_label.setGeometry(0, 0, w, h)
        self.overlay.setGeometry(self._overlay_target_rect())
        self._update_overlay_blur()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay_anim is not None:
            self._overlay_anim.stop()
            self._overlay_anim = None
        self._reposition_overlay()

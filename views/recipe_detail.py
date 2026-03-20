"""Recipe detail view - full-screen step image with frosted glass overlay."""

import os
from fractions import Fraction

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models.recipe_data import IngredientData, RecipeData
from utils.helpers import DIALOG_STYLE
from widgets.ingredient_list_editor import IngredientListEditor
from widgets.recipe_tips_widget import RecipeTipsWidget
from widgets.rich_text_editor import RichTextEditor
from widgets.tags_editor import TagsEditor


# ------------------------------------------------------------------
# Frosted glass overlay widget
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
# Recipe detail view
# ------------------------------------------------------------------

class RecipeDetailView(QWidget):
    """
    Recipe detail view with a full-screen step image background and a
    frosted-glass overlay on the left half containing ingredients and
    directions editors.
    """

    step_link_clicked = Signal(int)  # Emits step index for navigation

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_image_path = None
        self._recipe_data = None  # RecipeData loaded from DB
        self._current_step_index = 0  # 0 = intro step
        self._original_pixmap = None  # Full-size original (loaded once)
        self._blurred_source = None   # Pre-blurred at ~1/16 size (computed once per image)
        self._intro_ingredients = []  # In-memory intro step ingredients (list of dicts)
        self._full_title = ""  # Untruncated title text for elision
        self._top_inset = 0    # Space reserved for command bar
        self._bottom_inset = 0  # Space reserved for step navigator
        self._overlay_anim = None  # Geometry animation for overlay

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

        # --- Recipe title (wrapping, editable in edit mode) ---
        self._title_edit = QTextEdit()
        self._title_edit.setObjectName("OverlayTitleEdit")
        self._title_edit.setStyleSheet("""
            QTextEdit#OverlayTitleEdit {
                background-color: rgba(42, 42, 42, 180);
                color: #ffffff;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                font-size: 16px;
                font-weight: 600;
                padding: 0px;
            }
            QTextEdit#OverlayTitleEdit:focus {
                border-color: #0078d4;
            }
            QTextEdit#OverlayTitleEdit:disabled {
                background-color: transparent;
                border: none;
            }
        """)
        self._title_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._title_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._title_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self._title_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._title_edit.setFixedHeight(24)
        self._title_edit.setEnabled(False)
        self._title_edit.document().documentLayout().documentSizeChanged.connect(
            self._on_title_doc_size_changed
        )
        self._title_edit.textChanged.connect(self._on_title_changed)
        overlay_layout.addWidget(self._title_edit)

        # --- Ingredients section ---
        self.ingredients_editor = IngredientListEditor(title="Ingredients")
        self.ingredients_editor.aggregateRequested.connect(
            self._on_aggregate_requested
        )
        overlay_layout.addWidget(self.ingredients_editor, stretch=1)

        # --- Aggregate warning label (hidden by default) ---
        self.aggregate_warning = QLabel(
            "Aggregated on exact name/unit match — review for duplicates"
        )
        self.aggregate_warning.setStyleSheet(
            "color: #f0c040; font-size: 12px; padding: 4px 8px;"
        )
        self.aggregate_warning.hide()
        overlay_layout.addWidget(self.aggregate_warning)

        # --- Directions section ---
        self.directions_editor = RichTextEditor(
            title="Directions", placeholder="Add directions..."
        )
        self.directions_editor.step_link_clicked.connect(self._on_step_link_clicked)
        overlay_layout.addWidget(self.directions_editor, stretch=1)

        # --- Tags section (hidden by default, shown via layout mode) ---
        self.tags_editor = TagsEditor()
        self.tags_editor.tagsChanged.connect(self._on_tags_changed)
        self.tags_editor.hide()
        overlay_layout.addWidget(self.tags_editor, stretch=1)

        # --- Details section (hidden by default, shown via layout mode) ---
        self._details_container = QWidget()
        self._details_container.setAttribute(Qt.WA_TranslucentBackground)
        self._details_container.setStyleSheet("""
            QLabel.DetailsFieldLabel {
                color: #999999;
                font-size: 15px;
                background: transparent;
            }
            QLabel.DetailsFieldValue {
                color: #ffffff;
                font-size: 15px;
                background: transparent;
            }
            QSpinBox {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 15px;
            }
            QSpinBox:focus { border-color: #0078d4; }
            QSpinBox:disabled {
                background-color: transparent;
                border: none;
                padding: 0px;
                color: #ffffff;
                font-size: 15px;
            }
            QSpinBox::up-button, QSpinBox::down-button { width: 0px; }
            QComboBox {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 12px;
                min-width: 140px;
                font-size: 15px;
            }
            QComboBox:disabled {
                background-color: transparent;
                border: none;
                padding: 0px;
                color: #ffffff;
                font-size: 15px;
            }
            QComboBox::drop-down { border: none; width: 0px; }
            QComboBox::down-arrow { image: none; width: 0px; height: 0px; }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                color: white;
                selection-background-color: #0078d4;
                border: 1px solid #4a4a4a;
                font-size: 15px;
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

        self._details_prep_spin = QSpinBox()
        self._details_prep_spin.setRange(0, 999)
        self._details_prep_spin.setSuffix(" min")
        self._details_prep_spin.setFixedWidth(100)
        self._details_prep_spin.setMinimumHeight(32)
        self._details_prep_spin.valueChanged.connect(self._on_details_time_changed)
        _details_row("Prep", self._details_prep_spin)

        self._details_cook_spin = QSpinBox()
        self._details_cook_spin.setRange(0, 999)
        self._details_cook_spin.setSuffix(" min")
        self._details_cook_spin.setFixedWidth(100)
        self._details_cook_spin.setMinimumHeight(32)
        self._details_cook_spin.valueChanged.connect(self._on_details_time_changed)
        _details_row("Cook", self._details_cook_spin)

        self._details_total_label = QLabel("0 min")
        self._details_total_label.setProperty("class", "DetailsFieldValue")
        _details_row("Total", self._details_total_label)

        self._details_difficulty_combo = QComboBox()
        self._details_difficulty_combo.addItems(
            ["", "Beginner", "Intermediate", "Expert"]
        )
        self._details_difficulty_combo.setMinimumHeight(32)
        self._details_difficulty_combo.currentTextChanged.connect(
            self._on_details_changed
        )
        _details_row("Difficulty", self._details_difficulty_combo)

        self._details_cuisine_combo = QComboBox()
        self._details_cuisine_combo.addItems([
            "", "American", "Brazilian", "British", "Cajun/Creole",
            "Caribbean", "Chinese", "Ethiopian", "Filipino", "French",
            "Fusion", "German", "Greek", "Indian", "Indonesian",
            "Italian", "Japanese", "Korean", "Lebanese", "Mediterranean",
            "Mexican", "Middle Eastern", "Moroccan", "Peruvian", "Polish",
            "Southern/Soul Food", "Spanish", "Thai", "Turkish",
            "Vietnamese", "Other",
        ])
        self._details_cuisine_combo.setMinimumHeight(32)
        self._details_cuisine_combo.currentTextChanged.connect(
            self._on_details_changed
        )
        _details_row("Cuisine", self._details_cuisine_combo)

        details_layout.addStretch()
        self._details_container.hide()
        overlay_layout.addWidget(self._details_container, stretch=1)

        # --- Tips section (hidden by default, shown via layout mode) ---
        self.tips_widget = RecipeTipsWidget()
        self.tips_widget.hide()
        overlay_layout.addWidget(self.tips_widget, stretch=1)

        # Ensure overlay is above image
        self.overlay.raise_()

        # Start in display (read-only) mode
        self._editing = False
        self._layout_mode = "both"
        self.ingredients_editor.set_read_only(True)
        self.directions_editor.set_read_only(True)
        self._details_prep_spin.setEnabled(False)
        self._details_cook_spin.setEnabled(False)
        self._details_difficulty_combo.setEnabled(False)

    # ------------------------------------------------------------------
    # Edit mode
    # ------------------------------------------------------------------

    def set_editing(self, editing):
        """Toggle between display and edit mode."""
        self._editing = editing
        self._title_edit.setEnabled(editing)

        # Swap between full / elided title
        if self._current_step_index == 0 and self._full_title:
            if editing:
                self._title_edit.blockSignals(True)
                self._title_edit.setPlainText(self._full_title)
                self._title_edit.blockSignals(False)
            else:
                self._full_title = self._title_edit.toPlainText().strip()
                self._elide_title()
        self.ingredients_editor.set_read_only(not editing)
        self.directions_editor.set_read_only(not editing)
        self.tags_editor.set_read_only(not editing)
        self.ingredients_editor.set_aggregate_visible(
            editing and self._current_step_index == 0
        )
        # Details fields
        self._details_prep_spin.setEnabled(editing)
        self._details_cook_spin.setEnabled(editing)
        self._details_difficulty_combo.setEnabled(editing)
        self._details_cuisine_combo.setEnabled(editing)

    def _on_aggregate_requested(self):
        """Handle the 'Aggregate from steps' button click."""
        rd = self._recipe_data
        if not rd:
            return

        # Confirm if ingredients already exist
        current = self.ingredients_editor.get_ingredients()
        if current:
            msg = QMessageBox(self)
            msg.setWindowTitle("Replace ingredients?")
            msg.setText("This will replace the current ingredient list — continue?")
            yes_btn = msg.addButton("Yes", QMessageBox.YesRole)
            msg.addButton("No", QMessageBox.NoRole)
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            if msg.clickedButton() != yes_btn:
                return

        # Aggregate from all cooking steps
        agg = rd.aggregate_ingredients()
        self._intro_ingredients = [
            {
                "quantity": ing.quantity,
                "unit": ing.unit,
                "item_name": ing.item_name,
            }
            for ing in agg
        ]
        self.ingredients_editor.set_ingredients(self._intro_ingredients)

        # Show warning briefly
        self.aggregate_warning.show()
        QTimer.singleShot(5000, self.aggregate_warning.hide)

    TITLE_MAX_CHARS = 200

    def _on_title_doc_size_changed(self, size):
        """Resize title QTextEdit when document layout changes."""
        if self._editing:
            # Edit mode — grow freely (no upper clamp)
            height = max(24, int(size.height()) + 6)
        else:
            # Display mode — clamp to 2 lines max
            height = max(24, min(self._title_two_line_height(), int(size.height()) + 6))
        self._title_edit.setFixedHeight(height)

    def _title_two_line_height(self):
        """Return the pixel height for exactly 2 lines of title text."""
        fm = QFontMetrics(self._title_edit.font())
        return fm.lineSpacing() * 2 + 8  # +8 for frame margins

    def _elide_title(self):
        """Truncate displayed title to 2 lines with ellipsis in display mode."""
        text = self._full_title
        if not text:
            return

        fm = QFontMetrics(self._title_edit.font())
        # Approximate available width from the overlay (left half minus padding)
        avail_width = max(self._title_edit.width() - 10, 100)
        max_h = fm.lineSpacing() * 2 + 4

        def fits(s):
            rect = fm.boundingRect(0, 0, avail_width, 0, Qt.TextWordWrap, s)
            return rect.height() <= max_h

        if fits(text):
            self._title_edit.blockSignals(True)
            self._title_edit.setPlainText(text)
            self._title_edit.blockSignals(False)
            return

        # Binary search for longest truncation that fits in 2 lines
        low, high = 0, len(text)
        best = text[:20] + "..."
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid].rstrip() + "..."
            if fits(candidate):
                best = candidate
                low = mid + 1
            else:
                high = mid - 1

        self._title_edit.blockSignals(True)
        self._title_edit.setPlainText(best)
        self._title_edit.blockSignals(False)

    def _on_title_changed(self):
        """Handle title text changes — enforce char limit and mark dirty."""
        if not self._editing:
            return
        # Enforce character limit
        text = self._title_edit.toPlainText()
        if len(text) > self.TITLE_MAX_CHARS:
            cursor = self._title_edit.textCursor()
            pos = cursor.position()
            self._title_edit.blockSignals(True)
            self._title_edit.setPlainText(text[:self.TITLE_MAX_CHARS])
            cursor.setPosition(min(pos, self.TITLE_MAX_CHARS))
            self._title_edit.setTextCursor(cursor)
            self._title_edit.blockSignals(False)
        self._full_title = self._title_edit.toPlainText().strip()
        if self._recipe_data:
            self._recipe_data.dirty = True

    def _on_tags_changed(self):
        """Handle tags editor changes — update RecipeData."""
        if self._recipe_data:
            self._recipe_data.tags = self.tags_editor.get_tags()
            self._recipe_data.dirty = True

    def _on_details_changed(self):
        """Mark recipe dirty when a details field is edited."""
        if self._editing and self._recipe_data:
            self._recipe_data.dirty = True

    def _on_details_time_changed(self):
        """Update total time label and mark dirty."""
        total = self._details_prep_spin.value() + self._details_cook_spin.value()
        if total < 60:
            self._details_total_label.setText(f"{total} min")
        else:
            hours, mins = divmod(total, 60)
            self._details_total_label.setText(
                f"{hours}h" if mins == 0 else f"{hours}h {mins}m"
            )
        self._on_details_changed()

    # ------------------------------------------------------------------
    # Layout mode
    # ------------------------------------------------------------------

    def set_layout_mode(self, mode):
        """Switch overlay layout between display modes.

        Args:
            mode: One of "both", "ingredients", "directions",
                  "tags", "details", "tips", "image"
        """
        self._layout_mode = mode

        # Hide all optional sections first
        self.tags_editor.hide()
        self._details_container.hide()
        self.tips_widget.hide()

        if mode == "both":
            self.overlay.show()
            self.ingredients_editor.show()
            self.directions_editor.show()
        elif mode == "ingredients":
            self.overlay.show()
            self.ingredients_editor.show()
            self.directions_editor.hide()
        elif mode == "directions":
            self.overlay.show()
            self.ingredients_editor.hide()
            self.directions_editor.show()
        elif mode == "tags":
            self.overlay.show()
            self.ingredients_editor.hide()
            self.directions_editor.hide()
            self.tags_editor.show()
            self.tags_editor.refresh_available_tags()
        elif mode == "details":
            self.overlay.show()
            self.ingredients_editor.hide()
            self.directions_editor.hide()
            self._details_container.show()
        elif mode == "tips":
            self.overlay.show()
            self.ingredients_editor.hide()
            self.directions_editor.hide()
            self.tips_widget.show()
        elif mode == "image":
            self.overlay.hide()

        self._update_overlay_blur()

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------

    def _build_blur_source(self):
        """Create a small blurred version of the original image (once per image).

        Uses aggressive downscale + smooth upscale instead of pixel-level blur.
        The result is a small pixmap (~60x34) that gets scaled to overlay size
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

    def load_step_image(self, image_path):
        """Load and display a step image as the background."""
        self._current_image_path = image_path

        if not image_path:
            self._original_pixmap = None
            self.image_label.clear()
            self.image_label.setStyleSheet("background-color: #1a1a1a;")
            self._build_blur_source()
            return

        if not os.path.isabs(image_path):
            project_root = os.path.dirname(os.path.dirname(__file__))
            image_path = os.path.join(project_root, image_path)

        if os.path.isfile(image_path):
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                self._original_pixmap = pixmap
                # setScaledContents=True handles resize scaling automatically
                self.image_label.setPixmap(pixmap)
                self._build_blur_source()
                return

        # Fallback for missing / invalid image
        self._original_pixmap = None
        self.image_label.clear()
        self.image_label.setStyleSheet("background-color: #1a1a1a;")
        self._build_blur_source()

    def load_recipe(self, recipe_data):
        """Load a RecipeData instance for display.

        Args:
            recipe_data: A RecipeData with steps and ingredients populated.
        """
        self._recipe_data = recipe_data
        self._intro_ingredients = [
            {
                "quantity": ing.quantity,
                "unit": ing.unit,
                "item_name": ing.item_name,
            }
            for ing in recipe_data.intro_ingredients
        ]
        self._current_step_index = 0
        self._populate_step(0)

        # Load tags
        self.tags_editor.refresh_available_tags()
        self.tags_editor.set_tags(recipe_data.tags)

        # Update directions editor with step count for linking
        step_count = len(recipe_data.steps) + 1  # +1 for intro
        self.directions_editor.set_step_count(step_count)

    def _on_step_link_clicked(self, step_index):
        """Handle step link click from the directions editor.

        Args:
            step_index: 0-based step index to navigate to
        """
        self.step_link_clicked.emit(step_index)

    def _save_current_step(self):
        """Write the current editor state back to RecipeData if in edit mode."""
        if not self._editing or not self._recipe_data:
            return
        rd = self._recipe_data
        idx = self._current_step_index

        # Save title (recipe-level, not step-specific)
        new_title = self._full_title or self._title_edit.toPlainText().strip()
        if new_title != rd.title:
            rd.title = new_title
            rd.dirty = True

        # Read ingredients from editor
        editor_ings = self.ingredients_editor.get_ingredients()

        if idx == 0:
            # Intro step — save to local intro list, RecipeData, and description
            self._intro_ingredients = editor_ings
            rd.intro_ingredients = [
                IngredientData(
                    ingredient_id=None,
                    item_name=ing["item_name"],
                    quantity=self._parse_quantity(ing["quantity"]),
                    unit=ing["unit"],
                )
                for ing in editor_ings
            ]
            rd.description = self.directions_editor.get_html()

            # Save details fields
            rd.prep_time_min = self._details_prep_spin.value() or None
            rd.cook_time_min = self._details_cook_spin.value() or None
            diff = self._details_difficulty_combo.currentText()
            rd.difficulty = diff if diff else None
            cuisine = self._details_cuisine_combo.currentText()
            rd.cuisine_type = cuisine if cuisine else None
        else:
            # Regular step — save ingredients and instruction to RecipeData
            data_idx = idx - 1
            if data_idx < len(rd.steps):
                step = rd.steps[data_idx]
                step.ingredients = [
                    IngredientData(
                        ingredient_id=None,
                        item_name=ing["item_name"],
                        quantity=self._parse_quantity(ing["quantity"]),
                        unit=ing["unit"],
                    )
                    for ing in editor_ings
                ]
                step.instruction = self.directions_editor.get_html()

    @staticmethod
    def _parse_quantity(value):
        """Parse a quantity string to float, returning 0.0 on failure.

        Accepts decimals ("1.5"), fractions ("1/2"), and mixed numbers ("1 1/2").
        """
        try:
            if not value:
                return 0.0
            parts = value.strip().split()
            if len(parts) == 2:
                return float(Fraction(parts[0]) + Fraction(parts[1]))
            return float(Fraction(value.strip()))
        except (ValueError, TypeError, ZeroDivisionError):
            return 0.0

    def load_step(self, step_index):
        """Load content for a specific step (0-based index).

        Step 0 is the intro step: shows aggregated ingredients from all steps
        and the recipe description as directions.

        Steps 1+ show that step's specific ingredients and instruction.
        """
        # Save edits from the current step before switching
        self._save_current_step()
        self._current_step_index = step_index

        if self._recipe_data:
            self._populate_step(step_index)
        else:
            # Fallback if no recipe data loaded (shouldn't happen in normal flow)
            self.ingredients_editor.clear()
            self.directions_editor.set_html("")
            self.load_step_image(None)

    def _populate_step(self, step_index):
        """Populate the overlay panels from RecipeData for the given step."""
        rd = self._recipe_data
        if not rd:
            return

        # Show/hide aggregate button based on step and edit state
        self.ingredients_editor.set_aggregate_visible(
            step_index == 0 and self._editing
        )
        self.aggregate_warning.hide()

        self.directions_editor.set_title(
            "Description" if step_index == 0 else "Directions"
        )

        # Show recipe title only on the intro step
        if step_index == 0:
            self._full_title = rd.title or ""
            if self._editing:
                self._title_edit.blockSignals(True)
                self._title_edit.setPlainText(self._full_title)
                self._title_edit.blockSignals(False)
            else:
                self._elide_title()
            self._title_edit.show()

            # Populate details fields (block signals to avoid false dirty)
            self._details_prep_spin.blockSignals(True)
            self._details_cook_spin.blockSignals(True)
            self._details_difficulty_combo.blockSignals(True)
            self._details_cuisine_combo.blockSignals(True)

            self._details_prep_spin.setValue(rd.prep_time_min or 0)
            self._details_cook_spin.setValue(rd.cook_time_min or 0)
            self._on_details_time_changed()  # update total label

            diff_text = rd.difficulty or ""
            idx_diff = self._details_difficulty_combo.findText(diff_text)
            self._details_difficulty_combo.setCurrentIndex(max(0, idx_diff))

            cuisine_text = rd.cuisine_type or ""
            idx_cuisine = self._details_cuisine_combo.findText(cuisine_text)
            self._details_cuisine_combo.setCurrentIndex(max(0, idx_cuisine))

            self._details_prep_spin.blockSignals(False)
            self._details_cook_spin.blockSignals(False)
            self._details_difficulty_combo.blockSignals(False)
            self._details_cuisine_combo.blockSignals(False)
        else:
            self._title_edit.hide()

        self.ingredients_editor.set_grocery_btn_visible(step_index == 0)

        if step_index == 0:
            # Intro step: show the intro ingredient list (populated by aggregation)
            self.ingredients_editor.set_ingredients(self._intro_ingredients)
            self.directions_editor.set_html(rd.description or "")
            # Use the recipe's main image for the intro step
            self.load_step_image(rd.main_image_path)
        else:
            # Regular step (1-based in UI, but steps list is 0-based)
            idx = step_index - 1
            if idx < len(rd.steps):
                step = rd.steps[idx]
                self.ingredients_editor.set_ingredients([
                    {
                        "quantity": ing.quantity,
                        "unit": ing.unit,
                        "item_name": (
                            f"{ing.item_name} ({ing.amount_override})"
                            if ing.amount_override
                            else ing.item_name
                        ),
                    }
                    for ing in step.ingredients
                ])
                self.directions_editor.set_html(step.instruction or "")
                self.load_step_image(
                    step.image_path
                    or f"media/recipes/{rd.recipe_id}/step_{step.step_number}.webp"
                )
            else:
                self.ingredients_editor.clear()
                self.directions_editor.set_html("")
                self.load_step_image(None)

    # ------------------------------------------------------------------
    # Layout
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

    def scroll_pane(self, pane: str, direction: str):
        """Scroll the ingredients or directions pane by one viewport page.

        Args:
            pane: "ingredients" or "directions".
            direction: "down" (show more) or "up" (go back).
        """
        if pane == "ingredients":
            self.ingredients_editor.scroll_by_page(direction)
        else:
            self.directions_editor.scroll_by_page(direction)

    def adjust_font_size(self, delta):
        """Adjust font size in both ingredients and directions editors.

        Args:
            delta: Number of pixels to add (positive) or subtract (negative).
        """
        self.ingredients_editor.adjust_font_size(delta)
        self.directions_editor.adjust_font_size(delta)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay_anim is not None:
            self._overlay_anim.stop()
            self._overlay_anim = None
        self._reposition_overlay()
        # Re-elide title after width changes
        if not self._editing and self._full_title:
            self._elide_title()

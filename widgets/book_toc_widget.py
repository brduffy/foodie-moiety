"""Book table of contents widget - categorized recipe list with edit mode."""

import copy

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ------------------------------------------------------------------
# Style constants
# ------------------------------------------------------------------

# Small icon button (22x22, semi-transparent)
_ICON_BTN_SS = """
    QPushButton {
        background-color: rgba(255, 255, 255, 25);
        color: white;
        border: none;
        border-radius: 3px;
        font-size: 11px;
        min-width: 22px; max-width: 22px;
        min-height: 22px; max-height: 22px;
    }
    QPushButton:hover { background-color: rgba(255, 255, 255, 50); color: white; }
    QPushButton:pressed { background-color: rgba(255, 255, 255, 15); }
    QPushButton:disabled { color: #444444; background-color: transparent; }
"""

# Small text button (variable width, semi-transparent)
_TEXT_BTN_SS = """
    QPushButton {
        background-color: rgba(255, 255, 255, 25);
        color: white;
        border: none;
        border-radius: 3px;
        font-size: 11px;
        padding: 2px 8px;
        min-height: 22px; max-height: 22px;
    }
    QPushButton:hover { background-color: rgba(255, 255, 255, 50); color: white; }
    QPushButton:pressed { background-color: rgba(255, 255, 255, 15); }
"""

# Category name edit field
_CAT_NAME_SS = """
    QLineEdit {
        background-color: transparent;
        color: #f0c040;
        border: none;
        font-size: 13px;
        font-weight: bold;
        padding: 0px 2px;
    }
    QLineEdit:focus {
        background-color: rgba(255, 255, 255, 10);
        border: 1px solid rgba(240, 192, 64, 30);
        border-radius: 3px;
    }
"""

# Description edit field (edit mode) — QTextEdit for word-wrap
_DESC_EDIT_SS = """
    QTextEdit {
        background-color: rgba(0, 0, 0, 60);
        color: #e0e0e0;
        border: 1px solid rgba(255, 255, 255, 12);
        border-radius: 2px;
        font-size: 12px;
        padding: 0px 2px 0px 8px;
    }
    QTextEdit:focus {
        background-color: rgba(0, 0, 0, 110);
        border: 1px solid rgba(255, 255, 255, 35);
    }
"""

_SCROLL_SS = """
    QScrollArea { background-color: transparent; border: none; }
    QScrollArea > QWidget > QWidget { background-color: transparent; }
    QScrollBar:vertical { background-color: transparent; width: 6px; }
    QScrollBar::handle:vertical {
        background-color: rgba(255, 255, 255, 80);
        border-radius: 3px; min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""

# Selection highlights
_ENTRY_SEL_SS = (
    "background-color: rgba(255, 255, 255, 40);"
    "border-left: 3px solid rgba(240, 192, 64, 180);"
    "border-radius: 4px;"
)
_ENTRY_NORMAL_SS = "background-color: transparent; border-left: 3px solid transparent;"
_ENTRY_VIEW_SS = """
    #toc_entry {
        background-color: transparent;
        border-left: 3px solid transparent;
    }
    #toc_entry:hover {
        background-color: rgba(255, 255, 255, 15);
        border-radius: 4px;
    }
"""
_CAT_SEL_SS = (
    "background-color: rgba(240, 192, 64, 25);"
    "border-left: 3px solid rgba(240, 192, 64, 180);"
    "border-radius: 4px;"
)
_CAT_NORMAL_SS = "background-color: transparent; border-left: 3px solid transparent;"

_MENU_SS = """
    QMenu {
        background-color: #2a2a2a;
        color: #cccccc;
        border: 1px solid #4a4a4a;
        border-radius: 4px;
        padding: 4px 0px;
        font-size: 13px;
    }
    QMenu::item { padding: 6px 20px; }
    QMenu::item:selected { background-color: #4a4a4a; color: white; }
"""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_icon_btn(text, tooltip=""):
    btn = QPushButton(text)
    btn.setStyleSheet(_ICON_BTN_SS)
    btn.setFixedSize(22, 22)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def _make_text_btn(text, tooltip=""):
    btn = QPushButton(text)
    btn.setStyleSheet(_TEXT_BTN_SS)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


# ------------------------------------------------------------------
# BookTocWidget
# ------------------------------------------------------------------

class BookTocWidget(QWidget):
    """Categorized table of contents for a recipe book.

    Supports view mode (clean, navigable) and edit mode (toolbars for
    reordering, adding/removing categories and recipes).

    Selection model:
      - Click a recipe to select it (and implicitly its category).
      - Click a category toolbar to select just the category.
      - TOC toolbar up/down operates on the selected category.
      - Category toolbar up/down operates on the selected recipe
        within that category.
    """

    recipe_clicked = Signal(int)       # recipe_id — navigate to recipe detail
    recipe_hovered = Signal(str)       # main_image_path (empty = hover left)
    add_recipes_requested = Signal()   # navigate to recipe list selection mode
    toc_changed = Signal()             # data structure modified

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._edit_mode = False
        self._font_size_delta = 0          # cumulative +/- from base sizes
        self._categories = []              # list[BookCategoryData] working copy
        self._selected_cat_idx = None
        self._selected_recipe_idx = None   # within selected category

        # Widget references for targeted selection updates
        self._entry_widgets = []           # list[list[QWidget]]
        self._section_widgets = []         # list[QWidget] per category section
        self._cat_toolbar_widgets = []     # list[QWidget]
        self._cat_up_btns = []             # per-category recipe-up buttons
        self._cat_down_btns = []           # per-category recipe-down buttons

        # Swap animation state
        self._swap_anim = None             # QParallelAnimationGroup
        self._swap_overlays = []           # overlay QLabels
        self._swap_hidden = []             # real widgets hidden during anim

        # --- Layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title
        self._title_label = QLabel("Table of Contents")
        self._title_label.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 6px 8px;
            }
        """)
        layout.addWidget(self._title_label)

        # TOC toolbar (edit mode only)
        self._toc_toolbar = self._build_toc_toolbar()
        self._toc_toolbar.hide()
        layout.addWidget(self._toc_toolbar)

        # Separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 30);")
        layout.addWidget(sep)

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(_SCROLL_SS)

        self._content = QWidget()
        self._content.setAttribute(Qt.WA_TranslucentBackground)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 4, 0, 4)
        self._content_layout.setSpacing(0)

        placeholder = QLabel("No categories yet")
        placeholder.setStyleSheet(
            "color: #888888; font-size: 13px; padding: 8px;"
        )
        self._content_layout.addWidget(placeholder)
        self._content_layout.addStretch()

        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll)

    # ------------------------------------------------------------------
    # TOC toolbar (top-level, operates on categories)
    # ------------------------------------------------------------------

    def _build_toc_toolbar(self):
        toolbar = QWidget()
        toolbar.setAttribute(Qt.WA_StyledBackground, True)
        toolbar.setStyleSheet("background-color: rgba(255, 255, 255, 8);")

        lay = QHBoxLayout(toolbar)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(4)

        self._toc_up_btn = _make_icon_btn("\u25b2", "Move category up")
        self._toc_up_btn.clicked.connect(self._move_category_up)
        lay.addWidget(self._toc_up_btn)

        self._toc_down_btn = _make_icon_btn("\u25bc", "Move category down")
        self._toc_down_btn.clicked.connect(self._move_category_down)
        lay.addWidget(self._toc_down_btn)

        lay.addStretch()

        add_cat_btn = _make_text_btn("+ Category")
        add_cat_btn.clicked.connect(self._add_category)
        lay.addWidget(add_cat_btn)

        add_recipes_btn = _make_text_btn("+ Recipes")
        add_recipes_btn.clicked.connect(self.add_recipes_requested.emit)
        lay.addWidget(add_recipes_btn)

        return toolbar

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_toc(self, categories):
        """Load table of contents from a list of BookCategoryData.

        Makes a deep copy so edits don't affect the source data.
        """
        self._categories = [copy.deepcopy(cat) for cat in categories]
        self._selected_cat_idx = None
        self._selected_recipe_idx = None
        self._rebuild_toc()

    def get_categories(self):
        """Return a deep copy of current categories (for saving back to BookData)."""
        return copy.deepcopy(self._categories)

    def set_edit_mode(self, editing):
        """Toggle between view mode and edit mode."""
        if editing == self._edit_mode:
            return
        self._edit_mode = editing
        self._toc_toolbar.setVisible(editing)
        if not editing:
            self._selected_cat_idx = None
            self._selected_recipe_idx = None
        self._rebuild_toc()

    @property
    def edit_mode(self):
        return self._edit_mode

    def adjust_font_size(self, delta):
        """Adjust all font sizes by delta (positive = larger).

        Clamps the cumulative delta to 0–10, matching the 14→24px range
        used by RichTextEditor and IngredientListEditor.
        """
        self._font_size_delta = max(0, min(10, self._font_size_delta + delta))
        self._title_label.setStyleSheet(f"""
            QLabel {{
                background-color: transparent;
                color: white;
                font-size: {14 + self._font_size_delta}px;
                font-weight: bold;
                padding: 6px 8px;
            }}
        """)
        self._rebuild_toc()

    def _fs(self, base):
        """Return a font size adjusted by the current delta."""
        return base + self._font_size_delta

    # ------------------------------------------------------------------
    # Full UI rebuild
    # ------------------------------------------------------------------

    def _rebuild_toc(self):
        """Rebuild the scroll content from self._categories."""
        scroll_pos = self._scroll.verticalScrollBar().value()

        # Clear existing content
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._entry_widgets = []
        self._section_widgets = []
        self._cat_toolbar_widgets = []
        self._cat_up_btns = []
        self._cat_down_btns = []

        if not self._categories:
            placeholder = QLabel("No categories yet")
            placeholder.setStyleSheet(
                f"color: #888888; font-size: {self._fs(13)}px; padding: 8px;"
            )
            self._content_layout.addWidget(placeholder)
            self._content_layout.addStretch()
            self._update_toolbar_states()
            return

        for cat_idx, cat in enumerate(self._categories):
            entries = []
            section = self._build_category_section(cat_idx, cat, entries)
            self._entry_widgets.append(entries)
            self._section_widgets.append(section)
            self._content_layout.addWidget(section)

        self._content_layout.addStretch()
        self._update_toolbar_states()

        # Restore scroll position after layout settles
        QTimer.singleShot(
            0, lambda v=scroll_pos: self._scroll.verticalScrollBar().setValue(v)
        )

    # ------------------------------------------------------------------
    # Category section builder
    # ------------------------------------------------------------------

    def _build_category_section(self, cat_idx, cat, entries_out):
        section = QWidget()
        section.setAttribute(Qt.WA_TranslucentBackground)
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        if self._edit_mode:
            toolbar = self._build_category_toolbar(cat_idx, cat)
            self._cat_toolbar_widgets.append(toolbar)
            lay.addWidget(toolbar)
        else:
            header = QLabel(cat.name)
            header.setStyleSheet(f"""
                QLabel {{
                    color: #f0c040;
                    font-size: {self._fs(13)}px;
                    font-weight: bold;
                    padding: 8px 8px 4px 8px;
                    background-color: transparent;
                }}
            """)
            self._cat_toolbar_widgets.append(header)
            lay.addWidget(header)

        for recipe_idx, recipe in enumerate(cat.recipes):
            entry = self._build_recipe_entry(cat_idx, recipe_idx, recipe)
            entries_out.append(entry)
            lay.addWidget(entry)

        return section

    # ------------------------------------------------------------------
    # Category toolbar (edit mode)
    # ------------------------------------------------------------------

    def _build_category_toolbar(self, cat_idx, cat):
        toolbar = QWidget()
        toolbar.setAttribute(Qt.WA_StyledBackground, True)

        is_selected = self._selected_cat_idx == cat_idx
        toolbar.setStyleSheet(_CAT_SEL_SS if is_selected else _CAT_NORMAL_SS)

        # Click on toolbar background selects the category
        toolbar.mousePressEvent = lambda e, ci=cat_idx: self._select_category(ci)
        toolbar.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(toolbar)
        lay.setContentsMargins(8, 6, 8, 4)
        lay.setSpacing(4)

        # Category name (editable QLineEdit)
        name_edit = QLineEdit(cat.name)
        name_edit.setStyleSheet(_CAT_NAME_SS.replace(
            "font-size: 13px", f"font-size: {self._fs(13)}px"
        ))
        name_edit.editingFinished.connect(
            lambda ci=cat_idx, le=name_edit: self._on_category_renamed(
                ci, le.text()
            )
        )
        # Clicking the name to edit also selects the category
        _orig_focus_in = name_edit.focusInEvent

        def _name_focus_in(event, ci=cat_idx, orig=_orig_focus_in):
            self._select_category(ci)
            orig(event)

        name_edit.focusInEvent = _name_focus_in
        lay.addWidget(name_edit, stretch=1)

        # Recipe reorder buttons
        up_btn = _make_icon_btn("\u25b2", "Move recipe up")
        up_btn.clicked.connect(
            lambda checked=False, ci=cat_idx: self._move_recipe_up(ci)
        )
        self._cat_up_btns.append(up_btn)
        lay.addWidget(up_btn)

        down_btn = _make_icon_btn("\u25bc", "Move recipe down")
        down_btn.clicked.connect(
            lambda checked=False, ci=cat_idx: self._move_recipe_down(ci)
        )
        self._cat_down_btns.append(down_btn)
        lay.addWidget(down_btn)

        # Delete category
        del_btn = _make_icon_btn("\u2715", "Delete category")
        del_btn.clicked.connect(
            lambda checked=False, ci=cat_idx: self._delete_category(ci)
        )
        lay.addWidget(del_btn)

        return toolbar

    # ------------------------------------------------------------------
    # Recipe entry builder
    # ------------------------------------------------------------------

    def _build_recipe_entry(self, cat_idx, recipe_idx, recipe):
        entry = QWidget()
        entry.setAttribute(Qt.WA_StyledBackground, True)

        is_selected = (
            self._selected_cat_idx == cat_idx
            and self._selected_recipe_idx == recipe_idx
        )
        if self._edit_mode:
            entry.setStyleSheet(_ENTRY_SEL_SS if is_selected else _ENTRY_NORMAL_SS)
        else:
            entry.setObjectName("toc_entry")
            entry.setStyleSheet(_ENTRY_VIEW_SS)

        lay = QVBoxLayout(entry)
        lay.setContentsMargins(16, 8, 8, 8)
        lay.setSpacing(1)

        title = recipe.get("title", "Untitled")
        desc = recipe.get("book_description", "")

        # --- Title row ---
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)

        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet(
            f"color: white; font-size: {self._fs(13)}px; background: transparent;"
        )
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        title_row.addWidget(title_label, stretch=1)

        if self._edit_mode:
            move_btn = _make_text_btn("Move to\u2026")
            move_btn.clicked.connect(
                lambda checked=False, ci=cat_idx, ri=recipe_idx, b=move_btn: self._show_move_menu(
                    ci, ri, b
                )
            )
            title_row.addWidget(move_btn)

            remove_btn = _make_icon_btn("\u2715", "Remove from book")
            remove_btn.clicked.connect(
                lambda checked=False, ci=cat_idx, ri=recipe_idx: self._remove_recipe(
                    ci, ri
                )
            )
            title_row.addWidget(remove_btn)

        lay.addLayout(title_row)

        # --- Description ---
        if self._edit_mode:
            desc_edit = QTextEdit()
            desc_edit.setAcceptRichText(False)
            desc_edit.setPlaceholderText("Short description\u2026")
            desc_edit.setStyleSheet(_DESC_EDIT_SS.replace(
                "font-size: 12px", f"font-size: {self._fs(12)}px"
            ))
            desc_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            desc_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # Forward wheel events to the scroll area so scrolling the
            # list doesn't get trapped inside a description field.
            desc_edit.wheelEvent = lambda e, sa=self._scroll: sa.wheelEvent(e)
            # Auto-resize height to fit content.
            # QTextDocumentLayout.documentSize().height() returns actual
            # pixel height (unlike QPlainTextDocumentLayout which returns
            # line count), so we can use it directly.
            def _resize_desc(de):
                h = int(de.document().size().height()) + de.frameWidth() * 2
                de.setFixedHeight(max(28, h))
            desc_edit.document().documentLayout().documentSizeChanged.connect(
                lambda size, de=desc_edit: _resize_desc(de)
            )
            desc_edit.textChanged.connect(
                lambda ci=cat_idx, ri=recipe_idx, te=desc_edit: self._on_description_changed(
                    ci, ri, te.toPlainText()
                )
            )
            desc_edit.setPlainText(desc)
            _resize_desc(desc_edit)
            lay.addWidget(desc_edit)
        elif desc:
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(
                f"color: #cccccc; font-size: {self._fs(12)}px; background: transparent;"
                "padding-left: 8px;"
            )
            desc_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(desc_label)

        # --- Click handler ---
        if self._edit_mode:
            entry.mousePressEvent = lambda e, ci=cat_idx, ri=recipe_idx: self._select_recipe(
                ci, ri
            )
            entry.setCursor(Qt.PointingHandCursor)
        else:
            recipe_id = recipe.get("recipe_id")
            if recipe_id is not None:
                entry.mousePressEvent = (
                    lambda e, rid=recipe_id: self.recipe_clicked.emit(rid)
                )
                entry.setCursor(Qt.PointingHandCursor)
            img = recipe.get("main_image_path", "") or ""
            entry.enterEvent = lambda e, p=img: self.recipe_hovered.emit(p)
            entry.leaveEvent = lambda e: self.recipe_hovered.emit("")

        return entry

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _select_recipe(self, cat_idx, recipe_idx):
        """Select a recipe entry (and implicitly its category)."""
        if (
            self._selected_cat_idx == cat_idx
            and self._selected_recipe_idx == recipe_idx
        ):
            return
        self._selected_cat_idx = cat_idx
        self._selected_recipe_idx = recipe_idx
        self._update_selection_visuals()
        self._update_toolbar_states()

    def _select_category(self, cat_idx):
        """Select a category (deselects any recipe)."""
        self._selected_cat_idx = cat_idx
        self._selected_recipe_idx = None
        self._update_selection_visuals()
        self._update_toolbar_states()

    def _update_selection_visuals(self):
        """Update highlight styles on entries and category toolbars."""
        for ci, entries in enumerate(self._entry_widgets):
            for ri, entry in enumerate(entries):
                is_sel = (
                    self._selected_cat_idx == ci
                    and self._selected_recipe_idx == ri
                )
                entry.setStyleSheet(
                    _ENTRY_SEL_SS if is_sel else _ENTRY_NORMAL_SS
                )

        if self._edit_mode:
            for ci, toolbar in enumerate(self._cat_toolbar_widgets):
                is_sel = self._selected_cat_idx == ci
                toolbar.setStyleSheet(
                    _CAT_SEL_SS if is_sel else _CAT_NORMAL_SS
                )

    def _update_toolbar_states(self):
        """Enable/disable toolbar buttons based on selection state."""
        ci = self._selected_cat_idx
        ri = self._selected_recipe_idx
        n_cats = len(self._categories)

        # TOC toolbar
        self._toc_up_btn.setEnabled(ci is not None and ci > 0)
        self._toc_down_btn.setEnabled(ci is not None and ci < n_cats - 1)

        # Per-category recipe reorder buttons
        for idx, (up, down) in enumerate(
            zip(self._cat_up_btns, self._cat_down_btns)
        ):
            if ci == idx and ri is not None:
                n_recipes = len(self._categories[idx].recipes)
                up.setEnabled(ri > 0)
                down.setEnabled(ri < n_recipes - 1)
            else:
                up.setEnabled(False)
                down.setEnabled(False)

    # ------------------------------------------------------------------
    # Swap animation
    # ------------------------------------------------------------------

    def _finish_pending_swap(self):
        """Immediately finish any running swap animation."""
        if self._swap_anim is not None:
            self._swap_anim.stop()
        for ov in self._swap_overlays:
            ov.deleteLater()
        for w in self._swap_hidden:
            w.setGraphicsEffect(None)  # Remove opacity → widget appears
        self._swap_overlays = []
        self._swap_hidden = []
        self._swap_anim = None

    def _animate_item_swap(self, old_a, old_b, swap_fn, get_new_fn):
        """Animate two items swapping positions.

        Args:
            old_a, old_b: The two widgets being swapped (before rebuild).
            swap_fn: Callable that performs the data swap + rebuild.
            get_new_fn: Callable returning (new_a, new_b) after rebuild.
        """
        self._finish_pending_swap()

        viewport = self._scroll.viewport()
        saved_scroll = self._scroll.verticalScrollBar().value()

        # Record positions before swap
        pos_a = old_a.mapTo(viewport, QPoint(0, 0))
        pos_b = old_b.mapTo(viewport, QPoint(0, 0))

        # Perform the swap (triggers _rebuild_toc)
        swap_fn()

        # Let layout fully settle — word-wrap labels need the event loop
        # to compute heightForWidth, and _rebuild_toc defers scroll restore.
        new_a, new_b = get_new_fn()
        self._content.layout().activate()
        QApplication.processEvents()
        self._scroll.verticalScrollBar().setValue(saved_scroll)

        end_a = new_a.mapTo(viewport, QPoint(0, 0))
        end_b = new_b.mapTo(viewport, QPoint(0, 0))

        # Grab new widget pixmaps (final appearance = seamless reveal)
        pix_a = new_a.grab()
        pix_b = new_b.grab()

        # Make new widgets invisible via opacity (no layout impact)
        for w in (new_a, new_b):
            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)
        self._swap_hidden = [new_a, new_b]

        # Create overlays with final appearance at old positions.
        # Use widget logical size, not pixmap device pixels (Retina = 2x).
        ov_a = QLabel(viewport)
        ov_a.setPixmap(pix_a)
        ov_a.move(pos_a)
        ov_a.resize(new_a.size())
        ov_a.show()
        ov_a.raise_()

        ov_b = QLabel(viewport)
        ov_b.setPixmap(pix_b)
        ov_b.move(pos_b)
        ov_b.resize(new_b.size())
        ov_b.show()
        ov_b.raise_()
        self._swap_overlays = [ov_a, ov_b]

        # Animate overlays from old positions to exact new positions
        anim_a = QPropertyAnimation(ov_a, b"pos")
        anim_a.setDuration(200)
        anim_a.setStartValue(pos_a)
        anim_a.setEndValue(end_a)
        anim_a.setEasingCurve(QEasingCurve.InOutCubic)

        anim_b = QPropertyAnimation(ov_b, b"pos")
        anim_b.setDuration(200)
        anim_b.setStartValue(pos_b)
        anim_b.setEndValue(end_b)
        anim_b.setEasingCurve(QEasingCurve.InOutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_a)
        group.addAnimation(anim_b)
        group.finished.connect(self._finish_pending_swap)
        self._swap_anim = group
        group.start()

    # ------------------------------------------------------------------
    # Category operations
    # ------------------------------------------------------------------

    def _move_category_up(self):
        ci = self._selected_cat_idx
        if ci is None or ci == 0:
            return
        old_a = self._section_widgets[ci]
        old_b = self._section_widgets[ci - 1]

        def swap():
            cats = self._categories
            cats[ci], cats[ci - 1] = cats[ci - 1], cats[ci]
            self._selected_cat_idx = ci - 1
            self._rebuild_toc()
            self.toc_changed.emit()

        self._animate_item_swap(
            old_a, old_b, swap,
            lambda: (self._section_widgets[ci - 1], self._section_widgets[ci]),
        )

    def _move_category_down(self):
        ci = self._selected_cat_idx
        if ci is None or ci >= len(self._categories) - 1:
            return
        old_a = self._section_widgets[ci]
        old_b = self._section_widgets[ci + 1]

        def swap():
            cats = self._categories
            cats[ci], cats[ci + 1] = cats[ci + 1], cats[ci]
            self._selected_cat_idx = ci + 1
            self._rebuild_toc()
            self.toc_changed.emit()

        self._animate_item_swap(
            old_a, old_b, swap,
            lambda: (self._section_widgets[ci + 1], self._section_widgets[ci]),
        )

    def _add_category(self):
        from models.recipe_data import BookCategoryData

        new_cat = BookCategoryData(
            category_id=None,
            name="New Category",
            display_order=len(self._categories),
        )
        self._categories.append(new_cat)
        self._selected_cat_idx = len(self._categories) - 1
        self._selected_recipe_idx = None
        self._rebuild_toc()
        self.toc_changed.emit()

    def _delete_category(self, cat_idx):
        if cat_idx >= len(self._categories):
            return
        cat = self._categories[cat_idx]
        if cat.recipes:
            from PySide6.QtWidgets import QMessageBox
            from utils.helpers import DIALOG_STYLE, white_question_icon
            dlg = QMessageBox(
                QMessageBox.Question,
                "Delete Category",
                f'Delete "{cat.name}" and its {len(cat.recipes)} recipe(s)?',
                QMessageBox.Yes | QMessageBox.No,
                self,
            )
            dlg.setDefaultButton(QMessageBox.No)
            dlg.setIconPixmap(white_question_icon())
            dlg.setStyleSheet(DIALOG_STYLE)
            if dlg.exec() != QMessageBox.Yes:
                return
        self._categories.pop(cat_idx)
        if self._selected_cat_idx == cat_idx:
            self._selected_cat_idx = None
            self._selected_recipe_idx = None
        elif (
            self._selected_cat_idx is not None
            and self._selected_cat_idx > cat_idx
        ):
            self._selected_cat_idx -= 1
        self._rebuild_toc()
        self.toc_changed.emit()

    def _on_category_renamed(self, cat_idx, new_name):
        new_name = new_name.strip()
        if cat_idx < len(self._categories) and new_name:
            self._categories[cat_idx].name = new_name
            self.toc_changed.emit()

    # ------------------------------------------------------------------
    # Recipe operations
    # ------------------------------------------------------------------

    def _move_recipe_up(self, cat_idx):
        if self._selected_cat_idx != cat_idx or self._selected_recipe_idx is None:
            return
        ri = self._selected_recipe_idx
        recipes = self._categories[cat_idx].recipes
        if ri == 0 or ri >= len(recipes):
            return
        old_a = self._entry_widgets[cat_idx][ri]
        old_b = self._entry_widgets[cat_idx][ri - 1]

        def swap():
            recipes[ri], recipes[ri - 1] = recipes[ri - 1], recipes[ri]
            self._selected_recipe_idx = ri - 1
            self._rebuild_toc()
            self.toc_changed.emit()

        ci = cat_idx
        self._animate_item_swap(
            old_a, old_b, swap,
            lambda: (self._entry_widgets[ci][ri - 1], self._entry_widgets[ci][ri]),
        )

    def _move_recipe_down(self, cat_idx):
        if self._selected_cat_idx != cat_idx or self._selected_recipe_idx is None:
            return
        ri = self._selected_recipe_idx
        recipes = self._categories[cat_idx].recipes
        if ri >= len(recipes) - 1:
            return
        old_a = self._entry_widgets[cat_idx][ri]
        old_b = self._entry_widgets[cat_idx][ri + 1]

        def swap():
            recipes[ri], recipes[ri + 1] = recipes[ri + 1], recipes[ri]
            self._selected_recipe_idx = ri + 1
            self._rebuild_toc()
            self.toc_changed.emit()

        ci = cat_idx
        self._animate_item_swap(
            old_a, old_b, swap,
            lambda: (self._entry_widgets[ci][ri + 1], self._entry_widgets[ci][ri]),
        )

    def _show_move_menu(self, cat_idx, recipe_idx, button):
        """Show context menu to move a recipe to another category."""
        if len(self._categories) < 2:
            return
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_SS)
        for i, cat in enumerate(self._categories):
            if i == cat_idx:
                continue
            action = menu.addAction(cat.name)
            action.setData(i)

        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen is not None:
            target_idx = chosen.data()
            if target_idx is not None:
                self._move_recipe_to(cat_idx, recipe_idx, target_idx)

    def _move_recipe_to(self, from_cat_idx, recipe_idx, to_cat_idx):
        """Move a recipe to the bottom of another category, animated."""
        self._finish_pending_swap()

        viewport = self._scroll.viewport()
        saved_scroll = self._scroll.verticalScrollBar().value()

        # Grab the recipe entry before the move
        old_entry = self._entry_widgets[from_cat_idx][recipe_idx]
        pix = old_entry.grab()
        old_size = old_entry.size()
        pos_start = old_entry.mapTo(viewport, QPoint(0, 0))

        # Perform the data move + rebuild
        recipe = self._categories[from_cat_idx].recipes.pop(recipe_idx)
        self._categories[to_cat_idx].recipes.append(recipe)
        self._selected_cat_idx = to_cat_idx
        new_ri = len(self._categories[to_cat_idx].recipes) - 1
        self._selected_recipe_idx = new_ri
        self._rebuild_toc()
        self.toc_changed.emit()

        # Let layout fully settle before computing positions
        new_entry = self._entry_widgets[to_cat_idx][new_ri]
        self._content.layout().activate()
        QApplication.processEvents()
        self._scroll.verticalScrollBar().setValue(saved_scroll)
        pos_end = new_entry.mapTo(viewport, QPoint(0, 0))

        # Make destination invisible via opacity (no layout impact)
        eff = QGraphicsOpacityEffect(new_entry)
        eff.setOpacity(0.0)
        new_entry.setGraphicsEffect(eff)
        self._swap_hidden = [new_entry]

        # Create overlay sliding from source to destination.
        # Use widget logical size, not pixmap device pixels (Retina = 2x).
        ov = QLabel(viewport)
        ov.setPixmap(pix)
        ov.move(pos_start)
        ov.resize(old_size)
        ov.show()
        ov.raise_()
        self._swap_overlays = [ov]

        anim = QPropertyAnimation(ov, b"pos")
        anim.setDuration(300)
        anim.setStartValue(pos_start)
        anim.setEndValue(pos_end)
        anim.setEasingCurve(QEasingCurve.InOutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim)
        group.finished.connect(self._finish_pending_swap)
        self._swap_anim = group
        group.start()

    def _remove_recipe(self, cat_idx, recipe_idx):
        """Remove a recipe from the book (does not delete the recipe)."""
        recipes = self._categories[cat_idx].recipes
        if recipe_idx >= len(recipes):
            return
        recipes.pop(recipe_idx)
        if (
            self._selected_cat_idx == cat_idx
            and self._selected_recipe_idx == recipe_idx
        ):
            self._selected_recipe_idx = None
        elif (
            self._selected_cat_idx == cat_idx
            and self._selected_recipe_idx is not None
            and self._selected_recipe_idx > recipe_idx
        ):
            self._selected_recipe_idx -= 1
        self._rebuild_toc()
        self.toc_changed.emit()

    def _on_description_changed(self, cat_idx, recipe_idx, text):
        if cat_idx < len(self._categories):
            recipes = self._categories[cat_idx].recipes
            if recipe_idx < len(recipes):
                recipes[recipe_idx]["book_description"] = text
                self.toc_changed.emit()

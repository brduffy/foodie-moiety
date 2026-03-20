"""Tag filter widgets for the recipe list view."""

import platform

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
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

from utils.database import get_all_producers, get_all_tags

_IS_MACOS = platform.system() == "Darwin"


# ---------- Helpers ----------


class _HScrollArea(QScrollArea):
    """QScrollArea that forwards vertical wheel events to horizontal scroll.

    On Windows, mice only produce vertical deltas so we always remap.
    On macOS, the trackpad natively sends horizontal deltas — only remap
    when there is *no* horizontal component (i.e. a plain mouse wheel).
    """

    def wheelEvent(self, event):
        h_bar = self.horizontalScrollBar()
        if h_bar and h_bar.isVisible():
            dx = event.angleDelta().x()
            dy = event.angleDelta().y()
            if _IS_MACOS and dx != 0:
                # Trackpad horizontal swipe — let Qt handle natively
                super().wheelEvent(event)
                return
            # Vertical-only scroll (mouse wheel) — remap to horizontal
            h_bar.setValue(h_bar.value() - dy)
            event.accept()
        else:
            super().wheelEvent(event)


# ---------- Filter Widgets ----------


class FilterTagPill(QFrame):
    """A clickable tag pill for filter selection/display."""

    clicked = Signal(str)  # Emits tag text when clicked
    removed = Signal(str)  # Emits tag text when remove button clicked

    def __init__(
        self,
        text: str,
        selected: bool = False,
        removable: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._selected = selected
        self._removable = removable
        self.setObjectName("FilterTagPill")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10 if not removable else 6, 5)
        layout.setSpacing(4)

        self._label = QLabel(text)
        self._label.setObjectName("FilterTagLabel")
        layout.addWidget(self._label)

        if removable:
            remove_btn = QPushButton("\u2715")
            remove_btn.setObjectName("FilterTagRemoveButton")
            remove_btn.setCursor(Qt.PointingHandCursor)
            remove_btn.clicked.connect(self._on_remove_clicked)
            layout.addWidget(remove_btn)

        # Apply initial styling after label is created
        self._update_style()

    def _update_style(self):
        # Apply styles directly instead of relying on property selectors
        if self._selected:
            self.setStyleSheet("""
                QFrame#FilterTagPill {
                    background-color: #0078d4;
                    border: 1px solid #0078d4;
                    border-radius: 12px;
                }
                QFrame#FilterTagPill:hover {
                    background-color: #1084d8;
                    border-color: #1084d8;
                }
            """)
            if hasattr(self, "_label"):
                self._label.setStyleSheet("color: #ffffff; background: transparent;")
        else:
            self.setStyleSheet("""
                QFrame#FilterTagPill {
                    background-color: #3a3a3a;
                    border: 1px solid #555555;
                    border-radius: 12px;
                }
                QFrame#FilterTagPill:hover {
                    background-color: #444444;
                    border-color: #666666;
                }
            """)
            if hasattr(self, "_label"):
                self._label.setStyleSheet("color: #dddddd; background: transparent;")

    def _on_remove_clicked(self):
        self.removed.emit(self._text)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._text)
        super().mousePressEvent(event)

    @property
    def text(self) -> str:
        return self._text

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()


# ---------- Side Panel ----------


class TagSidePanel(QFrame):
    """Bottom bar overlay showing available tags and producers for filtering.

    Anchored to the bottom of the recipe list view.  An optional producer
    row sits above two horizontally-scrollable tag rows.
    """

    selectionChanged = Signal()  # Emitted when any tag or producer is toggled
    clearAll = Signal()  # Emitted when Clear All clicked

    _ROW_HEIGHT = 30   # Approximate pill + spacing per row
    _PADDING = 30      # Top + bottom margins (includes scrollbar gap)
    _TAG_ROWS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TagSidePanel")
        self._selected_tags: set[str] = set()
        self._selected_producers: set[str] = set()
        self._selected_cuisines: set[str] = set()
        self._pill_map: dict[str, FilterTagPill] = {}
        self._producer_pill_map: dict[str, FilterTagPill] = {}
        self._cuisine_pill_map: dict[str, FilterTagPill] = {}
        self._has_producers = False
        self._has_cuisines = False
        self._external_tags: list[str] | None = None
        self._external_producers: list[str] | None = None
        self._external_cuisines: list[str] | None = None
        self._update_panel_height()

        self.setStyleSheet("""
            QFrame#TagSidePanel {
                background-color: #1e1e1e;
                border-top: 1px solid #333333;
            }
            QLabel#FilterTagLabel {
                color: #dddddd;
                font-size: 12px;
                background: transparent;
            }
            QPushButton#ClearAllButton {
                background: transparent;
                color: #888888;
                border: none;
                font-size: 12px;
                padding: 4px 8px;
            }
            QPushButton#ClearAllButton:hover {
                color: #ffffff;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:horizontal {
                height: 6px;
                margin-top: 8px;
                background-color: transparent;
            }
            QScrollBar::handle:horizontal {
                background-color: #555555;
                border-radius: 3px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #777777;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
        """)

        # Main row: [scrollable rows] [clear]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 16)
        layout.setSpacing(10)

        # Horizontally scrollable area with optional producer row + two tag rows
        self._scroll_area = _HScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self._tags_container = QWidget()
        self._tags_container.setStyleSheet("background: transparent;")
        tags_vlayout = QVBoxLayout(self._tags_container)
        tags_vlayout.setContentsMargins(0, 0, 0, 0)
        tags_vlayout.setSpacing(6)

        self._producer_row = QHBoxLayout()
        self._producer_row.setSpacing(6)
        self._cuisine_row = QHBoxLayout()
        self._cuisine_row.setSpacing(6)
        self._row1 = QHBoxLayout()
        self._row1.setSpacing(6)
        self._row2 = QHBoxLayout()
        self._row2.setSpacing(6)
        tags_vlayout.addLayout(self._producer_row)
        tags_vlayout.addLayout(self._cuisine_row)
        tags_vlayout.addLayout(self._row1)
        tags_vlayout.addLayout(self._row2)

        self._scroll_area.setWidget(self._tags_container)
        layout.addWidget(self._scroll_area, stretch=1)

        # Buttons to the right
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)

        self._clear_btn = QPushButton("Clear All")
        self._clear_btn.setObjectName("ClearAllButton")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.clicked.connect(self.clearAll.emit)
        self._clear_btn.hide()
        btn_col.addWidget(self._clear_btn)

        layout.addLayout(btn_col)

        self.hide()

    def refresh_tags(self):
        """Reload available tags (from DB or external source) and producers."""
        # If external tags are set (community mode), use those instead of DB
        if self._external_tags is not None:
            self.set_external_tags(
                self._external_tags,
                producers=self._external_producers,
                cuisines=self._external_cuisines,
            )
            return

        # Clear all rows
        for row in (self._producer_row, self._cuisine_row, self._row1, self._row2):
            while row.count():
                item = row.takeAt(0)
                w = item.widget()
                if w:
                    w.hide()
                    w.deleteLater()
        self._pill_map.clear()
        self._producer_pill_map.clear()
        self._cuisine_pill_map.clear()

        # --- Producers ---
        all_producers = get_all_producers()
        self._has_producers = len(all_producers) >= 2
        if self._has_producers:
            selected_p = sorted(
                [p for p in all_producers if p in self._selected_producers],
                key=str.lower,
            )
            unselected_p = [p for p in all_producers if p not in self._selected_producers]
            for producer in selected_p + unselected_p:
                pill = FilterTagPill(producer, selected=producer in self._selected_producers)
                pill.clicked.connect(self._on_producer_clicked)
                self._producer_row.addWidget(pill)
                self._producer_pill_map[producer] = pill
            self._producer_row.addStretch()

        # --- Cuisines (library mode — from DB) ---
        from utils.database import get_all_cuisines
        all_cuisines = get_all_cuisines()
        self._has_cuisines = len(all_cuisines) >= 2
        if self._has_cuisines:
            selected_c = sorted(
                [c for c in all_cuisines if c in self._selected_cuisines],
                key=str.lower,
            )
            unselected_c = [c for c in all_cuisines if c not in self._selected_cuisines]
            for cuisine in selected_c + unselected_c:
                pill = FilterTagPill(cuisine, selected=cuisine in self._selected_cuisines)
                pill.clicked.connect(self._on_cuisine_clicked)
                self._cuisine_row.addWidget(pill)
                self._cuisine_pill_map[cuisine] = pill
            self._cuisine_row.addStretch()

        # --- Tags ---
        all_tags = get_all_tags()
        selected = sorted(
            [t for t in all_tags if t in self._selected_tags], key=str.lower
        )
        unselected = [t for t in all_tags if t not in self._selected_tags]
        ordered = selected + unselected

        half = (len(ordered) + 1) // 2
        for i, tag in enumerate(ordered):
            pill = FilterTagPill(tag, selected=tag in self._selected_tags)
            pill.clicked.connect(self._on_tag_clicked)
            if i < half:
                self._row1.addWidget(pill)
            else:
                self._row2.addWidget(pill)
            self._pill_map[tag] = pill
        self._row1.addStretch()
        self._row2.addStretch()

        # Update panel height and resize container
        self._update_panel_height()
        QTimer.singleShot(0, self._resize_tags_container)

    def _update_panel_height(self):
        """Set panel height based on whether producers/cuisines are shown."""
        rows = (self._TAG_ROWS
                + (1 if self._has_producers else 0)
                + (1 if self._has_cuisines else 0))
        # row_height(30) * rows + spacing(6) * (rows-1) + padding(16)
        h = self._ROW_HEIGHT * rows + 6 * (rows - 1) + self._PADDING
        self.setFixedHeight(h)

    def panel_height(self) -> int:
        """Return current panel height (for overlay positioning)."""
        return self.height()

    def _resize_tags_container(self):
        """Set the tags container width so all pills are visible and scrollable."""
        widths = [self._row_width(r) for r in
                  (self._producer_row, self._cuisine_row, self._row1, self._row2)]
        self._tags_container.setMinimumWidth(max(widths))

    @staticmethod
    def _row_width(row_layout: QHBoxLayout) -> int:
        """Calculate the total width needed by a row of pills."""
        total = 0
        for i in range(row_layout.count()):
            item = row_layout.itemAt(i)
            if item and item.widget():
                total += item.widget().sizeHint().width()
        spacing = row_layout.spacing() * max(0, row_layout.count() - 1)
        return total + spacing

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def set_selected_tags(self, tags: list[str]):
        """Set which tags appear selected and scroll to show them."""
        self._selected_tags = set(tags)
        self.refresh_tags()
        self._update_clear_btn()
        if self._selected_tags:
            QTimer.singleShot(0, self._smooth_scroll_to_start)

    def get_selected_tags(self) -> list[str]:
        """Return the currently selected tag names."""
        return list(self._selected_tags)

    def _on_tag_clicked(self, tag_text: str):
        """Handle clicking a tag - toggle selection and apply immediately."""
        if tag_text in self._selected_tags:
            self._selected_tags.discard(tag_text)
        else:
            self._selected_tags.add(tag_text)
        self.refresh_tags()
        self._update_clear_btn()
        if self._selected_tags:
            QTimer.singleShot(0, self._smooth_scroll_to_start)
        self.selectionChanged.emit()

    # ------------------------------------------------------------------
    # Producers
    # ------------------------------------------------------------------

    def set_selected_producers(self, producers: list[str]):
        """Set which producers appear selected."""
        self._selected_producers = set(producers)
        self.refresh_tags()
        self._update_clear_btn()
        if self._selected_producers:
            QTimer.singleShot(0, self._smooth_scroll_to_start)

    def get_selected_producers(self) -> list[str]:
        """Return the currently selected producer names."""
        return list(self._selected_producers)

    def _on_producer_clicked(self, producer_text: str):
        """Handle clicking a producer pill - toggle selection."""
        if producer_text in self._selected_producers:
            self._selected_producers.discard(producer_text)
        else:
            self._selected_producers.add(producer_text)
        self.refresh_tags()
        self._update_clear_btn()
        if self._selected_producers:
            QTimer.singleShot(0, self._smooth_scroll_to_start)
        self.selectionChanged.emit()

    # ------------------------------------------------------------------
    # Cuisines
    # ------------------------------------------------------------------

    def set_selected_cuisines(self, cuisines: list[str]):
        """Set which cuisines appear selected."""
        self._selected_cuisines = set(cuisines)
        self.refresh_tags()
        self._update_clear_btn()
        if self._selected_cuisines:
            QTimer.singleShot(0, self._smooth_scroll_to_start)

    def get_selected_cuisines(self) -> list[str]:
        """Return the currently selected cuisine names."""
        return list(self._selected_cuisines)

    def _on_cuisine_clicked(self, cuisine_text: str):
        """Handle clicking a cuisine pill - toggle selection."""
        if cuisine_text in self._selected_cuisines:
            self._selected_cuisines.discard(cuisine_text)
        else:
            self._selected_cuisines.add(cuisine_text)
        self.refresh_tags()
        self._update_clear_btn()
        if self._selected_cuisines:
            QTimer.singleShot(0, self._smooth_scroll_to_start)
        self.selectionChanged.emit()

    # ------------------------------------------------------------------
    # Batch selection
    # ------------------------------------------------------------------

    def set_selections(self, tags: list[str], producers: list[str], cuisines: list[str] | None = None):
        """Batch-set tag, producer, and cuisine selections with a single refresh."""
        self._selected_tags = set(tags)
        self._selected_producers = set(producers)
        if cuisines is not None:
            self._selected_cuisines = set(cuisines)
        self.refresh_tags()
        self._update_clear_btn()
        if self._selected_tags or self._selected_producers or self._selected_cuisines:
            QTimer.singleShot(0, self._smooth_scroll_to_start)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def update_count(self, shown_count: int, total_count: int):
        """Update the recipe count display (no-op for bottom bar)."""
        pass

    def _update_clear_btn(self):
        """Show Clear All when any filter is active."""
        self._clear_btn.setVisible(
            bool(self._selected_tags or self._selected_producers
                 or self._selected_cuisines)
        )

    def clear_external_tags(self):
        """Clear external tags, reverting to local DB queries."""
        self._external_tags = None
        self._external_producers = None
        self._external_cuisines = None

    def set_external_tags(self, tags: list[str],
                          producers: list[str] | None = None,
                          cuisines: list[str] | None = None):
        """Populate the panel with externally provided tags (e.g. from API).

        Bypasses the local database query. Optional producer and cuisine rows
        are shown when the respective list has 2+ entries.
        """
        self._external_tags = tags
        self._external_producers = producers
        self._external_cuisines = cuisines
        for row in (self._producer_row, self._cuisine_row, self._row1, self._row2):
            while row.count():
                item = row.takeAt(0)
                w = item.widget()
                if w:
                    w.hide()
                    w.deleteLater()
        self._pill_map.clear()
        self._producer_pill_map.clear()
        self._cuisine_pill_map.clear()

        # --- Producers ---
        self._has_producers = bool(producers and len(producers) >= 2)
        if self._has_producers:
            selected_p = sorted(
                [p for p in producers if p in self._selected_producers],
                key=str.lower,
            )
            unselected_p = [p for p in producers if p not in self._selected_producers]
            for producer in selected_p + unselected_p:
                pill = FilterTagPill(producer, selected=producer in self._selected_producers)
                pill.clicked.connect(self._on_producer_clicked)
                self._producer_row.addWidget(pill)
                self._producer_pill_map[producer] = pill
            self._producer_row.addStretch()

        # --- Cuisines ---
        self._has_cuisines = bool(cuisines and len(cuisines) >= 2)
        if self._has_cuisines:
            selected_c = sorted(
                [c for c in cuisines if c in self._selected_cuisines],
                key=str.lower,
            )
            unselected_c = [c for c in cuisines if c not in self._selected_cuisines]
            for cuisine in selected_c + unselected_c:
                pill = FilterTagPill(cuisine, selected=cuisine in self._selected_cuisines)
                pill.clicked.connect(self._on_cuisine_clicked)
                self._cuisine_row.addWidget(pill)
                self._cuisine_pill_map[cuisine] = pill
            self._cuisine_row.addStretch()

        # --- Tags ---
        selected = sorted(
            [t for t in tags if t in self._selected_tags], key=str.lower
        )
        unselected = sorted(
            [t for t in tags if t not in self._selected_tags], key=str.lower
        )
        ordered = selected + unselected

        half = (len(ordered) + 1) // 2
        for i, tag in enumerate(ordered):
            pill = FilterTagPill(tag, selected=tag in self._selected_tags)
            pill.clicked.connect(self._on_tag_clicked)
            if i < half:
                self._row1.addWidget(pill)
            else:
                self._row2.addWidget(pill)
            self._pill_map[tag] = pill
        self._row1.addStretch()
        self._row2.addStretch()

        self._update_panel_height()
        QTimer.singleShot(0, self._resize_tags_container)

    def _smooth_scroll_to_start(self):
        """Animate horizontal scroll back to x=0."""
        h_bar = self._scroll_area.horizontalScrollBar()
        if h_bar.value() == 0:
            return
        self._scroll_anim = QPropertyAnimation(h_bar, b"value")
        self._scroll_anim.setDuration(400)
        self._scroll_anim.setStartValue(h_bar.value())
        self._scroll_anim.setEndValue(0)
        self._scroll_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._scroll_anim.start()

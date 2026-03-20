"""Ingredient list editor widget - structured ingredient entry with add/remove rows."""

from fractions import Fraction

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class AutoResizeTextEdit(QTextEdit):
    """A QTextEdit that auto-resizes its height to fit content."""

    textModified = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText("")
        self.setAcceptRichText(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setFrameShape(QFrame.NoFrame)
        self.document().setDocumentMargin(5)
        if text:
            self.setPlainText(text)
        self.document().contentsChanged.connect(self._update_height)
        self.document().contentsChanged.connect(self.textModified.emit)
        self._update_height()

    def _update_height(self):
        doc_height = self.document().size().toSize().height()
        self.setFixedHeight(doc_height)
        # Also resize the wrapper container if present
        wrapper = self.parent()
        if wrapper and isinstance(wrapper, NameFieldWrapper):
            wrapper.setFixedHeight(doc_height + 2)  # +2 for 1px border top+bottom

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_height()

    def text(self):
        """Compatibility with QLineEdit API."""
        return self.toPlainText()

    def setReadOnly(self, read_only):
        super().setReadOnly(read_only)
        # Update wrapper object name so stylesheet can target read-only state
        wrapper = self.parent()
        if wrapper and isinstance(wrapper, NameFieldWrapper):
            wrapper.setObjectName("NameFieldReadOnly" if read_only else "NameField")
            wrapper.style().unpolish(wrapper)
            wrapper.style().polish(wrapper)
        self._update_height()


class NameFieldWrapper(QFrame):
    """Container frame for AutoResizeTextEdit that provides cross-platform border styling.

    QTextEdit on Windows ignores stylesheet border/border-radius. Wrapping it in
    a QFrame lets the stylesheet reliably control the border appearance on all platforms.
    """

    def __init__(self, text_edit, parent=None):
        super().__init__(parent)
        self.setObjectName("NameField")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(text_edit)


class IngredientRow(QWidget):
    """A single ingredient row with quantity, unit, and name fields."""

    removed = Signal(object)  # Emits self when delete is clicked
    changed = Signal()  # Emits when any field changes

    def __init__(self, quantity="", unit="", item_name="", parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(6)

        # Quantity field (narrow)
        self.quantity_field = QLineEdit(quantity)
        self.quantity_field.setPlaceholderText("Qty")
        self.quantity_field.setFixedWidth(60)
        self.quantity_field.textChanged.connect(self.changed.emit)
        layout.addWidget(self.quantity_field, alignment=Qt.AlignTop)

        # Unit field (medium)
        self.unit_field = QLineEdit(unit)
        self.unit_field.setPlaceholderText("Unit")
        self.unit_field.setFixedWidth(138)
        self.unit_field.textChanged.connect(self.changed.emit)
        layout.addWidget(self.unit_field, alignment=Qt.AlignTop)

        # Item name field (stretches, wraps text)
        # Wrapped in NameFieldWrapper for cross-platform border styling
        self.name_field = AutoResizeTextEdit(item_name)
        self.name_field.setPlaceholderText("Ingredient name")
        self.name_field.textModified.connect(self.changed.emit)
        self.name_wrapper = NameFieldWrapper(self.name_field)
        layout.addWidget(self.name_wrapper, alignment=Qt.AlignTop)

        # Delete button
        self.delete_btn = QPushButton("x")
        self.delete_btn.setObjectName("DeleteBtn")
        self.delete_btn.setFixedSize(28, 28)
        self.delete_btn.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(self.delete_btn, alignment=Qt.AlignTop)

    def get_data(self):
        """Return ingredient data as a dict."""
        return {
            "quantity": self.quantity_field.text().strip(),
            "unit": self.unit_field.text().strip(),
            "item_name": self.name_field.text().strip(),
        }

    def set_read_only(self, read_only):
        """Toggle fields between editable and read-only."""
        self.quantity_field.setReadOnly(read_only)
        self.unit_field.setReadOnly(read_only)
        self.name_field.setReadOnly(read_only)
        self.delete_btn.setVisible(not read_only)


class IngredientListEditor(QWidget):
    """
    Structured ingredient list editor with add/remove rows.

    Each row contains quantity, unit, and ingredient name fields.
    Visually matches the RichTextEditor dark theme.
    """

    dataChanged = Signal()
    aggregateRequested = Signal()
    addToGroceryListRequested = Signal()

    def __init__(self, title="", parent=None):
        super().__init__(parent)

        self._read_only = False
        self._grocery_btn_enabled = False  # Only show on intro step
        self._font_size = 14  # Default font size in pixels

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Title row (title + aggregate button) ---
        if title:
            title_row = QWidget()
            title_row_layout = QHBoxLayout(title_row)
            title_row_layout.setContentsMargins(0, 0, 4, 0)
            title_row_layout.setSpacing(8)

            self.title_label = QLabel(title)
            self.title_label.setObjectName("IngredientTitle")
            self.title_label.setStyleSheet("""
                QLabel#IngredientTitle {
                    background-color: transparent;
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 6px 8px;
                }
            """)
            title_row_layout.addWidget(self.title_label)
            title_row_layout.addStretch()

            # Aggregate button (visible only on intro step in edit mode)
            self.aggregate_btn = QPushButton("⟳  Aggregate from steps")
            self.aggregate_btn.setObjectName("AggregateBtn")
            self.aggregate_btn.setFixedHeight(28)
            self.aggregate_btn.clicked.connect(self.aggregateRequested.emit)
            self.aggregate_btn.hide()
            title_row_layout.addWidget(self.aggregate_btn)

            # Send-to-phone button (visible only in read-only mode)
            self.add_to_list_btn = QPushButton("Add to Grocery List")
            self.add_to_list_btn.setObjectName("AddToListBtn")
            self.add_to_list_btn.setFixedHeight(28)
            self.add_to_list_btn.clicked.connect(self.addToGroceryListRequested.emit)
            self.add_to_list_btn.hide()
            title_row_layout.addWidget(self.add_to_list_btn)

            layout.addWidget(title_row)
        else:
            # No title — still create buttons (hidden by default)
            self.aggregate_btn = QPushButton("⟳  Aggregate from steps")
            self.aggregate_btn.setObjectName("AggregateBtn")
            self.aggregate_btn.setFixedHeight(28)
            self.aggregate_btn.clicked.connect(self.aggregateRequested.emit)
            self.aggregate_btn.hide()
            self.add_to_list_btn = QPushButton("Add to Grocery List")
            self.add_to_list_btn.setObjectName("AddToListBtn")
            self.add_to_list_btn.setFixedHeight(28)
            self.add_to_list_btn.clicked.connect(self.addToGroceryListRequested.emit)
            self.add_to_list_btn.hide()

        # --- Header bar (matches RichTextEditor toolbar) ---
        self.header = QWidget()
        self.header.setObjectName("IngredientHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        # Column labels
        qty_label = QPushButton("Qty")
        qty_label.setObjectName("ColumnLabel")
        qty_label.setFixedWidth(60)
        qty_label.setEnabled(False)
        header_layout.addWidget(qty_label)

        unit_label = QPushButton("Unit")
        unit_label.setObjectName("ColumnLabel")
        unit_label.setFixedWidth(138)
        unit_label.setEnabled(False)
        header_layout.addWidget(unit_label)

        name_label = QPushButton("Ingredient")
        name_label.setObjectName("ColumnLabel")
        name_label.setEnabled(False)
        header_layout.addWidget(name_label)

        header_layout.addStretch()

        # Add button
        self.add_btn = QPushButton("+  Add")
        self.add_btn.setObjectName("AddBtn")
        self.add_btn.setFixedHeight(28)
        self.add_btn.clicked.connect(self._add_empty_row)
        header_layout.addWidget(self.add_btn)

        layout.addWidget(self.header)

        # --- Scrollable row area ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setObjectName("IngredientScrollArea")

        self.row_container = QWidget()
        self.row_layout = QVBoxLayout(self.row_container)
        self.row_layout.setContentsMargins(6, 6, 6, 6)
        self.row_layout.setSpacing(2)
        self.row_layout.addStretch()

        self.scroll_area.setWidget(self.row_container)
        layout.addWidget(self.scroll_area)

        self.rows = []

        # Apply dark theme
        self.setStyleSheet(self._widget_style())

    def _widget_style(self):
        """Return the full widget stylesheet matching RichTextEditor theme."""
        return """
            QWidget#IngredientHeader {
                background-color: transparent;
                border-bottom: 1px solid #555555;
            }
            QPushButton#ColumnLabel {
                background-color: transparent;
                color: #888888;
                border: none;
                font-size: 11px;
                font-weight: bold;
                text-align: left;
                padding: 2px 4px;
            }
            QPushButton#AddBtn {
                background-color: #2a2a2a;
                color: #cccccc;
                border: 1px solid #444444;
                border-radius: 4px;
                font-size: 12px;
                padding: 2px 12px;
            }
            QPushButton#AddBtn:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton#AggregateBtn {
                background-color: #1a3a5c;
                color: #aaccee;
                border: 1px solid #2a5a8a;
                border-radius: 4px;
                font-size: 12px;
                padding: 2px 12px;
            }
            QPushButton#AggregateBtn:hover {
                background-color: #2a5a8a;
                color: white;
            }
            QPushButton#AddToListBtn {
                background-color: #1a3a5c;
                color: #aaccee;
                border: 1px solid #2a5a8a;
                border-radius: 4px;
                font-size: 12px;
                padding: 2px 12px;
            }
            QPushButton#AddToListBtn:hover {
                background-color: #2a5a8a;
                color: white;
            }
            QScrollArea#IngredientScrollArea {
                background-color: transparent;
                border: 1px solid #555555;
            }
            QWidget {
                background-color: transparent;
            }
            QLineEdit {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 4px 6px;
                selection-background-color: #0078d4;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
            QLineEdit:read-only {
                background-color: transparent;
                border: none;
            }
            QFrame#NameField {
                background-color: #1e1e1e;
                border: 1px solid #444444;
                border-radius: 4px;
            }
            QFrame#NameField:focus-within {
                border-color: #0078d4;
            }
            QFrame#NameFieldReadOnly {
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }
            AutoResizeTextEdit {
                background-color: transparent;
                color: #e0e0e0;
                padding: 0px;
                selection-background-color: #0078d4;
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
            QMenu {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #444444;
            }
            QMenu::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """

    def _add_empty_row(self):
        """Add an empty ingredient row."""
        self.add_ingredient()

    def add_ingredient(self, quantity="", unit="", item_name=""):
        """Add an ingredient row with optional pre-filled data."""
        row = IngredientRow(quantity, unit, item_name)
        row.removed.connect(self._remove_row)
        row.changed.connect(self.dataChanged.emit)
        row.set_read_only(self._read_only)
        self._apply_font_size_to_row(row)

        # Insert before the stretch
        self.row_layout.insertWidget(len(self.rows), row)
        self.rows.append(row)
        self.dataChanged.emit()
        return row

    def _remove_row(self, row):
        """Remove an ingredient row."""
        if row in self.rows:
            self.rows.remove(row)
            self.row_layout.removeWidget(row)
            row.deleteLater()
            self.dataChanged.emit()

    def clear(self):
        """Remove all ingredient rows."""
        for row in self.rows[:]:
            self.row_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()

    def get_ingredients(self):
        """Return list of ingredient dicts with non-empty names."""
        return [
            row.get_data()
            for row in self.rows
            if row.get_data()["item_name"]
        ]

    @staticmethod
    def _format_quantity(value):
        """Format a numeric quantity as a fraction string for display.

        Returns friendly fractions (e.g. 0.5 → "1/2", 1.5 → "1 1/2").
        Denominators are capped at 16 to keep values kitchen-friendly.
        """
        if not value:
            return ""
        try:
            frac = Fraction(value).limit_denominator(16)
        except (ValueError, TypeError):
            return str(value)
        if frac == 0:
            return ""
        whole = int(frac)
        remainder = frac - whole
        if remainder == 0:
            return str(whole)
        if whole == 0:
            return str(remainder)
        return f"{whole} {remainder}"

    def set_ingredients(self, ingredients):
        """Load a list of ingredient dicts into the editor.

        Args:
            ingredients: List of dicts with keys: quantity, unit, item_name
        """
        self.clear()
        for ing in ingredients:
            self.add_ingredient(
                quantity=self._format_quantity(ing.get("quantity", "")),
                unit=ing.get("unit", ""),
                item_name=ing.get("item_name", ""),
            )
        self._update_grocery_btn()

    def set_read_only(self, read_only):
        """Toggle between edit and read-only mode."""
        self._read_only = read_only
        self.add_btn.setVisible(not read_only)
        if read_only:
            self.aggregate_btn.hide()
        self._update_grocery_btn()
        for row in self.rows:
            row.set_read_only(read_only)

    def set_aggregate_visible(self, visible):
        """Show or hide the 'Aggregate from steps' button."""
        self.aggregate_btn.setVisible(visible)

    def set_grocery_btn_visible(self, visible):
        """Enable or disable the grocery list button (e.g. only on intro step)."""
        self._grocery_btn_enabled = visible
        self._update_grocery_btn()

    def _update_grocery_btn(self):
        self.add_to_list_btn.setVisible(
            self._grocery_btn_enabled and self._read_only and len(self.rows) > 0
        )

    def _apply_font_size_to_row(self, row):
        """Apply the current font size to all fields in a row."""
        font = QFont()
        font.setPixelSize(self._font_size)
        for widget in (row.quantity_field, row.unit_field, row.name_field):
            widget.setFont(font)
        row.name_field._update_height()

    def scroll_by_page(self, direction: str):
        """Smooth-scroll the ingredient list by one viewport page.

        Args:
            direction: "down" or "up".
        """
        v_bar = self.scroll_area.verticalScrollBar()
        page = self.scroll_area.viewport().height()
        target = v_bar.value() + (page if direction == "down" else -page)
        target = max(v_bar.minimum(), min(target, v_bar.maximum()))
        self._scroll_anim = QPropertyAnimation(v_bar, b"value")
        self._scroll_anim.setDuration(300)
        self._scroll_anim.setStartValue(v_bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_anim.start()

    def adjust_font_size(self, delta):
        """Adjust the font size of all ingredient fields by delta pixels.

        Args:
            delta: Number of pixels to add (positive) or subtract (negative).
        """
        self._font_size = max(14, min(24, self._font_size + delta))
        for row in self.rows:
            self._apply_font_size_to_row(row)

"""Tags editor widget - displays and manages recipe tags."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from utils.database import (
    create_tag,
    delete_tag,
    get_all_tags,
    get_canonical_tags,
    get_tag_usage_count,
    is_canonical_tag,
    rename_tag,
)
from utils.helpers import DIALOG_STYLE, white_question_icon

# Backward-compatible aliases
_DIALOG_STYLE = DIALOG_STYLE
_white_question_icon = white_question_icon


class TagPill(QFrame):
    """A clickable tag displayed as a pill/chip."""

    clicked = Signal(str)  # Emits the tag text when clicked
    removed = Signal(str)  # Emits the tag text when remove button clicked

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
        self.setObjectName("TagPill")
        self.setCursor(Qt.PointingHandCursor)
        # Prevent vertical expansion - maintain natural pill height
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._update_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10 if not removable else 6, 6)
        layout.setSpacing(4)

        label = QLabel(text)
        label.setObjectName("TagLabel")
        layout.addWidget(label)

        if removable:
            remove_btn = QPushButton("\u2715")
            remove_btn.setObjectName("TagRemoveButton")
            remove_btn.setCursor(Qt.PointingHandCursor)
            remove_btn.clicked.connect(self._on_remove_clicked)
            layout.addWidget(remove_btn)

    def _update_style(self):
        if self._selected:
            self.setProperty("selected", True)
        else:
            self.setProperty("selected", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def _on_remove_clicked(self):
        self.removed.emit(self._text)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._removable:
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


class TagsEditor(QWidget):
    """Editor widget for managing recipe tags.

    Shows current recipe tags and allows selecting from available tags
    or creating new ones.
    """

    tagsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TagsEditor")
        self._recipe_tags: list[str] = []
        self._available_tags: list[str] = []
        self._editing = False

        self.setStyleSheet("""
            QWidget#TagsEditor {
                background: transparent;
            }
            QWidget#TagsContainer {
                background: transparent;
                border: none;
            }
            QScrollArea#TagsScrollArea {
                background: transparent;
                border: 1px solid #555555;
            }
            QScrollArea#TagsScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QLabel#SectionTitle {
                color: #ffffff;
                font-size: 14px;
                font-weight: 600;
                padding: 4px 0;
                background: transparent;
            }
            QLabel#TagLabel {
                color: #dddddd;
                font-size: 13px;
                background: transparent;
            }
            QFrame#TagPill {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 14px;
            }
            QFrame#TagPill:hover {
                background-color: #444444;
                border-color: #666666;
            }
            QFrame#TagPill[selected="true"] {
                background-color: #0078d4;
                border-color: #0078d4;
            }
            QFrame#TagPill[selected="true"]:hover {
                background-color: #1084d8;
                border-color: #1084d8;
            }
            QFrame#TagPill[userDefined="true"] {
                background-color: #2a4a2a;
                border-color: #4a8a4a;
            }
            QFrame#TagPill[userDefined="true"]:hover {
                background-color: #3a5a3a;
                border-color: #5a9a5a;
            }
            QPushButton#TagRemoveButton {
                background: transparent;
                color: #ffffff;
                border: none;
                font-size: 14px;
                font-weight: bold;
                padding: 0px;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
            }
            QPushButton#TagRemoveButton:hover {
                color: #ff6666;
            }
            QPushButton#CreateTagButton {
                background-color: #3a3a3a;
                color: #cccccc;
                border: 1px dashed #555555;
                border-radius: 14px;
                padding: 6px 14px;
                font-size: 13px;
            }
            QPushButton#CreateTagButton:hover {
                background-color: #444444;
                color: #ffffff;
                border-color: #666666;
            }
            QLineEdit#NewTagInput {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLineEdit#NewTagInput:focus {
                border-color: #0078d4;
            }
            QScrollArea {
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
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        # Section 1: Current recipe tags
        current_label = QLabel("Recipe Tags")
        current_label.setObjectName("SectionTitle")
        main_layout.addWidget(current_label)

        # Scrollable area for current tags (fixed height for ~2 rows)
        current_scroll = QScrollArea()
        current_scroll.setObjectName("TagsScrollArea")
        current_scroll.setWidgetResizable(True)
        current_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        current_scroll.setFixedHeight(80)

        self._current_tags_container = QWidget()
        self._current_tags_container.setObjectName("TagsContainer")
        self._current_tags_layout = FlowLayout(self._current_tags_container)
        self._current_tags_layout.setSpacing(8)
        current_scroll.setWidget(self._current_tags_container)
        main_layout.addWidget(current_scroll)

        # Section 2: Available tags
        available_label = QLabel("Available Tags")
        available_label.setObjectName("SectionTitle")
        main_layout.addWidget(available_label)

        # Scrollable area for available tags
        scroll = QScrollArea()
        scroll.setObjectName("TagsScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._available_tags_container = QWidget()
        self._available_tags_container.setObjectName("TagsContainer")
        self._available_tags_layout = FlowLayout(self._available_tags_container)
        self._available_tags_layout.setSpacing(8)
        scroll.setWidget(self._available_tags_container)
        main_layout.addWidget(scroll, stretch=1)

        # Section 3: Create new tag
        create_row = QHBoxLayout()
        create_row.setSpacing(8)

        self._create_btn = QPushButton("+ Create New Tag")
        self._create_btn.setObjectName("CreateTagButton")
        self._create_btn.setCursor(Qt.PointingHandCursor)
        self._create_btn.clicked.connect(self._on_create_clicked)
        create_row.addWidget(self._create_btn)

        self._new_tag_input = QLineEdit()
        self._new_tag_input.setObjectName("NewTagInput")
        self._new_tag_input.setPlaceholderText("Enter new tag name...")
        self._new_tag_input.returnPressed.connect(self._on_new_tag_submitted)
        self._new_tag_input.hide()
        create_row.addWidget(self._new_tag_input)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("CreateTagButton")
        self._cancel_btn.clicked.connect(self._on_create_cancelled)
        self._cancel_btn.hide()
        create_row.addWidget(self._cancel_btn)

        create_row.addStretch()
        main_layout.addLayout(create_row)

        self._hint_label = QLabel("User defined tags (green) can be right-clicked to rename / delete")
        self._hint_label.setStyleSheet("color: #cccc00; font-size: 11px; font-style: italic; background: transparent;")
        self._hint_label.hide()
        main_layout.addWidget(self._hint_label)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_read_only(self, read_only: bool):
        """Toggle between view mode (read-only) and edit mode."""
        self._editing = not read_only
        self._create_btn.setVisible(not read_only)
        self._hint_label.setVisible(not read_only)
        self._new_tag_input.hide()
        self._cancel_btn.hide()
        self._rebuild_tags()

    def set_tags(self, recipe_tags: list[str]):
        """Set the current recipe's tags."""
        self._recipe_tags = list(recipe_tags) if recipe_tags else []
        self._rebuild_tags()

    def get_tags(self) -> list[str]:
        """Return the current recipe tags."""
        return list(self._recipe_tags)

    def refresh_available_tags(self):
        """Reload available tags from the database."""
        self._available_tags = get_all_tags()
        self._rebuild_tags()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild_tags(self):
        """Rebuild both current and available tag displays."""
        # Clear current tags
        self._clear_layout(self._current_tags_layout)

        # Add current recipe tags (removable in edit mode)
        if self._recipe_tags:
            for tag in sorted(self._recipe_tags, key=str.lower):
                pill = TagPill(tag, selected=True, removable=self._editing)
                pill.removed.connect(self._on_tag_removed)
                self._current_tags_layout.addFlowWidget(pill)
        else:
            placeholder = QLabel("No tags assigned" if not self._editing else "Click tags below to add")
            placeholder.setStyleSheet("color: #888888; font-style: italic;")
            self._current_tags_layout.addFlowWidget(placeholder)

        # Clear available tags
        self._clear_layout(self._available_tags_layout)

        # Add available tags (excluding current recipe tags)
        available = [t for t in self._available_tags if t not in self._recipe_tags]
        canonical = get_canonical_tags()
        if available:
            for tag in sorted(available, key=str.lower):
                pill = TagPill(tag, selected=False, removable=False)
                if tag not in canonical:
                    pill.setProperty("userDefined", True)
                    pill.style().unpolish(pill)
                    pill.style().polish(pill)
                if self._editing:
                    pill.clicked.connect(self._on_available_tag_clicked)
                    if tag not in canonical:
                        pill.setContextMenuPolicy(Qt.CustomContextMenu)
                        pill.customContextMenuRequested.connect(
                            lambda pos, t=tag, p=pill: self._show_tag_context_menu(t, p, pos)
                        )
                else:
                    pill.setCursor(Qt.ArrowCursor)
                self._available_tags_layout.addFlowWidget(pill)
        else:
            placeholder = QLabel("All tags are assigned" if self._recipe_tags else "No tags defined yet")
            placeholder.setStyleSheet("color: #888888; font-style: italic;")
            self._available_tags_layout.addFlowWidget(placeholder)

        # Force containers to recalculate geometry after rebuild
        self._current_tags_container.updateGeometry()
        self._available_tags_container.updateGeometry()

    def _clear_layout(self, layout):
        """Remove all widgets from a layout."""
        if isinstance(layout, FlowLayout):
            layout.clearFlow()
        else:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

    def _on_tag_removed(self, tag_text: str):
        """Handle removing a tag from the recipe."""
        if tag_text in self._recipe_tags:
            self._recipe_tags.remove(tag_text)
            self._rebuild_tags()
            self.tagsChanged.emit()

    def _on_available_tag_clicked(self, tag_text: str):
        """Handle clicking an available tag to add it."""
        if tag_text not in self._recipe_tags:
            self._recipe_tags.append(tag_text)
            self._rebuild_tags()
            self.tagsChanged.emit()

    def _show_tag_context_menu(self, tag_text: str, pill: TagPill, pos):
        """Show rename/delete context menu for an available tag pill."""
        canonical = is_canonical_tag(tag_text)
        if canonical:
            return  # Canonical tags cannot be renamed or deleted
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a2a;
                color: #dddddd;
                border: 1px solid #555555;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
            }
            QMenu::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        action = menu.exec(pill.mapToGlobal(pos))
        if action == rename_action:
            self._rename_tag(tag_text)
        elif action == delete_action:
            self._delete_tag(tag_text)

    def _rename_tag(self, old_name: str):
        """Prompt for a new name and rename the tag."""
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Rename Tag")
        dlg.setLabelText(f'Rename "{old_name}" to:')
        dlg.setTextValue(old_name)
        dlg.setStyleSheet(_DIALOG_STYLE)
        ok = dlg.exec()
        new_name = dlg.textValue()
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == old_name:
            return
        if new_name.lower() in [t.lower() for t in self._available_tags]:
            w = QMessageBox(QMessageBox.Warning, "Tag Exists",
                            f'A tag named "{new_name}" already exists.', QMessageBox.Ok, self)
            w.setStyleSheet(_DIALOG_STYLE)
            w.exec()
            return
        if not rename_tag(old_name, new_name):
            w = QMessageBox(QMessageBox.Warning, "Rename Failed",
                            f'Could not rename "{old_name}".', QMessageBox.Ok, self)
            w.setStyleSheet(_DIALOG_STYLE)
            w.exec()
            return
        # Update local state
        idx = self._available_tags.index(old_name)
        self._available_tags[idx] = new_name
        # Update recipe tags if this tag was on the current recipe
        if old_name in self._recipe_tags:
            ridx = self._recipe_tags.index(old_name)
            self._recipe_tags[ridx] = new_name
            self.tagsChanged.emit()
        self._rebuild_tags()

    def _delete_tag(self, tag_name: str):
        """Confirm and delete a tag globally."""
        count = get_tag_usage_count(tag_name)
        if count > 0:
            msg = (
                f'Delete "{tag_name}"?\n\n'
                f"This tag is used on {count} recipe{'s' if count != 1 else ''}. "
                f"It will be removed from all of them."
            )
        else:
            msg = f'Delete "{tag_name}"?'
        dlg = QMessageBox(QMessageBox.NoIcon, "Delete Tag", msg,
                          QMessageBox.Yes | QMessageBox.No, self)
        dlg.setDefaultButton(QMessageBox.No)
        dlg.setIconPixmap(_white_question_icon())
        dlg.setStyleSheet(_DIALOG_STYLE)
        reply = dlg.exec()
        if reply != QMessageBox.Yes:
            return
        delete_tag(tag_name)
        self._available_tags.remove(tag_name)
        if tag_name in self._recipe_tags:
            self._recipe_tags.remove(tag_name)
            self.tagsChanged.emit()
        self._rebuild_tags()

    def _on_create_clicked(self):
        """Show the new tag input."""
        self._create_btn.hide()
        self._new_tag_input.show()
        self._new_tag_input.clear()
        self._new_tag_input.setFocus()
        self._cancel_btn.show()

    def _on_create_cancelled(self):
        """Hide the new tag input."""
        self._new_tag_input.hide()
        self._cancel_btn.hide()
        self._create_btn.show()

    def _on_new_tag_submitted(self):
        """Handle submitting a new tag."""
        tag_name = self._new_tag_input.text().strip()
        if not tag_name:
            self._on_create_cancelled()
            return

        # Check if tag already exists
        if tag_name.lower() in [t.lower() for t in self._available_tags]:
            w = QMessageBox(
                QMessageBox.Warning, "Tag Exists",
                f'The tag "{tag_name}" already exists.\n\n'
                "Select it from the available tags list to add it to this recipe.",
                QMessageBox.Ok, self,
            )
            w.setStyleSheet(_DIALOG_STYLE)
            w.exec()
            return

        # Create the tag in the database
        if create_tag(tag_name):
            self._available_tags.append(tag_name)
            # Also add it to the recipe
            self._recipe_tags.append(tag_name)
            self._rebuild_tags()
            self.tagsChanged.emit()

        self._on_create_cancelled()


class FlowLayout(QLayout):
    """A proper flow layout that wraps widgets horizontally.

    Based on the Qt FlowLayout example. Automatically reflows on resize.
    """

    def __init__(self, parent=None, h_spacing=8, v_spacing=8):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing

    def addItem(self, item):
        self._items.append(item)

    def addFlowWidget(self, widget):
        """Add a widget to the flow layout."""
        self.addWidget(widget)

    def flowCount(self):
        """Return the number of flow widgets."""
        return len(self._items)

    def clearFlow(self):
        """Clear all flow widgets."""
        while self._items:
            item = self._items.pop()
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.invalidate()

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

    def horizontalSpacing(self):
        return self._h_spacing

    def verticalSpacing(self):
        return self._v_spacing

    def expandingDirections(self):
        return Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        effective_rect = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            space_x = self._h_spacing
            space_y = self._v_spacing

            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + margins.bottom()

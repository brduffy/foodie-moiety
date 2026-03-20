"""Rich text editor widget - reusable editor with integrated formatting toolbar."""

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QTextCharFormat, QTextCursor, QTextListFormat
from utils.helpers import DIALOG_STYLE, platform_icon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class RichTextEditor(QWidget):
    """
    Reusable rich text editor with an integrated formatting toolbar.

    Can be used for both ingredients and directions panes. Supports
    bold, italic, underline, bullet lists, and numbered lists.
    """

    textChanged = Signal()
    step_link_clicked = Signal(int)  # Emits step index when link clicked in view mode

    def __init__(self, title="", placeholder="", parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Title header ---
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("EditorTitle")
            self.title_label.setStyleSheet("""
                QLabel#EditorTitle {
                    background-color: transparent;
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 6px 8px;
                }
            """)
            layout.addWidget(self.title_label)

        self._has_title = bool(title)

        # --- Formatting toolbar ---
        self.toolbar = QWidget()
        self.toolbar.setObjectName("EditorToolbar")
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(4, 4, 4, 4)
        toolbar_layout.setSpacing(4)

        self.btn_bold = self._create_toolbar_button("B", "Bold")
        self.btn_bold.setStyleSheet(self._toolbar_button_style(bold=True))
        self.btn_bold.clicked.connect(self._toggle_bold)
        toolbar_layout.addWidget(self.btn_bold)

        self.btn_italic = self._create_toolbar_button("I", "Italic")
        self.btn_italic.setStyleSheet(self._toolbar_button_style(italic=True))
        self.btn_italic.clicked.connect(self._toggle_italic)
        toolbar_layout.addWidget(self.btn_italic)

        self.btn_underline = self._create_toolbar_button("U", "Underline")
        self.btn_underline.setStyleSheet(self._toolbar_button_style(underline=True))
        self.btn_underline.clicked.connect(self._toggle_underline)
        toolbar_layout.addWidget(self.btn_underline)

        # Separator (invisible spacer between button groups)
        sep = QWidget()
        sep.setFixedWidth(7)
        sep.setStyleSheet("background-color: transparent; border: none;")
        toolbar_layout.addWidget(sep)

        self.btn_bullet = self._create_toolbar_button("•", "Bullet List")
        self.btn_bullet.clicked.connect(self._toggle_bullet_list)
        toolbar_layout.addWidget(self.btn_bullet)

        self.btn_numbered = self._create_toolbar_button("1.", "Numbered List")
        self.btn_numbered.clicked.connect(self._toggle_numbered_list)
        toolbar_layout.addWidget(self.btn_numbered)

        # Separator before link button
        sep2 = QWidget()
        sep2.setFixedWidth(7)
        sep2.setStyleSheet("background-color: transparent; border: none;")
        toolbar_layout.addWidget(sep2)

        self.btn_link_step = self._create_toolbar_button("Lk", "Link to Step")
        self.btn_link_step.setCheckable(False)
        link_icon = platform_icon("link", weight="regular", point_size=48, color="white")
        if not link_icon.isNull():
            self.btn_link_step.setText("")
            self.btn_link_step.setIcon(link_icon)
            self.btn_link_step.setIconSize(QSize(20, 20))
        self.btn_link_step.clicked.connect(self._on_step_link_clicked)
        toolbar_layout.addWidget(self.btn_link_step)

        self.btn_link_web = self._create_toolbar_button("Wl", "Insert Link")
        self.btn_link_web.setCheckable(False)
        web_link_icon = platform_icon("link.icloud", weight="regular", point_size=48, color="white")
        if not web_link_icon.isNull():
            self.btn_link_web.setText("")
            self.btn_link_web.setIcon(web_link_icon)
            self.btn_link_web.setIconSize(QSize(20, 20))
        self.btn_link_web.clicked.connect(self._on_web_link_clicked)
        toolbar_layout.addWidget(self.btn_link_web)

        toolbar_layout.addStretch()
        layout.addWidget(self.toolbar)

        # --- Custom tooltip for toolbar buttons ---
        self._tip_label = QLabel("", self)
        self._tip_label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: #2a2a2a;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 12px;
            }
        """)
        self._tip_label.setWindowFlags(Qt.ToolTip)
        self._tip_label.hide()
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(500)
        self._tip_timer.timeout.connect(self._show_tip)
        self._tip_target = None

        # Install event filter on toolbar buttons for custom tooltips
        for btn in (self.btn_bold, self.btn_italic, self.btn_underline,
                    self.btn_bullet, self.btn_numbered, self.btn_link_step,
                    self.btn_link_web):
            btn.installEventFilter(self)

        # --- Text editor ---
        self._font_size = 14  # Default font size in pixels
        self._step_count = 0  # Total steps for link dialog
        self._read_only = False  # Track mode for link click handling
        self._article_mode = False  # True when editing article paragraphs
        self._list_hints = None  # Lazy-created floating hints panel
        self.editor = QTextEdit()
        self.editor.setPlaceholderText(placeholder)
        self.editor.setAcceptRichText(True)
        font = QFont()
        font.setPixelSize(self._font_size)
        self.editor.setFont(font)
        self.editor.cursorPositionChanged.connect(self._update_toolbar_state)
        self.editor.textChanged.connect(self.textChanged.emit)
        # Install event filter on editor for key events, viewport for link clicks
        self.editor.installEventFilter(self)
        self.editor.viewport().installEventFilter(self)
        self.editor.setMouseTracking(True)
        # Set link color via palette (QTextEdit uses palette for anchor colors)
        palette = self.editor.palette()
        palette.setColor(QPalette.Link, QColor("#f0c040"))
        self.editor.setPalette(palette)
        layout.addWidget(self.editor)

        # Apply dark theme styling
        self.setStyleSheet(self._widget_style())

    def _create_toolbar_button(self, text, tooltip):
        """Create a small toolbar toggle button."""
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setFixedSize(32, 28)
        return btn

    def _toolbar_button_style(self, bold=False, italic=False, underline=False):
        """Return style for a specific toolbar button with font variation."""
        font_weight = "bold" if bold else "normal"
        font_style = "italic" if italic else "normal"
        text_decoration = "underline" if underline else "none"
        return f"""
            QPushButton {{
                font-weight: {font_weight};
                font-style: {font_style};
                text-decoration: {text_decoration};
            }}
        """

    def set_title(self, title):
        """Update the title label text."""
        if self._has_title:
            self.title_label.setText(title)

    def _widget_style(self):
        """Return the full widget stylesheet."""
        return """
            QWidget#EditorToolbar {
                background-color: transparent;
                border-bottom: 1px solid #555555;
            }
            QPushButton {
                background-color: #2a2a2a;
                color: #cccccc;
                border: 1px solid #444444;
                border-radius: 4px;
                font-size: 13px;
                padding: 2px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:checked {
                background-color: #0078d4;
                color: white;
                border-color: #0078d4;
            }
            QTextEdit {
                background-color: transparent;
                color: #e0e0e0;
                border: 1px solid #555555;
                padding: 8px;
                selection-background-color: #0078d4;
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
            QToolTip {
                color: white;
                background-color: #333333;
                border: 1px solid #555555;
                padding: 4px;
            }
        """

    # --- Formatting actions ---

    def _toggle_bold(self):
        """Toggle bold formatting on the current selection."""
        fmt = QTextCharFormat()
        if self.editor.currentCharFormat().fontWeight() == QFont.Weight.Bold:
            fmt.setFontWeight(QFont.Weight.Normal)
        else:
            fmt.setFontWeight(QFont.Weight.Bold)
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFocus()

    def _toggle_italic(self):
        """Toggle italic formatting on the current selection."""
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.currentCharFormat().fontItalic())
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFocus()

    def _toggle_underline(self):
        """Toggle underline formatting on the current selection."""
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.currentCharFormat().fontUnderline())
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFocus()

    def _toggle_bullet_list(self):
        """Toggle bullet list formatting on the current block."""
        cursor = self.editor.textCursor()
        current_list = cursor.currentList()

        if current_list and current_list.format().style() == QTextListFormat.Style.ListDisc:
            # Remove list formatting
            self._remove_list(cursor)
        else:
            # Apply bullet list
            list_fmt = QTextListFormat()
            list_fmt.setStyle(QTextListFormat.Style.ListDisc)
            list_fmt.setIndent(1)
            cursor.createList(list_fmt)

        self.editor.setFocus()
        self._update_toolbar_state()

    def _toggle_numbered_list(self):
        """Toggle numbered list formatting on the current block."""
        cursor = self.editor.textCursor()
        current_list = cursor.currentList()

        if current_list and current_list.format().style() == QTextListFormat.Style.ListDecimal:
            # Remove list formatting
            self._remove_list(cursor)
        else:
            # Apply numbered list
            list_fmt = QTextListFormat()
            list_fmt.setStyle(QTextListFormat.Style.ListDecimal)
            list_fmt.setIndent(1)
            cursor.createList(list_fmt)

        self.editor.setFocus()
        self._update_toolbar_state()

    def _remove_list(self, cursor):
        """Remove list formatting from the current block."""
        current_list = cursor.currentList()
        if current_list:
            block = cursor.block()
            current_list.remove(block)
            # Reset indent
            block_fmt = block.blockFormat()
            block_fmt.setIndent(0)
            cursor.setBlockFormat(block_fmt)

    # --- Toolbar state tracking ---

    def _update_toolbar_state(self):
        """Update toolbar button checked states based on cursor position."""
        char_fmt = self.editor.currentCharFormat()
        self.btn_bold.setChecked(char_fmt.fontWeight() == QFont.Weight.Bold)
        self.btn_italic.setChecked(char_fmt.fontItalic())
        self.btn_underline.setChecked(char_fmt.fontUnderline())

        cursor = self.editor.textCursor()
        current_list = cursor.currentList()
        if current_list:
            style = current_list.format().style()
            self.btn_bullet.setChecked(style == QTextListFormat.Style.ListDisc)
            self.btn_numbered.setChecked(style == QTextListFormat.Style.ListDecimal)
        else:
            self.btn_bullet.setChecked(False)
            self.btn_numbered.setChecked(False)

        # Show/hide contextual list hints
        if current_list and not self._read_only:
            self._show_list_hints()
        else:
            self._hide_list_hints()

    # --- Contextual list hints ---

    def _create_list_hints(self):
        """Create the floating list hints panel (parented to top-level window)."""
        win = self.window()
        panel = QLabel(win)
        panel.setText(
            "\u2022  Enter twice at start of list item to add space above\n\n"
            "\u2022  Enter twice at end of list item to end list"
        )
        panel.setStyleSheet("""
            QLabel {
                background-color: #1e1e1e;
                color: #d0d0d0;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 10px 14px;
                font-size: 11px;
            }
        """)
        panel.setWordWrap(True)
        panel.adjustSize()
        panel.hide()
        return panel

    def _show_list_hints(self):
        """Position and show the list hints panel to the right of the editor."""
        if self._list_hints is None:
            self._list_hints = self._create_list_hints()
        win = self.window()
        # Map editor's top-right corner to the window coordinate space
        editor_top_right = self.mapTo(win, QPoint(self.width(), 0))
        gap = 10
        x = editor_top_right.x() + gap
        y = editor_top_right.y()
        # Clamp so it doesn't go off the right edge
        max_x = win.width() - self._list_hints.sizeHint().width() - 10
        x = min(x, max_x)
        self._list_hints.move(x, y)
        self._list_hints.show()
        self._list_hints.raise_()

    def _hide_list_hints(self):
        """Hide the list hints panel."""
        if self._list_hints is not None:
            self._list_hints.hide()

    # --- Public API ---

    def set_html(self, html):
        """Load content as HTML."""
        self.editor.setHtml(html)
        # Always re-apply current font size — HTML content carries its own
        # inline styles from when it was saved, which may not match the
        # user's persisted font size preference.
        self.adjust_font_size(0)

    def get_html(self):
        """Return content as HTML string."""
        return self.editor.toHtml()

    def set_read_only(self, read_only):
        """Toggle between edit and read-only mode."""
        self._read_only = read_only
        self.editor.setReadOnly(read_only)
        self.toolbar.setVisible(not read_only)
        if read_only:
            self._hide_list_hints()

        # Configure link click behavior and cursor
        if read_only:
            # In view mode: make links clickable
            self.editor.setTextInteractionFlags(Qt.TextBrowserInteraction)
        else:
            # In edit mode: normal text editing, links not clickable
            self.editor.setTextInteractionFlags(Qt.TextEditorInteraction)
            # Reset cursor to text cursor for editing
            self.editor.viewport().setCursor(Qt.IBeamCursor)

    def set_article_mode(self, enabled: bool) -> None:
        """Enable or disable article mode for link behavior."""
        self._article_mode = enabled
        # Article mode: web link only. Recipe mode: both step link and web link.
        self.btn_link_step.setVisible(not enabled)
        self.btn_link_web.setVisible(True)

    def set_step_count(self, count):
        """Set the total number of steps available for linking.

        Args:
            count: Total steps including intro (0 = intro, 1-N = cooking steps)
        """
        self._step_count = count

    def _px_to_pt(self, px):
        """Convert pixel size to point size using screen DPI."""
        screen = QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch() if screen else 96
        return px * 72.0 / dpi

    def scroll_by_page(self, direction: str):
        """Smooth-scroll the text editor by one viewport page.

        Args:
            direction: "down" or "up".
        """
        v_bar = self.editor.verticalScrollBar()
        page = self.editor.viewport().height()
        target = v_bar.value() + (page if direction == "down" else -page)
        target = max(v_bar.minimum(), min(target, v_bar.maximum()))
        self._scroll_anim = QPropertyAnimation(v_bar, b"value")
        self._scroll_anim.setDuration(300)
        self._scroll_anim.setStartValue(v_bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_anim.start()

    def adjust_font_size(self, delta):
        """Adjust the font size of the editor text by delta pixels.

        Args:
            delta: Number of pixels to add (positive) or subtract (negative).
        """
        self._font_size = max(14, min(24, self._font_size + delta))
        font = QFont()
        font.setPixelSize(self._font_size)
        # Update the default font for new text
        self.editor.setFont(font)
        # Apply to all existing rich text content (inline styles override setFont)
        cursor = self.editor.textCursor()
        saved_pos = cursor.position()
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        pt_size = self._px_to_pt(self._font_size)
        fmt.setFontPointSize(pt_size)
        cursor.mergeCharFormat(fmt)
        cursor.clearSelection()
        cursor.setPosition(saved_pos)
        self.editor.setTextCursor(cursor)

        # Update list marker fonts — Qt draws markers using the block-level
        # character format, not fragment formats set by mergeCharFormat.
        doc = self.editor.document()
        block = doc.begin()
        while block.isValid():
            if block.textList() is not None:
                bc = QTextCursor(block)
                block_char_fmt = QTextCharFormat()
                block_char_fmt.setFontPointSize(pt_size)
                bc.mergeBlockCharFormat(block_char_fmt)
            block = block.next()

    # --- Custom tooltip ---

    def _show_tip(self):
        """Show the custom tooltip below the target button."""
        btn = self._tip_target
        if btn is None or not btn.isVisible():
            return
        tip = btn.toolTip()
        if not tip:
            return
        self._tip_label.setText(tip)
        self._tip_label.adjustSize()
        # Center below the button
        btn_global = btn.mapToGlobal(QPoint(0, 0))
        x = btn_global.x() + (btn.width() - self._tip_label.width()) // 2
        y = btn_global.y() + btn.height() + 2
        self._tip_label.move(x, y)
        self._tip_label.show()

    # --- Step link handling ---

    def eventFilter(self, obj, event):
        """Handle toolbar button tooltips, list marker cleanup, and step link clicks."""
        # When Enter is pressed in a list, fix the new block's marker format
        # so it doesn't inherit link styling (gold color) from the previous block.
        if obj == self.editor and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                cursor = self.editor.textCursor()
                if cursor.currentList() is not None:
                    at_start = cursor.atBlockStart() and cursor.block().text()
                    # Let Qt handle Enter normally (creates new list item,
                    # or exits list if current item is empty).
                    QTextEdit.keyPressEvent(self.editor, event)
                    cursor = self.editor.textCursor()
                    default_fmt = QTextCharFormat()
                    default_fmt.setForeground(QColor("#e0e0e0"))
                    default_fmt.setFontPointSize(self._px_to_pt(self._font_size))
                    # Enter at start of a non-empty item: Qt splits the
                    # block and leaves the cursor on the text below.
                    # Move it up to the new empty item so the user can
                    # press Enter again to exit the list.
                    if at_start and cursor.currentList() is not None:
                        # Clean the block that moved down (current position)
                        cursor.setBlockCharFormat(default_fmt)
                        # Move up to the new empty item
                        cursor.movePosition(QTextCursor.PreviousBlock)
                        cursor.movePosition(QTextCursor.StartOfBlock)
                    # If still in a list, reset the current block's format
                    # so markers don't inherit link styling (gold, underline).
                    if cursor.currentList() is not None:
                        cursor.setBlockCharFormat(default_fmt)
                        cursor.setCharFormat(default_fmt)
                    self.editor.setTextCursor(cursor)
                    return True

        # Toolbar button tooltip handling
        if isinstance(obj, QPushButton):
            etype = event.type()
            if etype == QEvent.Enter:
                if obj.toolTip():
                    self._tip_target = obj
                    self._tip_timer.start()
            elif etype == QEvent.Leave:
                self._tip_timer.stop()
                self._tip_target = None
                self._tip_label.hide()
            elif etype == QEvent.ToolTip:
                # Block Qt's native tooltip
                return True
            return super().eventFilter(obj, event)

        # Link click handling on editor viewport (read-only mode)
        if obj == self.editor.viewport() and self._read_only:
            if event.type() == QEvent.MouseMove:
                anchor = self.editor.anchorAt(event.pos())
                if anchor and (anchor.startswith("step:") or anchor.startswith("https://")):
                    self.editor.viewport().setCursor(Qt.PointingHandCursor)
                else:
                    self.editor.viewport().setCursor(Qt.ArrowCursor)
            elif event.type() == QEvent.MouseButtonRelease:
                anchor = self.editor.anchorAt(event.pos())
                if anchor and anchor.startswith("step:"):
                    try:
                        step_index = int(anchor[5:])
                        self.step_link_clicked.emit(step_index)
                        return True
                    except ValueError:
                        pass
                elif anchor and anchor.startswith("https://"):
                    from PySide6.QtGui import QDesktopServices
                    from PySide6.QtCore import QUrl
                    from widgets.article_link_dialog import _is_valid_fm_url
                    # Only open links within foodiemoiety.com
                    if _is_valid_fm_url(anchor):
                        QDesktopServices.openUrl(QUrl(anchor))
                    return True
        return super().eventFilter(obj, event)

    def _on_step_link_clicked(self):
        """Handle the step link toolbar button click."""
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            msg = QMessageBox(self)
            msg.setWindowTitle("No Text Selected")
            msg.setText("Please select the text you want to turn into a step link.")
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            return
        self._on_step_link(cursor)
        self.editor.setFocus()

    def _on_web_link_clicked(self):
        """Handle the web link toolbar button click."""
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            msg = QMessageBox(self)
            msg.setWindowTitle("No Text Selected")
            msg.setText("Please select the text you want to turn into a link.")
            msg.setStyleSheet(DIALOG_STYLE)
            msg.exec()
            return
        self._on_article_link(cursor)
        self.editor.setFocus()

    def _on_step_link(self, cursor):
        """Show step selection dialog and apply step link."""
        if self._step_count == 0:
            return
        from widgets.step_link_dialog import StepLinkDialog

        dialog = StepLinkDialog(self._step_count, parent=self)
        if dialog.exec() == QDialog.Accepted:
            step_index = dialog.selected_step()
            self._apply_step_link(cursor, step_index)

    def _on_article_link(self, cursor):
        """Show URL input dialog and apply article link."""
        from widgets.article_link_dialog import ArticleLinkDialog

        dialog = ArticleLinkDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            url = dialog.selected_url()
            self._apply_article_link(cursor, url)

    def _apply_step_link(self, cursor, step_index):
        """Apply step link formatting to the selected text.

        Args:
            cursor: QTextCursor with selection
            step_index: 0-based step index to link to
        """
        # Build the link text with step indicator for voice control
        selected_text = cursor.selectedText()
        step_label = "Intro" if step_index == 0 else f"step {step_index}"
        new_text = f"{selected_text} ({step_label})"

        # Create format for the link
        fmt = QTextCharFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(f"step:{step_index}")
        fmt.setForeground(QColor("#f0c040"))  # Gold link color - pops on frosted glass
        fmt.setFontUnderline(True)

        # Replace selection with new text and apply formatting
        cursor.insertText(new_text, fmt)

        # Reset char format so subsequent typing is normal (not gold/underlined)
        default_fmt = QTextCharFormat()
        default_fmt.setForeground(QColor("#e0e0e0"))
        default_fmt.setFontPointSize(self._px_to_pt(self._font_size))
        if cursor.currentList() is not None:
            cursor.setBlockCharFormat(default_fmt)
        cursor.setCharFormat(default_fmt)
        self.editor.setTextCursor(cursor)

    def _apply_article_link(self, cursor, url):
        """Apply article URL link formatting to the selected text.

        Args:
            cursor: QTextCursor with selection
            url: Validated foodiemoiety.com URL
        """
        selected_text = cursor.selectedText()

        fmt = QTextCharFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(url)
        fmt.setForeground(QColor("#5EAAFF"))  # Blue link color for articles
        fmt.setFontUnderline(True)

        cursor.insertText(selected_text, fmt)

        # Reset char format so subsequent typing is normal (not blue/underlined)
        default_fmt = QTextCharFormat()
        default_fmt.setForeground(QColor("#e0e0e0"))
        default_fmt.setFontPointSize(self._px_to_pt(self._font_size))
        cursor.setCharFormat(default_fmt)
        self.editor.setTextCursor(cursor)

    def hideEvent(self, event):
        """Hide list hints when the editor is hidden."""
        self._hide_list_hints()
        super().hideEvent(event)

    def resizeEvent(self, event):
        """Reposition list hints when the editor resizes."""
        if self._list_hints is not None and self._list_hints.isVisible():
            self._show_list_hints()
        super().resizeEvent(event)

    def moveEvent(self, event):
        """Reposition list hints when the editor moves (e.g. window resize)."""
        if self._list_hints is not None and self._list_hints.isVisible():
            self._show_list_hints()
        super().moveEvent(event)

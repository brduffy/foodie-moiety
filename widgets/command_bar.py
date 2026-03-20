"""Command bar widget - reusable top bar for view-specific commands."""

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt, QSize
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CommandBar(QWidget):
    """
    Reusable command bar that stretches horizontally across the top of the window.

    Views can configure this bar with their own buttons, search fields, and filters.
    Auto-hides when video player is active.
    """

    ITEM_HEIGHT = 32

    def __init__(self, parent=None):
        super().__init__(parent)

        # Set object name for stylesheet targeting
        self.setObjectName("CommandBar")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Main vertical layout to hold buttons and separator
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Horizontal layout for buttons
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(10, 5, 10, 5)
        self.layout.setSpacing(7)
        main_layout.addLayout(self.layout)

        # Separator line at bottom
        separator = QWidget()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #888888;")
        main_layout.addWidget(separator)

        # Style - distinct lighter background
        self.setStyleSheet("""
            QWidget#CommandBar {
                background-color: #000000;
            }
            QPushButton {
                background-color: #2a2a2a;
                color: #cccccc;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
                min-width: 70px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: white;
            }
            QPushButton:pressed {
                background-color: #1e1e1e;
            }
            QPushButton[checkable="true"] {
                min-width: 0px;
                padding: 8px 4px;
            }
            QPushButton[checkable="true"]:checked {
                background-color: #4a4a4a;
                border: 1px solid #888888;
            }
            QLineEdit {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                min-width: 250px;
            }
            QLineEdit:hover {
                border-color: #5a5a5a;
            }
            QLineEdit:focus {
                border-color: #0078d4;
                background-color: #333;
            }
            QMenu {
                background-color: #2a2a2a;
                color: #cccccc;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 0px;
                font-size: 14px;
            }
            QMenu::item {
                padding: 6px 20px;
            }
            QMenu::item:selected {
                background-color: #4a4a4a;
                color: white;
            }
        """)

        # Custom tooltip label (parented to top-level window, positioned manually)
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

        # Keep track of widgets for clearing
        self.command_widgets = []

    def clear(self):
        """Remove all command widgets from the bar."""
        for widget in self.command_widgets:
            self.layout.removeWidget(widget)
            widget.deleteLater()
        self.command_widgets.clear()

    def add_button(self, label, callback, tooltip=None, icon=None):
        """
        Add a command button to the bar.

        Args:
            label: Button text
            callback: Function to call when clicked
            tooltip: Optional tooltip text
            icon: Optional QIcon or unicode character for button icon

        Returns:
            QPushButton: The created button
        """
        button = QPushButton(label)
        button.setFixedHeight(self.ITEM_HEIGHT)
        button.clicked.connect(callback)
        button.installEventFilter(self)
        if tooltip:
            button.setToolTip(tooltip)
        if icon:
            if isinstance(icon, QIcon):
                button.setIcon(icon)
                button.setIconSize(QSize(24, 24))
            elif isinstance(icon, str):
                # If it's a string, use it as a text icon prefix
                button.setText(f"{icon}  {label}")

        # Insert before the stretch
        self.layout.insertWidget(len(self.command_widgets), button)
        self.command_widgets.append(button)
        return button

    def add_search(self, placeholder, callback, tooltip=None):
        """
        Add a search field to the bar.

        Filters live as the user types (250ms debounce) and instantly on Enter.

        Args:
            placeholder: Placeholder text
            callback: Function to call with search text
            tooltip: Optional tooltip text

        Returns:
            QLineEdit: The created search field
        """
        search = QLineEdit()
        search.setFixedHeight(self.ITEM_HEIGHT)
        search.setPlaceholderText(placeholder)

        # Instant filter on Enter
        search.returnPressed.connect(lambda: callback(search.text()))

        # Debounced live filter as user types
        debounce = QTimer()
        debounce.setSingleShot(True)
        debounce.setInterval(250)
        debounce.timeout.connect(lambda: callback(search.text()))
        search.textChanged.connect(lambda _: debounce.start())
        search._debounce_timer = debounce  # prevent GC

        if tooltip:
            search.setToolTip(tooltip)

        # Insert before the stretch
        self.layout.insertWidget(len(self.command_widgets), search)
        self.command_widgets.append(search)
        return search

    def add_menu_button(self, items, callback, tooltip=None):
        """
        Add a button with a dropdown menu to the bar.

        The button displays the currently selected item label with a ▾ suffix.
        Clicking opens a menu below the button.

        Args:
            items: List of (label, value) tuples
            callback: Function to call with the selected value
            tooltip: Optional tooltip text

        Returns:
            QPushButton: The created button
        """
        button = QPushButton()
        button.setFixedHeight(self.ITEM_HEIGHT)
        button.installEventFilter(self)
        menu = QMenu(button)

        def on_action(action):
            value = action.data()
            button.setText(f"{action.text()}  ▾")
            callback(value)

        for label, value in items:
            action = menu.addAction(label)
            action.setData(value)

        menu.triggered.connect(on_action)

        # Set initial text from first item
        if items:
            button.setText(f"{items[0][0]}  ▾")

        button.clicked.connect(
            lambda: menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        )
        if tooltip:
            button.setToolTip(tooltip)

        # Store callback and menu reference for updating items later
        button._menu_callback = callback
        button._menu = menu

        # Insert before the stretch
        self.layout.insertWidget(len(self.command_widgets), button)
        self.command_widgets.append(button)
        return button

    def update_menu_button_items(self, button, items, preserve_selection=True):
        """
        Update the items in a menu button.

        Args:
            button: The QPushButton returned by add_menu_button
            items: New list of (label, value) tuples
            preserve_selection: If True, keep the current selection if still valid
        """
        if not hasattr(button, "_menu") or button._menu is None:
            return

        menu = button._menu

        # Get current selection value before clearing
        current_text = button.text().replace("  ▾", "")
        current_value = None
        for action in menu.actions():
            if action.text() == current_text:
                current_value = action.data()
                break

        # Clear and rebuild menu items (keep existing signal connection)
        menu.clear()

        for label, value in items:
            action = menu.addAction(label)
            action.setData(value)

        # Restore selection or default to first item
        if preserve_selection and current_value is not None:
            # Check if current value is still valid
            for label, value in items:
                if value == current_value:
                    button.setText(f"{label}  ▾")
                    return

        # Default to first item
        if items:
            button.setText(f"{items[0][0]}  ▾")

    def add_toggle_button(self, label, callback, size=32, tooltip=None):
        """
        Add a square toggle button to the bar.

        Args:
            label: Button text (displayed centered)
            callback: Function to call with checked state (bool)
            size: Width and height in pixels
            tooltip: Optional tooltip text

        Returns:
            QPushButton: The created toggle button
        """
        button = QPushButton(label)
        button.setCheckable(True)
        button.setFixedSize(size, self.ITEM_HEIGHT)
        button.toggled.connect(callback)
        button.installEventFilter(self)
        if tooltip:
            button.setToolTip(tooltip)

        # Insert before the stretch
        self.layout.insertWidget(len(self.command_widgets), button)
        self.command_widgets.append(button)
        return button

    def add_spacer(self):
        """Add a small spacer between groups of controls."""
        # Create an invisible widget as spacer
        spacer = QWidget()
        spacer.setFixedWidth(20)
        self.layout.insertWidget(len(self.command_widgets), spacer)
        self.command_widgets.append(spacer)

    def add_widget(self, widget):
        """Add an arbitrary widget to the command bar."""
        self.layout.insertWidget(len(self.command_widgets), widget)
        self.command_widgets.append(widget)
        return widget

    def add_stretch(self):
        """Add an expanding stretch between controls."""
        from PySide6.QtWidgets import QSizePolicy

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.layout.insertWidget(len(self.command_widgets), spacer)
        self.command_widgets.append(spacer)

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

    def eventFilter(self, obj, event):
        """Show custom tooltip label just below command bar buttons."""
        if not isinstance(obj, QPushButton):
            return super().eventFilter(obj, event)

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

    def set_visible_animated(self, visible):
        """
        Show or hide the command bar.

        Args:
            visible: True to show, False to hide
        """
        # For now, just show/hide
        # TODO: Add smooth animation in future
        self.setVisible(visible)

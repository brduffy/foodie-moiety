"""Step navigator widget - bottom bar for navigating recipe steps."""

from PySide6.QtCore import (
    Signal, Qt, QPoint, QParallelAnimationGroup, QPropertyAnimation, QEasingCurve,
    QTimer, QVariantAnimation,
)
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QWidget,
)


class StepNavigator(QWidget):
    """
    Step navigation bar that appears at the bottom of the window.

    Features:
    - Horizontal scrollable row of step buttons
    - Rounded rectangle buttons with step numbers
    - Red background for step buttons, white text
    - Auto-hides when video plays
    - Can overlay video on mouse movement or AI command
    - Drag-and-drop reordering of steps (edit mode only)
    """

    step_changed = Signal(int)  # Emits 0-based step index when user clicks a step
    selection_changed = Signal(list)  # Emits sorted list of 0-based selected step indices
    step_moved = Signal(int, int)  # Emits (from_step_index, to_step_index), 1-based

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Fixed height
        self.control_height = 65
        self.setFixedHeight(self.control_height)

        # Track current step and multi-selection
        self.current_step = 0
        self.selected_steps = set()  # Steps highlighted blue via Ctrl/Cmd+click
        self.step_buttons = []
        self._step_offset = 0  # 0 when intro shown, 1 when intro hidden

        # Button insert/delete/move animation
        self._insert_anim = None
        self._insert_opacity = None
        self._delete_anim = None
        self._move_anim = None
        self._highlighted_steps = set()  # Steps with blue highlight fade
        self._highlight_anim = None

        # Drag state
        self._drag_enabled = False
        self._drag_source_btn_idx = None  # Button list index being dragged
        self._drag_start_pos = None  # QPoint of initial mouse press
        self._dragging = False  # True once drag threshold exceeded

        # Main layout
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 10)
        main_layout.setSpacing(0)

        # Scroll area for step buttons
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setFixedHeight(53)  # 45px buttons + 8px spacing
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:horizontal {
                height: 6px;
                background-color: #1a1a1a;
                border: none;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background-color: #555555;
                border-radius: 3px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #777777;
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px;
                height: 0px;
            }
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background: none;
            }
        """)

        # Container widget for step buttons
        self.button_container = QWidget()
        self.button_container.setFixedHeight(45)  # Match button height exactly
        self.button_layout = QHBoxLayout(self.button_container)
        self.button_layout.setContentsMargins(5, 0, 5, 0)  # No bottom margin
        self.button_layout.setSpacing(8)
        self.button_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.scroll_area.setWidget(self.button_container)
        main_layout.addWidget(self.scroll_area)

        # Drag indicator (thin yellow line shown between buttons during drag)
        self._drag_indicator = QWidget(self.button_container)
        self._drag_indicator.setFixedSize(3, 45)
        self._drag_indicator.setStyleSheet("background-color: #f0c040; border-radius: 1px;")
        self._drag_indicator.hide()
        self._drag_indicator.raise_()

        # Background styling
        self.setStyleSheet("""
            StepNavigator {
                background-color: #121212;
            }
        """)

        # Start hidden by default
        self.hide()

    # ------------------------------------------------------------------
    # Drag-and-drop
    # ------------------------------------------------------------------

    def set_drag_enabled(self, enabled):
        """Enable or disable drag-to-reorder."""
        self._drag_enabled = enabled
        if not enabled:
            self._reset_drag()

    def _reset_drag(self):
        """Reset all drag state."""
        self._drag_source_btn_idx = None
        self._drag_start_pos = None
        self._dragging = False
        self._drag_indicator.hide()

    def eventFilter(self, obj, event):
        """Intercept mouse events on step buttons for drag-and-drop."""
        if not self._drag_enabled:
            return False

        etype = event.type()

        if etype == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            # Find which button was pressed
            btn_idx = self._button_index_of(obj)
            if btn_idx is not None and btn_idx > 0:  # Skip intro (index 0)
                self._drag_source_btn_idx = btn_idx
                self._drag_start_pos = event.globalPosition().toPoint()
            return False  # Let click proceed

        if etype == event.Type.MouseMove and self._drag_source_btn_idx is not None:
            if not self._dragging:
                # Check drag threshold
                dist = (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength()
                if dist >= QApplication.startDragDistance():
                    self._dragging = True
                    # Dim the source button
                    src_btn = self.step_buttons[self._drag_source_btn_idx]
                    src_btn.setStyleSheet(self._step_button_style(
                        "#555555", "#555555", "#555555", "2px dashed #888", 18))
            if self._dragging:
                self._update_drag_indicator(event.globalPosition().toPoint())
                return True  # Consume move events during drag

        if etype == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._dragging:
                self._finish_drag(event.globalPosition().toPoint())
                return True  # Consume release — don't trigger click
            self._reset_drag()

        return False

    def _button_index_of(self, widget):
        """Return the index in step_buttons for the given widget, or None."""
        for i, btn in enumerate(self.step_buttons):
            if btn is widget:
                return i
        return None

    def _drop_button_index(self, global_pos):
        """Determine the drop position (button list index) for the given cursor.

        Returns the button index where the dragged step should be inserted
        BEFORE. For example, returning 1 means "insert before button 1"
        (i.e., right after the intro button).

        The minimum return value is 1 (can't drop before intro).
        The maximum is len(step_buttons) (append at end).
        """
        container_x = self.button_container.mapFromGlobal(global_pos).x()

        # Walk buttons from index 1 onward (skip intro at 0)
        for i in range(1, len(self.step_buttons)):
            btn = self.step_buttons[i]
            mid = btn.x() + btn.width() // 2
            if container_x < mid:
                return i

        # Past the last button — drop at end
        return len(self.step_buttons)

    def _update_drag_indicator(self, global_pos):
        """Position the drag indicator line at the drop target gap."""
        drop_idx = self._drop_button_index(global_pos)

        # Position the indicator between buttons
        if drop_idx < len(self.step_buttons):
            btn = self.step_buttons[drop_idx]
            x = btn.x() - self.button_layout.spacing() // 2 - 1
        else:
            # After last button
            btn = self.step_buttons[-1]
            x = btn.x() + btn.width() + self.button_layout.spacing() // 2 - 1

        self._drag_indicator.move(x, 0)
        self._drag_indicator.show()
        self._drag_indicator.raise_()

    def _finish_drag(self, global_pos):
        """Complete the drag: emit step_moved if position changed."""
        drop_idx = self._drop_button_index(global_pos)
        src_idx = self._drag_source_btn_idx
        self._reset_drag()

        if src_idx is None:
            return

        # Convert button indices to 1-based step indices
        from_step = src_idx + self._step_offset
        # drop_idx is "insert before this button index"
        # If dropping after the source, adjust for the removal
        if drop_idx > src_idx:
            to_step = (drop_idx - 1) + self._step_offset
        else:
            to_step = drop_idx + self._step_offset

        if from_step != to_step:
            self.step_moved.emit(from_step, to_step)

    # ------------------------------------------------------------------
    # Step loading
    # ------------------------------------------------------------------

    def load_steps(self, recipe_id, num_steps=5, show_intro=True):
        """
        Load steps for a recipe.

        Args:
            recipe_id: ID of the recipe to load steps for
            num_steps: Number of steps (placeholder until database integration)
            show_intro: If True, first button is the intro icon; if False,
                        all buttons are numbered (used for clipboard view).
        """
        # Clear existing buttons and selection
        for button in self.step_buttons:
            button.removeEventFilter(self)
            self.button_layout.removeWidget(button)
            button.deleteLater()
        self.step_buttons.clear()
        self.selected_steps.clear()
        self._reset_drag()

        # Stop any running animations (old buttons are being deleted)
        if self._insert_anim is not None:
            self._insert_anim.stop()
            self._insert_anim = None
        self._insert_opacity = None
        if self._delete_anim is not None:
            self._delete_anim.stop()
            self._delete_anim = None
        if self._move_anim is not None:
            self._move_anim.stop()
            self._move_anim = None

        # Create step buttons
        # step_offset maps button list index → step index emitted by clicks.
        # With intro: index 0 = intro (step 0), index 1 = step 1, etc.
        # Without intro: index 0 = step 1, index 1 = step 2, etc.
        self._step_offset = 0 if show_intro else 1
        for i in range(num_steps):
            step_idx = i + self._step_offset
            if i == 0 and show_intro:
                button = self._create_intro_button()
            else:
                button = self._create_step_button(step_idx)
            button.installEventFilter(self)
            self.button_layout.addWidget(button)
            self.step_buttons.append(button)

        # Set container width based on number of buttons
        # Width = (button_width * num_steps) + (spacing * (num_steps - 1)) + margins
        container_width = (50 * num_steps) + (8 * (num_steps - 1)) + 10
        self.button_container.setFixedWidth(container_width)

        # Set first step as active
        if self.step_buttons:
            self.current_step = self._step_offset
            self._update_active_step()

    def _create_intro_button(self):
        """Create the intro step button with a list/summary icon."""
        button = QPushButton("☰")
        button.setFixedSize(50, 45)
        button.setStyleSheet("""
            QPushButton {
                background-color: #cc0000;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff0000;
            }
            QPushButton:pressed {
                background-color: #990000;
            }
        """)
        button.clicked.connect(lambda checked=False, b=button: self._on_button_clicked(b))
        return button

    def _create_step_button(self, step_number):
        """Create a rounded rectangle button for a step."""
        button = QPushButton(str(step_number))
        button.setFixedSize(50, 45)
        button.setStyleSheet("""
            QPushButton {
                background-color: #cc0000;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff0000;
            }
            QPushButton:pressed {
                background-color: #990000;
            }
        """)
        button.clicked.connect(lambda checked=False, b=button: self._on_button_clicked(b))
        return button

    def _on_button_clicked(self, button):
        """Route a button click to _on_step_clicked using the button's current position."""
        idx = self._button_index_of(button)
        if idx is not None:
            self._on_step_clicked(idx + self._step_offset)

    def _on_step_clicked(self, step_index):
        """Handle step button click.

        Normal click: clears any multi-selection, sets current step, emits step_changed.
        Cmd+click on the selected step (white border): adds it to the blue range.
        Cmd+click on an unselected step: creates a blue range from the selected step
            to the clicked step (inclusive), excluding the intro step (index 0).
        Cmd+click on the intro step (index 0): ignored for range selection.
        If the selected step is the intro and no blue range exists, Cmd+click is ignored.
        """
        modifiers = QGuiApplication.keyboardModifiers()
        ctrl_held = modifiers & Qt.ControlModifier or modifiers & Qt.MetaModifier

        if ctrl_held:
            # Intro step cannot be part of a copy range
            if step_index == 0:
                return

            if step_index == self.current_step:
                # Cmd+click on the selected step — if any blue range exists,
                # clear all selection; otherwise add this step to the range
                if self.selected_steps:
                    self.selected_steps.clear()
                else:
                    self.selected_steps.add(step_index)
            else:
                # If selected step is intro and no existing blue range, ignore
                if self.current_step == 0 and not self.selected_steps:
                    return
                # Cmd+click on an unselected step — range from selected to clicked
                low = max(self.current_step, 1)  # exclude intro
                high = step_index
                if low > high:
                    low, high = high, low
                self.selected_steps = set(range(low, high + 1))
            self._update_active_step()
            self.selection_changed.emit(sorted(self.selected_steps))
        else:
            # Normal click — clear multi-selection, change current step
            had_selection = len(self.selected_steps) > 0
            self.selected_steps.clear()
            self.current_step = step_index
            self._update_active_step()
            self.step_changed.emit(step_index)
            if had_selection:
                self.selection_changed.emit([])

    def _step_button_style(self, bg, hover_bg, pressed_bg, border, font_size):
        """Build a QPushButton stylesheet for a step button."""
        border_css = f"border: {border};" if border else "border: none;"
        return f"""
            QPushButton {{
                background-color: {bg};
                color: white;
                {border_css}
                border-radius: 8px;
                font-size: {font_size}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
            }}
            QPushButton:pressed {{
                background-color: {pressed_bg};
            }}
        """

    def _update_active_step(self):
        """Update the visual state of step buttons to show active, selected, and inactive steps."""
        for i, button in enumerate(self.step_buttons):
            step_idx = i + self._step_offset
            is_intro = (step_idx == 0)
            fs = 20 if is_intro else 18  # Intro icon needs slightly larger font
            if step_idx in self.selected_steps or step_idx in self._highlighted_steps:
                if step_idx == self.current_step:
                    button.setStyleSheet(self._step_button_style(
                        "#2266cc", "#3377dd", "#1155bb", "2px solid white", fs))
                else:
                    button.setStyleSheet(self._step_button_style(
                        "#2266cc", "#3377dd", "#1155bb", None, fs))
            elif step_idx == self.current_step:
                button.setStyleSheet(self._step_button_style(
                    "#ff3333", "#ff5555", "#ff0000", "2px solid white", fs))
            else:
                button.setStyleSheet(self._step_button_style(
                    "#cc0000", "#ff0000", "#990000", None, fs))

    def wheelEvent(self, event):
        """
        Handle mouse wheel events to scroll horizontally instead of vertically.

        Args:
            event: QWheelEvent containing scroll information
        """
        # Get the horizontal scrollbar
        h_scrollbar = self.scroll_area.horizontalScrollBar()

        # Convert vertical wheel movement to horizontal scrolling
        # event.angleDelta().y() is positive when scrolling up, negative when scrolling down
        delta = event.angleDelta().y()
        h_scrollbar.setValue(h_scrollbar.value() - delta)

        # Accept the event to prevent it from propagating
        event.accept()

    def set_visible_animated(self, visible):
        """
        Show or hide the step navigator.

        Args:
            visible: True to show, False to hide
        """
        # For now, just show/hide
        # TODO: Add smooth slide animation in future
        self.setVisible(visible)

    def scroll_to_step(self, step_index):
        """
        Smoothly scroll to make a specific step visible.

        Args:
            step_index: Step index (accounts for offset when no intro is shown)
        """
        button_index = step_index - self._step_offset
        if button_index < 0 or button_index >= len(self.step_buttons):
            return

        # Get the target button
        target_button = self.step_buttons[button_index]

        # Calculate the target scroll position
        # We want to center the button in the visible area if possible
        button_x = target_button.x()
        button_width = target_button.width()
        viewport_width = self.scroll_area.viewport().width()

        # Calculate scroll position to center the button
        target_scroll = button_x - (viewport_width // 2) + (button_width // 2)

        # Get current scrollbar
        h_scrollbar = self.scroll_area.horizontalScrollBar()

        # Clamp to valid range
        target_scroll = max(h_scrollbar.minimum(), min(target_scroll, h_scrollbar.maximum()))

        # Create smooth scroll animation
        self.scroll_animation = QPropertyAnimation(h_scrollbar, b"value")
        self.scroll_animation.setDuration(800)  # 800ms smooth scroll
        self.scroll_animation.setStartValue(h_scrollbar.value())
        self.scroll_animation.setEndValue(target_scroll)
        self.scroll_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.scroll_animation.start()

    def animate_button_insert(self, step_index):
        """Animate a newly inserted button with a width expand + fade in.

        The button grows from zero width to its full size while fading in,
        pushing neighboring buttons aside smoothly.

        Call after load_steps to animate the newly inserted button.
        """
        button_index = step_index - self._step_offset
        if button_index < 0 or button_index >= len(self.step_buttons):
            return

        # Stop any previous insert animation and restore its button
        if self._insert_anim is not None:
            self._insert_anim.stop()
            self._insert_anim = None
        if self._insert_opacity is not None:
            self._insert_opacity.setOpacity(1.0)

        btn = self.step_buttons[button_index]
        full_width = 50

        # --- Opacity: fade from 0 to 1 ---
        effect = QGraphicsOpacityEffect(btn)
        effect.setOpacity(0.0)
        btn.setGraphicsEffect(effect)
        self._insert_opacity = effect

        fade_anim = QPropertyAnimation(effect, b"opacity")
        fade_anim.setDuration(300)
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.OutCubic)

        # --- Width: expand from 0 to full_width ---
        btn.setMinimumWidth(0)
        btn.setMaximumWidth(0)

        width_anim = QPropertyAnimation(btn, b"maximumWidth")
        width_anim.setDuration(300)
        width_anim.setStartValue(0)
        width_anim.setEndValue(full_width)
        width_anim.setEasingCurve(QEasingCurve.OutCubic)

        # Also animate minimumWidth so the layout fully collapses at start
        min_width_anim = QPropertyAnimation(btn, b"minimumWidth")
        min_width_anim.setDuration(300)
        min_width_anim.setStartValue(0)
        min_width_anim.setEndValue(full_width)
        min_width_anim.setEasingCurve(QEasingCurve.OutCubic)

        # Run all animations in parallel
        group = QParallelAnimationGroup(self)
        group.addAnimation(fade_anim)
        group.addAnimation(width_anim)
        group.addAnimation(min_width_anim)
        group.finished.connect(lambda: btn.setFixedSize(full_width, 45))
        self._insert_anim = group
        group.start()

    def animate_highlight_fade(self, step_indices):
        """Highlight inserted step buttons blue, hold, then smoothly fade to red.

        Holds blue for 1.5s, then uses QVariantAnimation to interpolate
        the color smoothly from blue to red over 1.2s.

        Args:
            step_indices: list of 1-based nav indices to highlight
        """
        # Stop any existing highlight animation
        if self._highlight_anim is not None:
            self._highlight_anim.stop()
            self._highlight_anim = None

        self._highlighted_steps.update(step_indices)
        self._update_active_step()
        # Hold blue for 1.5s, then begin smooth fade
        QTimer.singleShot(1500, self._start_highlight_fade)

    def _start_highlight_fade(self):
        """Begin smooth QVariantAnimation from blue to red."""
        if not self._highlighted_steps:
            return

        anim = QVariantAnimation(self)
        anim.setStartValue(QColor("#2266cc"))
        anim.setEndValue(QColor("#cc0000"))
        anim.setDuration(1200)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.valueChanged.connect(self._on_highlight_color_changed)
        anim.finished.connect(self._on_highlight_fade_done)
        self._highlight_anim = anim
        anim.start()

    def _on_highlight_color_changed(self, color):
        """Apply the interpolated color to all highlighted buttons each frame."""
        bg = color.name()
        hover = color.lighter(120).name()
        pressed = color.darker(120).name()
        for idx in self._highlighted_steps:
            btn_idx = idx - self._step_offset
            if 0 <= btn_idx < len(self.step_buttons):
                btn = self.step_buttons[btn_idx]
                is_intro = (idx == 0)
                fs = 20 if is_intro else 18
                btn.setStyleSheet(self._step_button_style(
                    bg, hover, pressed, None, fs))

    def _on_highlight_fade_done(self):
        """Clean up after the fade animation completes."""
        self._highlighted_steps.clear()
        self._highlight_anim = None
        self._update_active_step()

    def animate_button_delete(self, step_indices, on_finished):
        """Animate deleted buttons fading out while survivors slide into place.

        Runs a single animation phase: all buttons are removed from layout
        control upfront, deleted buttons fade out, and survivors slide to
        their final positions simultaneously.  On completion the deleted
        buttons are destroyed, survivors are re-added to the layout, and
        on_finished is called.

        Args:
            step_indices: List of 1-based step indices to delete.
            on_finished: Callable invoked after cleanup completes.
        """
        # Stop any previous delete animation
        if self._delete_anim is not None:
            self._delete_anim.stop()
            self._delete_anim = None

        # Identify which button indices are being deleted
        btn_indices = set()
        for step_idx in step_indices:
            bi = step_idx - self._step_offset
            if 0 <= bi < len(self.step_buttons):
                btn_indices.add(bi)

        # Split into survivors and deleted, recording positions
        survivors = []
        deleted = []
        for i, btn in enumerate(self.step_buttons):
            pos = btn.pos()
            if i in btn_indices:
                deleted.append(btn)
            else:
                survivors.append((btn, pos))

        # Remove ALL buttons from layout (free positioning)
        for btn in self.step_buttons:
            self.button_layout.removeWidget(btn)

        # Calculate target positions for survivors
        margin_left = self.button_layout.contentsMargins().left()
        spacing = self.button_layout.spacing()
        targets = []
        x = margin_left
        for _ in survivors:
            targets.append(QPoint(x, 0))
            x += 50 + spacing

        # Build combined animation group
        group = QParallelAnimationGroup(self)

        # Deleted buttons: fade out in place
        for btn in deleted:
            effect = QGraphicsOpacityEffect(btn)
            effect.setOpacity(1.0)
            btn.setGraphicsEffect(effect)

            fade = QPropertyAnimation(effect, b"opacity")
            fade.setDuration(300)
            fade.setStartValue(1.0)
            fade.setEndValue(0.0)
            fade.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(fade)

        # Survivors: slide to final positions
        for i, (btn, old_pos) in enumerate(survivors):
            if old_pos != targets[i]:
                anim = QPropertyAnimation(btn, b"pos")
                anim.setDuration(300)
                anim.setStartValue(old_pos)
                anim.setEndValue(targets[i])
                anim.setEasingCurve(QEasingCurve.InOutQuad)
                group.addAnimation(anim)

        def _finish():
            # Destroy deleted buttons
            for bi in sorted(btn_indices, reverse=True):
                btn = self.step_buttons.pop(bi)
                btn.removeEventFilter(self)
                btn.deleteLater()

            # Renumber survivors
            for i, btn in enumerate(self.step_buttons):
                step_idx = i + self._step_offset
                if step_idx > 0:
                    btn.setText(str(step_idx))

            # Update container width
            n = len(self.step_buttons)
            container_width = (50 * n) + (8 * max(n - 1, 0)) + 10
            self.button_container.setFixedWidth(container_width)

            # Re-add survivors to layout
            for btn in self.step_buttons:
                self.button_layout.addWidget(btn)

            self._delete_anim = None
            on_finished()

        if group.animationCount() == 0:
            _finish()
            return

        group.finished.connect(_finish)
        self._delete_anim = group
        group.start()

    def animate_step_move(self, from_step, to_step):
        """Animate a step button sliding from one position to another.

        Temporarily removes all buttons from the layout, reorders them,
        and animates each affected button sliding to its new position.

        Args:
            from_step: 1-based step index of the button being moved.
            to_step: 1-based step index of the target position.
        """
        from_btn_idx = from_step - self._step_offset
        to_btn_idx = to_step - self._step_offset

        if from_btn_idx == to_btn_idx:
            return
        if from_btn_idx < 0 or from_btn_idx >= len(self.step_buttons):
            return

        # Stop any previous move animation
        if self._move_anim is not None:
            self._move_anim.stop()
            self._move_anim = None

        # Record old positions keyed by button identity
        old_pos = {id(btn): btn.pos() for btn in self.step_buttons}

        # Reorder the button list
        btn = self.step_buttons.pop(from_btn_idx)
        to_btn_idx = max(0, min(to_btn_idx, len(self.step_buttons)))
        self.step_buttons.insert(to_btn_idx, btn)

        # Remove all buttons from layout (they stay as children of container)
        for b in self.step_buttons:
            self.button_layout.removeWidget(b)

        # Position each button at its old position (absolute within container)
        for b in self.step_buttons:
            b.move(old_pos[id(b)])

        # Renumber button labels
        for i, b in enumerate(self.step_buttons):
            step_idx = i + self._step_offset
            if step_idx > 0:
                b.setText(str(step_idx))

        # Calculate target positions manually
        margin_left = self.button_layout.contentsMargins().left()
        spacing = self.button_layout.spacing()
        targets = {}
        x = margin_left
        for b in self.step_buttons:
            targets[id(b)] = QPoint(x, 0)
            x += 50 + spacing  # button width = 50

        # Animate buttons that changed position
        group = QParallelAnimationGroup(self)
        for b in self.step_buttons:
            bid = id(b)
            if old_pos[bid] != targets[bid]:
                anim = QPropertyAnimation(b, b"pos")
                anim.setDuration(300)
                anim.setStartValue(old_pos[bid])
                anim.setEndValue(targets[bid])
                anim.setEasingCurve(QEasingCurve.InOutQuad)
                group.addAnimation(anim)

        # On completion, re-add buttons to layout in new order
        def _finish():
            for b in self.step_buttons:
                self.button_layout.addWidget(b)
            self._move_anim = None

        if group.animationCount() == 0:
            _finish()
            return

        group.finished.connect(_finish)
        self._move_anim = group
        group.start()

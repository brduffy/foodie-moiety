"""Video player view component."""

import platform

from PySide6.QtCore import QEvent, QPoint, QSize, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.recipe_data import SpeedRange
from utils.database import delete_speed_range, load_speed_ranges, save_speed_range
from utils.helpers import create_white_icon, platform_icon
from widgets.custom_widgets import ClickableSlider, SpeedRangeSlider


class CustomTooltip(QWidget):
    """A custom tooltip widget that can be positioned consistently across platforms."""

    def __init__(self, text, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        label = QLabel(text)
        label.setStyleSheet("color: white; font-size: 11px;")
        layout.addWidget(label)

        self.setStyleSheet(
            "CustomTooltip { background-color: #333; border: 1px solid #555; border-radius: 4px; }"
        )

    def show_above(self, widget):
        """Show the tooltip centered above the given widget."""
        pos = widget.mapToGlobal(QPoint(widget.width() // 2, 0))
        pos.setX(pos.x() - self.sizeHint().width() // 2)
        pos.setY(pos.y() - self.sizeHint().height() - 5)
        self.move(pos)
        self.show()


class VideoPlayer(QWidget):
    """
    Video player component with playback controls.

    Features:
    - Play/pause, skip forward/backward
    - Toggleable skip intervals (5s/10s)
    - Volume control with mute
    - Clickable seek slider
    - Keyboard shortcuts (Space, Arrow keys, F)
    - Double-click fullscreen toggle
    - Platform-specific fullscreen handling (Windows/macOS)
    """

    stop_requested = Signal()

    def __init__(self, video_path):
        super().__init__()

        self.is_windows = platform.system() == "Windows"
        self.control_height = 65
        self.video_ratio = 0.5625
        self.is_transitioning = False
        self.skip_amount = 5000
        self._video_path = video_path
        self._speed_ranges = []
        self._pending_marker_ms = None
        self._user_muted = False
        self._controls_visible = True

        # Multimedia setup
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.7)
        self.media_player.setAudioOutput(self.audio_output)

        # QGraphicsVideoItem renders video inside a QGraphicsScene,
        # allowing controls to be overlaid as proxy widgets that paint
        # above the video — unlike QVideoWidget's native surface.
        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene)
        self._view.setFrameStyle(0)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setStyleSheet("background-color: black;")
        self._view.setMouseTracking(True)
        self._view.viewport().setMouseTracking(True)
        self._view.installEventFilter(self)
        self._view.viewport().installEventFilter(self)

        self.video_item = QGraphicsVideoItem()
        self._scene.addItem(self.video_item)
        self.media_player.setVideoOutput(self.video_item)

        # Main UI layout
        self.setStyleSheet("background-color: black;")
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.main_layout.addWidget(self._view)

        # Create controls
        self._setup_controls()

        # Connect signals
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.playbackStateChanged.connect(self.update_play_icon)
        self.media_player.playbackStateChanged.connect(self._update_marker_btn_state)
        self.media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.media_player.tracksChanged.connect(self.sync_ratio_to_video)
        self.media_player.tracksChanged.connect(self._fit_video)

        # Load video (video_path is None on fresh install — videos loaded per-recipe)
        if video_path:
            self.media_player.setSource(QUrl.fromLocalFile(video_path))

    def load_video(self, video_path):
        """Load a new video source into the player."""
        self.media_player.stop()
        self.media_player.setPlaybackRate(1.0)
        self.media_player.setSource(QUrl.fromLocalFile(video_path))
        self.seek_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        # Load speed ranges for this video
        self._video_path = video_path
        self._pending_marker_ms = None
        self.seek_slider.clear_pending_marker()
        self._reload_speed_ranges()

    def _setup_controls(self):
        """Create and configure the video control panel."""
        self.controls_widget = QWidget()
        self.controls_widget.setFixedHeight(self.control_height)
        self.controls_widget.setStyleSheet("background: transparent;")
        self.controls_widget.setAttribute(Qt.WA_TranslucentBackground, True)

        # Inner container with rounded bottom corners (macOS window chrome)
        controls_inner = QWidget()
        controls_inner.setObjectName("ControlsInner")
        _corner_radius = "10px"
        controls_inner.setStyleSheet(
            f"QWidget#ControlsInner {{"
            f"  background-color: #121212;"
            f"  border-top: 1px solid #222;"
            f"  border-bottom-left-radius: {_corner_radius};"
            f"  border-bottom-right-radius: {_corner_radius};"
            f"}}"
        )

        outer_layout = QVBoxLayout(self.controls_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        outer_layout.addWidget(controls_inner)

        controls_layout = QHBoxLayout(controls_inner)
        controls_layout.setContentsMargins(15, 0, 15, 0)
        controls_layout.setSpacing(1)

        # Shared tooltip for all control buttons
        self._btn_tooltip = CustomTooltip("")
        self._tooltip_map = {}

        # Stop button
        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(create_white_icon("stop"))
        self.stop_btn.setIconSize(QSize(22, 22))
        self.stop_btn.clicked.connect(self._on_stop)
        self._register_tooltip(self.stop_btn, "Stop / Return to Recipe")

        # Skip backward button
        self.back_btn = QPushButton()
        self.back_btn.setIcon(create_white_icon("skip_b"))
        self.back_btn.setIconSize(QSize(26, 26))
        self.back_btn.clicked.connect(lambda: self.skip_video(-1))
        self._register_tooltip(self.back_btn, "Skip Back")

        # Play/pause button
        self.play_button = QPushButton()
        self.play_button.setIcon(create_white_icon("play"))
        self.play_button.setIconSize(QSize(36, 36))
        self.play_button.clicked.connect(self.toggle_play)
        self._register_tooltip(self.play_button, "Play / Pause")

        # Skip forward button
        self.fwd_btn = QPushButton()
        self.fwd_btn.setIcon(create_white_icon("skip_f"))
        self.fwd_btn.setIconSize(QSize(26, 26))
        self.fwd_btn.clicked.connect(lambda: self.skip_video(1))
        self._register_tooltip(self.fwd_btn, "Skip Forward")

        # Toggle skip amount button (1s/5s/10s)
        self.toggle_skip_btn = QPushButton("5s")
        self.toggle_skip_btn.setFixedSize(40, 24)
        self.toggle_skip_btn.setStyleSheet(
            "QPushButton { background-color: #2a2a2a; color: #bbb; "
            "border-radius: 3px; font-weight: bold; font-size: 10px; }"
        )
        self.toggle_skip_btn.clicked.connect(self.toggle_skip_amount)
        self._register_tooltip(self.toggle_skip_btn, "Skip Duration")

        # Marker button (set speed-up range start/end while paused)
        self.marker_btn = QPushButton("Mark")
        self.marker_btn.setFixedSize(40, 24)
        self.marker_btn.setStyleSheet(
            "QPushButton { background-color: #2a2a2a; color: #bbb; "
            "border-radius: 3px; font-weight: bold; font-size: 10px; }"
            "QPushButton:disabled { color: #555; }"
        )
        self.marker_btn.setEnabled(False)
        self.marker_btn.clicked.connect(self._on_marker_pressed)
        self._register_tooltip(self.marker_btn, "Set speed range start/end while paused")

        # Playback rate button (cycles 2x→4x→6x→8x for the current speed range)
        self._rate_options = [2.0, 4.0, 6.0, 8.0]
        self._rate_idx = 1  # default 4x
        self.rate_btn = QPushButton("4x")
        self.rate_btn.setFixedSize(32, 24)
        self.rate_btn.setStyleSheet(
            "QPushButton { background-color: #2a2a2a; color: #bbb; "
            "border-radius: 3px; font-weight: bold; font-size: 10px; }"
            "QPushButton:disabled { color: #555; }"
        )
        self.rate_btn.setEnabled(False)
        self.rate_btn.clicked.connect(self._on_rate_cycle)
        self._register_tooltip(self.rate_btn, "Cycle playback rate for this speed range")

        # Seek slider with speed range overlays
        self.seek_slider = SpeedRangeSlider(Qt.Horizontal)
        self.seek_slider.sliderMoved.connect(self.set_position)

        # Remove range button (enabled when position is inside a speed range)
        self.remove_range_btn = QPushButton("Del")
        self.remove_range_btn.setFixedSize(40, 24)
        self.remove_range_btn.setStyleSheet(
            "QPushButton { background-color: #2a2a2a; color: #bbb; "
            "border-radius: 3px; font-weight: bold; font-size: 10px; }"
            "QPushButton:disabled { color: #555; }"
        )
        self.remove_range_btn.setEnabled(False)
        self.remove_range_btn.clicked.connect(self._on_remove_range)
        self._register_tooltip(self.remove_range_btn, "Delete Range")

        # Volume button
        self.vol_button = QPushButton()
        self.vol_button.setIcon(platform_icon("speaker.wave.3.fill", point_size=20, color="#ffffff"))
        self.vol_button.setIconSize(QSize(20, 20))
        self.vol_button.clicked.connect(self.toggle_mute)
        self._register_tooltip(self.vol_button, "Mute / Unmute")

        # Volume slider
        self.vol_slider = ClickableSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.valueChanged.connect(self.set_volume)

        # Time label
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(
            "color: white; font-family: Arial; font-weight: bold; font-size: 11px;"
        )

        # Add all controls to layout
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addWidget(self.back_btn)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.fwd_btn)
        controls_layout.addSpacing(5)
        controls_layout.addWidget(self.toggle_skip_btn)
        controls_layout.addSpacing(3)
        controls_layout.addWidget(self.marker_btn)
        controls_layout.addWidget(self.rate_btn)
        controls_layout.addWidget(self.remove_range_btn)
        controls_layout.addSpacing(3)
        controls_layout.addWidget(self.seek_slider)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(self.vol_button)
        controls_layout.addWidget(self.vol_slider)
        controls_layout.addWidget(self.time_label)
        # Add controls as a proxy widget in the scene — always paints
        # above the video item, moves with the window, no z-order issues.
        self._controls_proxy = self._scene.addWidget(self.controls_widget)

        # Auto-hide controls after 3 seconds of no mouse activity
        self._controls_hide_timer = QTimer(self)
        self._controls_hide_timer.setSingleShot(True)
        self._controls_hide_timer.setInterval(3000)
        self._controls_hide_timer.timeout.connect(self._hide_controls)

    def sync_ratio_to_video(self):
        """Standardizes height to match video aspect ratio."""
        # Get the top-level window (MainWindow)
        main_window = self.window()
        if main_window.isFullScreen() or self.is_transitioning:
            return

        size = self.media_player.videoSink().videoSize()
        if size.isValid() and size.width() > 0:
            self.video_ratio = size.height() / size.width()

    def on_window_state_change(self):
        """
        Called when the parent window state changes (e.g., fullscreen toggle).

        Handles platform-specific transition logic:
        - Windows: Multiple aggressive snapping timers (50ms, 250ms, 500ms)
        - macOS: Single 500ms delay for smooth OS animation
        """
        main_window = self.window()
        self.is_transitioning = True
        # Unlock any fixed heights on main window
        main_window.setMinimumHeight(0)
        main_window.setMaximumHeight(16777215)

        if self.is_windows:
            # Windows needs more aggressive snapping (no transition animation)
            QTimer.singleShot(50, self.end_transition)
            QTimer.singleShot(250, self.end_transition)
            QTimer.singleShot(500, self.end_transition)
        else:
            # Mac uses 500ms delay to wait for OS zoom animation
            QTimer.singleShot(500, self.end_transition)

    def end_transition(self):
        """Mark transition as complete."""
        self.is_transitioning = False
        self._fit_video()

    def toggle_full_screen(self):
        """Toggle between fullscreen and normal window mode."""
        main_window = self.window()
        if main_window.isFullScreen():
            main_window.showNormal()
        else:
            main_window.showFullScreen()

    def resizeEvent(self, event):
        """Fit video and reposition controls on resize."""
        super().resizeEvent(event)
        self._fit_video()

    def _fit_video(self):
        """Scale video item to fill the view and position controls."""
        view_size = self._view.viewport().size()
        self.video_item.setSize(QSizeF(view_size.width(), view_size.height()))
        self._scene.setSceneRect(0, 0, view_size.width(), view_size.height())
        self.controls_widget.setFixedWidth(view_size.width())
        self._controls_proxy.setPos(
            0, view_size.height() - self.control_height + 2)

    def _on_view_event(self, event):
        """Handle mouse events on the graphics view."""
        etype = event.type()
        if etype == QEvent.MouseButtonDblClick:
            self.toggle_full_screen()
            return True
        elif etype == QEvent.MouseMove:
            if not self._controls_visible:
                self._show_controls()
            else:
                # Don't restart hide timer if mouse is over the controls
                pos = event.position() if hasattr(event, 'position') else event.pos()
                view_pos = self._view.mapToScene(pos.toPoint())
                ctrl_rect = self._controls_proxy.sceneBoundingRect()
                if not ctrl_rect.contains(view_pos):
                    self._controls_hide_timer.start()
                else:
                    self._controls_hide_timer.stop()
        return False

    def showEvent(self, event):
        """Show controls when video view appears."""
        super().showEvent(event)
        self._fit_video()
        self._show_controls()

    def hideEvent(self, event):
        """Stop timers when video view is hidden."""
        super().hideEvent(event)
        self._controls_hide_timer.stop()

    def _show_controls(self):
        """Show the control bar and restart the auto-hide timer."""
        self._controls_visible = True
        self._controls_proxy.show()
        self._controls_hide_timer.start()

    def _hide_controls(self):
        """Hide the control bar (auto-hide after inactivity)."""
        self._controls_visible = False
        self._controls_proxy.hide()

    def toggle_skip_amount(self):
        """Cycle skip amount through 1s, 5s, 10s."""
        cycle = {1000: 5000, 5000: 10000, 10000: 1000}
        self.skip_amount = cycle[self.skip_amount]
        self.toggle_skip_btn.setText(f"{self.skip_amount // 1000}s")

    def set_volume(self, value):
        """Set audio volume (0-100)."""
        self.audio_output.setVolume(value / 100)
        self.update_vol_icon()

    def toggle_mute(self):
        """Toggle audio mute state."""
        self._user_muted = not self._user_muted
        self.audio_output.setMuted(self._user_muted)
        self.update_vol_icon()

    def update_vol_icon(self):
        """Update volume button icon based on mute state and volume level."""
        sf_name = (
            "speaker.slash.fill"
            if self.audio_output.isMuted() or self.vol_slider.value() == 0
            else "speaker.wave.3.fill"
        )
        self.vol_button.setIcon(platform_icon(sf_name, point_size=20, color="#ffffff"))

    def skip_video(self, direction):
        """
        Skip forward or backward by the configured skip amount.

        Args:
            direction: 1 for forward, -1 for backward
        """
        target = max(0, self.media_player.position() + (direction * self.skip_amount))
        self.media_player.setPosition(target)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts for video control."""
        if event.key() == Qt.Key_Space:
            self.toggle_play()
        elif event.key() == Qt.Key_Right:
            self.skip_video(1)
        elif event.key() == Qt.Key_Left:
            self.skip_video(-1)
        elif event.key() == Qt.Key_F:
            self.toggle_full_screen()
        super().keyPressEvent(event)

    def _register_tooltip(self, button, text):
        """Register a button for shared custom tooltip on hover."""
        self._tooltip_map[button] = text
        button.installEventFilter(self)

    def eventFilter(self, obj, event):
        """Handle graphics view mouse events and button tooltips."""
        if obj is self._view or obj is self._view.viewport():
            if self._on_view_event(event):
                return True
        elif hasattr(self, "_tooltip_map") and obj in self._tooltip_map:
            if event.type() == QEvent.Enter:
                self._btn_tooltip.findChild(QLabel).setText(self._tooltip_map[obj])
                self._btn_tooltip.adjustSize()
                self._btn_tooltip.show_above(obj)
            elif event.type() == QEvent.Leave:
                self._btn_tooltip.hide()
        return super().eventFilter(obj, event)

    def _on_stop(self):
        """Stop playback and request return to previous view."""
        self.media_player.stop()
        self.media_player.setPlaybackRate(1.0)
        self.stop_requested.emit()

    def _on_media_status_changed(self, status):
        """Return to previous view when video finishes playing."""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop_requested.emit()

    def toggle_play(self):
        """Toggle between play and pause states."""
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def update_play_icon(self, state):
        """Update play button icon based on playback state."""
        icon = "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play"
        self.play_button.setIcon(create_white_icon(icon))

    def set_position(self, pos):
        """Set video position in milliseconds."""
        self.media_player.setPosition(pos)

    def position_changed(self, pos):
        """Update UI when video position changes."""
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(pos)
        self.update_label()
        # Speed range playback rate switching + button states
        current_range = self.seek_slider.range_at(pos) if self._speed_ranges else None
        self.remove_range_btn.setEnabled(
            self._pending_marker_ms is not None or bool(self._speed_ranges)
        )
        self._update_marker_btn_state()
        # Rate button enabled only inside a speed range
        self.rate_btn.setEnabled(current_range is not None)
        if current_range is not None:
            rate = current_range.playback_rate
            self.rate_btn.setText(f"{int(rate)}x" if rate == int(rate) else f"{rate}x")
        if self._speed_ranges:
            current_rate = self.media_player.playbackRate()
            if current_range is not None and current_rate == 1.0:
                self.media_player.setPlaybackRate(current_range.playback_rate)
                self.audio_output.setMuted(True)
            elif current_range is None and current_rate != 1.0:
                self.media_player.setPlaybackRate(1.0)
                self.audio_output.setMuted(self._user_muted)

    def duration_changed(self, dur):
        """Update seek slider range when video duration is known."""
        self.seek_slider.setRange(0, dur)

    def update_label(self):
        """Update the time label with current position and duration."""
        p, d = (
            self.media_player.position() // 1000,
            self.media_player.duration() // 1000,
        )
        self.time_label.setText(
            f"{p // 60:02d}:{p % 60:02d} / {d // 60:02d}:{d % 60:02d}"
        )

    # --- Speed Range Markers ---

    def _update_marker_btn_state(self, state=None):
        """Enable marker button only when paused and outside a speed range."""
        if state is None:
            state = self.media_player.playbackState()
        paused = state == QMediaPlayer.PlaybackState.PausedState
        pos = self.media_player.position()
        inside_range = self.seek_slider.range_at(pos) is not None if self._speed_ranges else False
        self.marker_btn.setEnabled(paused and not inside_range)

    def _on_marker_pressed(self):
        """Handle marker button press: set start or complete a range."""
        pos = self.media_player.position()
        if self._pending_marker_ms is None:
            # First marker — set pending start
            self._pending_marker_ms = pos
            self.seek_slider.set_pending_marker(pos)
            self.remove_range_btn.setEnabled(True)
        else:
            # Second marker — create range
            start = min(self._pending_marker_ms, pos)
            end = max(self._pending_marker_ms, pos)
            if end > start:
                save_speed_range(self._video_path, start, end)
            self._pending_marker_ms = None
            self.seek_slider.clear_pending_marker()
            self._reload_speed_ranges()

    def _on_rate_cycle(self):
        """Cycle the playback rate for the speed range at the current position."""
        pos = self.media_player.position()
        current_range = self.seek_slider.range_at(pos) if self._speed_ranges else None
        if current_range is None:
            return
        # Find current rate in options and advance to the next
        try:
            idx = self._rate_options.index(current_range.playback_rate)
        except ValueError:
            idx = 0
        new_rate = self._rate_options[(idx + 1) % len(self._rate_options)]
        # Persist to DB
        save_speed_range(self._video_path, current_range.start_ms,
                         current_range.end_ms, new_rate)
        self._reload_speed_ranges()
        # Apply immediately if playing
        if self.media_player.playbackRate() != 1.0:
            self.media_player.setPlaybackRate(new_rate)
        # Update button label
        self.rate_btn.setText(f"{int(new_rate)}x")

    def _on_remove_range(self):
        """Clear pending marker, or show range menu for deletion."""
        if self._pending_marker_ms is not None:
            self._pending_marker_ms = None
            self.seek_slider.clear_pending_marker()
            return
        if not self._speed_ranges:
            return
        # Save position to restore if menu is dismissed without selection
        self._pre_menu_pos = self.media_player.position()
        current_range = self.seek_slider.range_at(self._pre_menu_pos)

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #2a2a2a; color: #ccc; border: 1px solid #4a4a4a; "
            "border-radius: 4px; padding: 4px 0; font-size: 13px; }"
            "QMenu::item { padding: 6px 20px; }"
            "QMenu::item:selected { background-color: #4a4a4a; color: white; }"
        )

        default_action = None
        for r in self._speed_ranges:
            s_min, s_sec = divmod(r.start_ms // 1000, 60)
            e_min, e_sec = divmod(r.end_ms // 1000, 60)
            label = f"{s_min:02d}:{s_sec:02d}  \u2013  {e_min:02d}:{e_sec:02d}"
            action = menu.addAction(label)
            action.setData(r.start_ms)
            if current_range is not None and r.start_ms == current_range.start_ms:
                default_action = action

        menu.hovered.connect(self._on_range_menu_hovered)
        if default_action:
            menu.setActiveAction(default_action)
        chosen = menu.exec(
            self.remove_range_btn.mapToGlobal(
                QPoint(0, -menu.sizeHint().height())
            )
        )

        if chosen:
            delete_speed_range(self._video_path, chosen.data())
            self._reload_speed_ranges()
        else:
            # Restore original position if dismissed
            self.media_player.setPosition(self._pre_menu_pos)

    def _on_range_menu_hovered(self, action):
        """Seek to the start of the hovered range in the delete menu."""
        start_ms = action.data()
        if start_ms is not None:
            self.media_player.setPosition(start_ms)

    def _reload_speed_ranges(self):
        """Reload speed ranges from DB and update the slider."""
        rows = load_speed_ranges(self._video_path)
        self._speed_ranges = [SpeedRange(*r) for r in rows]
        self.seek_slider.set_speed_ranges(self._speed_ranges)
        # Reset rate — next position_changed tick will re-apply if still in a range
        self.media_player.setPlaybackRate(1.0)
        self.audio_output.setMuted(self._user_muted)

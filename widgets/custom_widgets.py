"""Custom widget components used across the application."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QSlider, QStyle


class ClickableSlider(QSlider):
    """Slider that allows clicking anywhere on the track to jump to that position."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            new_value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), event.position().x(), self.width()
            )
            self.setValue(new_value)
            self.sliderMoved.emit(new_value)
        super().mousePressEvent(event)


class SpeedRangeSlider(ClickableSlider):
    """Seek slider with visual speed-range overlays and marker support.

    Draws colored segments on the groove for speed-up zones.
    """

    # Groove / handle dimensions
    GROOVE_HEIGHT = 4
    HANDLE_RADIUS = 6

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._speed_ranges = []  # list of SpeedRange dataclass instances
        self._pending_marker_ms = None
        # Disable default styling so paintEvent has full control
        self.setStyleSheet("QSlider { background: transparent; }")

    def set_speed_ranges(self, ranges):
        """Update the list of speed ranges and repaint."""
        self._speed_ranges = list(ranges)
        self.update()

    def set_pending_marker(self, ms):
        """Show a pending (unpaired) marker tick on the groove."""
        self._pending_marker_ms = ms
        self.update()

    def clear_pending_marker(self):
        """Remove the pending marker tick."""
        self._pending_marker_ms = None
        self.update()

    def _value_to_x(self, value):
        """Convert a slider value to an x pixel coordinate."""
        if self.maximum() == self.minimum():
            return self.HANDLE_RADIUS
        ratio = (value - self.minimum()) / (self.maximum() - self.minimum())
        usable = self.width() - 2 * self.HANDLE_RADIUS
        return int(self.HANDLE_RADIUS + ratio * usable)

    def paintEvent(self, event):
        """Custom paint: groove, range overlays, pending marker, handle."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cy = self.height() // 2
        gh = self.GROOVE_HEIGHT
        hr = self.HANDLE_RADIUS

        # 1. Groove background
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3a3a3a"))
        p.drawRoundedRect(hr, cy - gh // 2, self.width() - 2 * hr, gh, 2, 2)

        # 2. Speed range segments (amber)
        p.setBrush(QColor("#cc8800"))
        for r in self._speed_ranges:
            x1 = self._value_to_x(r.start_ms)
            x2 = self._value_to_x(r.end_ms)
            p.drawRoundedRect(x1, cy - gh // 2, x2 - x1, gh, 2, 2)

        # 3. Handle
        hx = self._value_to_x(self.value())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(hx - hr, cy - hr, hr * 2, hr * 2)

        # 4. Pending marker tick (drawn last so it's visible over the handle)
        if self._pending_marker_ms is not None:
            mx = self._value_to_x(self._pending_marker_ms)
            p.setPen(QColor("#ff4444"))
            p.setBrush(QColor("#ff4444"))
            p.drawRect(mx - 1, cy - 10, 2, 20)

        p.end()

    def range_at(self, value):
        """Return the SpeedRange containing value, or None."""
        for r in self._speed_ranges:
            if r.start_ms <= value <= r.end_ms:
                return r
        return None


class DoubleClickVideoWidget(QVideoWidget):
    """Video widget that toggles fullscreen on double-click."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.main_window.toggle_full_screen()
        super().mouseDoubleClickEvent(event)

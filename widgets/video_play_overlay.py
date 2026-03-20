"""Floating circular play button overlay for video-equipped recipe steps."""

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QWidget


class VideoPlayOverlay(QWidget):
    """A round blue circle with a white play icon.

    Emits ``clicked`` when the user clicks on it. Stays visible over the
    recipe detail background image to indicate a video is available.
    """

    clicked = Signal()

    # Circle fill colour (semi-transparent)
    FILL_COLOR = QColor(30, 120, 220, 180)
    # Play triangle colour (semi-transparent)
    ICON_COLOR = QColor(255, 255, 255, 200)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        size = min(self.width(), self.height())
        # Center the circle in the widget
        cx = self.width() / 2
        cy = self.height() / 2
        radius = size / 2

        # Draw filled circle
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self.FILL_COLOR))
        painter.drawEllipse(QRectF(cx - radius, cy - radius, size, size))

        # Draw play triangle (slightly offset right for visual centering)
        tri_size = radius * 0.8
        offset_x = tri_size * 0.1  # nudge right to visually center
        left_x = cx - tri_size * 0.35 + offset_x
        right_x = cx + tri_size * 0.5 + offset_x
        top_y = cy - tri_size * 0.45
        bottom_y = cy + tri_size * 0.45

        triangle = QPolygonF([
            QPointF(left_x, top_y),
            QPointF(right_x, cy),
            QPointF(left_x, bottom_y),
        ])
        painter.setBrush(QBrush(self.ICON_COLOR))
        painter.drawPolygon(triangle)

        painter.end()

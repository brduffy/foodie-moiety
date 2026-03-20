"""Pulsing skeleton placeholder for loading states."""

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QWidget


class SkeletonWidget(QWidget):
    """A pulsing rounded rectangle (or circle) placeholder.

    Parameters:
        width:  fixed width (0 = use layout stretch)
        height: fixed height
        radius: corner radius for rounded rect
        circle: if True, paint as ellipse
    """

    _BASE = QColor("#333333")
    _HI = QColor("#484848")

    def __init__(self, width=0, height=0, radius=6, circle=False, parent=None):
        super().__init__(parent)
        self._pulse = 0.0
        self._radius = radius
        self._circle = circle
        self._anim = None
        if width > 0:
            self.setFixedWidth(width)
        if height > 0:
            self.setFixedHeight(height)
        self.setStyleSheet("background: transparent;")
        # Defer animation start so widget is parented before first paint
        QTimer.singleShot(0, self._start_pulse)

    # --- Property for QPropertyAnimation ---

    def _get_pulse(self):
        return self._pulse

    def _set_pulse(self, val):
        self._pulse = val
        self.update()

    pulse_value = Property(float, _get_pulse, _set_pulse)

    # --- Animation ---

    def _start_pulse(self):
        anim = QPropertyAnimation(self, b"pulse_value")
        anim.setDuration(1200)
        anim.setEasingCurve(QEasingCurve.InOutSine)
        anim.setKeyValueAt(0.0, 0.0)
        anim.setKeyValueAt(0.5, 1.0)
        anim.setKeyValueAt(1.0, 0.0)
        anim.setLoopCount(-1)
        self._anim = anim
        anim.start()

    def stop_pulse(self):
        if self._anim:
            self._anim.stop()
            self._anim = None

    # --- Paint ---

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._pulse
        r = self._BASE.red() + t * (self._HI.red() - self._BASE.red())
        g = self._BASE.green() + t * (self._HI.green() - self._BASE.green())
        b = self._BASE.blue() + t * (self._HI.blue() - self._BASE.blue())
        p.setBrush(QBrush(QColor(int(r), int(g), int(b))))
        p.setPen(Qt.NoPen)
        if self._circle:
            p.drawEllipse(self.rect())
        else:
            p.drawRoundedRect(self.rect(), self._radius, self._radius)
        p.end()

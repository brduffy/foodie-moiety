"""Marquee label that scrolls overflowing text on hover."""

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QPauseAnimation,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget


class MarqueeLabel(QWidget):
    """Single-line label that scrolls its text when it overflows.

    Call ``start_scroll()`` / ``stop_scroll()`` from the parent widget's
    enter/leave events.  If the text fits, scrolling is a no-op.
    """

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = text
        self._offset = 0.0
        self._anim_group = None
        self._font = QFont()
        self._font.setPixelSize(13)
        self._font.setWeight(QFont.DemiBold)
        self._color = QColor("white")
        self.setFixedHeight(QFontMetrics(self._font).height() + 2)
        self.setStyleSheet("background: transparent;")

    # --- Public API ---

    def setText(self, text):
        self._text = text
        self._offset = 0.0
        self.update()

    def text(self):
        return self._text

    def setFont(self, font):
        self._font = font
        self.setFixedHeight(QFontMetrics(self._font).height() + 2)
        self.update()

    def setColor(self, color):
        self._color = QColor(color)
        self.update()

    def start_scroll(self):
        """Begin the marquee loop if text overflows."""
        overflow = self._overflow()
        if overflow <= 0:
            return  # Text fits — nothing to scroll
        if self._anim_group is not None:
            return  # Already running
        self._build_animation(overflow)
        self._anim_group.start()

    def stop_scroll(self):
        """Stop scrolling and reset to left-justified."""
        if self._anim_group is not None:
            self._anim_group.stop()
            self._anim_group = None
        self._offset = 0.0
        self.update()

    # --- Property for animation ---

    def _get_offset(self):
        return self._offset

    def _set_offset(self, val):
        self._offset = val
        self.update()

    scroll_offset = Property(float, _get_offset, _set_offset)

    # --- Internal ---

    def _overflow(self):
        fm = QFontMetrics(self._font)
        text_w = fm.horizontalAdvance(self._text)
        return text_w - self.width()

    def _build_animation(self, overflow):
        """Build a sequential loop: pause → scroll left → pause → reset."""
        # Scroll speed: ~60px per second
        duration = max(1500, int(overflow / 60.0 * 1000))

        group = QSequentialAnimationGroup(self)
        group.setLoopCount(-1)  # Infinite loop

        # 1. Pause at start
        group.addAnimation(QPauseAnimation(1000))

        # 2. Scroll left
        scroll = QPropertyAnimation(self, b"scroll_offset")
        scroll.setDuration(duration)
        scroll.setStartValue(0.0)
        scroll.setEndValue(-float(overflow))
        scroll.setEasingCurve(QEasingCurve.Linear)
        group.addAnimation(scroll)

        # 3. Pause at end
        group.addAnimation(QPauseAnimation(1000))

        # 4. Snap back to start (instant)
        reset = QPropertyAnimation(self, b"scroll_offset")
        reset.setDuration(0)
        reset.setStartValue(-float(overflow))
        reset.setEndValue(0.0)
        group.addAnimation(reset)

        self._anim_group = group

    # --- Paint ---

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setClipRect(self.rect())
        p.setFont(self._font)
        p.setPen(self._color)
        fm = QFontMetrics(self._font)
        y = fm.ascent() + 1
        p.drawText(int(self._offset), y, self._text)
        p.end()

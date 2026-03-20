"""Animated overlay icon showing voice control state."""

from __future__ import annotations

import math
from enum import Enum, auto

from PySide6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class VoiceState(Enum):
    HIDDEN = auto()
    LISTENING = auto()
    PROCESSING = auto()
    SUCCESS = auto()
    ERROR = auto()
    PAUSED = auto()


class VoiceIndicator(QWidget):
    """A minimal animated circle that shows voice activity state.

    Designed to be visible from 10 feet away while cooking. Shows:
    - Pulsing blue circle with "f" when listening for a command
    - Spinning arc when processing/thinking
    - Green checkmark on success (auto-dismisses)
    - Red X on error (auto-dismisses)
    """

    # State colours (semi-transparent)
    COLORS = {
        VoiceState.LISTENING:  QColor(0, 120, 212, 220),   # Blue (app accent)
        VoiceState.PROCESSING: QColor(0, 120, 212, 220),
        VoiceState.SUCCESS:    QColor(40, 167, 69, 220),    # Green
        VoiceState.ERROR:      QColor(220, 53, 69, 220),    # Red
        VoiceState.PAUSED:     QColor(218, 165, 32, 220),   # Yellow/gold
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(120, 120)

        self._state = VoiceState.HIDDEN

        # Opacity for fade in/out
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_anim: QPropertyAnimation | None = None

        # Pulse animation (LISTENING)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(80)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)
        self._pulse_phase = 0.0

        # Spin animation (PROCESSING)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(30)
        self._spin_timer.timeout.connect(self._on_spin_tick)
        self._spin_angle = 0.0

        # Auto-dismiss timer (SUCCESS / ERROR / PAUSED)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._auto_dismiss)

        # Cached mic pixmap for PAUSED state
        self._mic_pixmap: QPixmap | None = None
        self._mic_pixmap_size: int = 0

        self.hide()

    # ----- public API -----

    def set_state(self, state: VoiceState) -> None:
        if state == self._state:
            return

        old_state = self._state
        self._state = state

        self._pulse_timer.stop()
        self._spin_timer.stop()
        self._dismiss_timer.stop()

        if state == VoiceState.HIDDEN:
            self._fade_to(0.0, duration=300, on_finished=self.hide)
            return

        if state == VoiceState.LISTENING:
            self._pulse_phase = 0.0
            self._pulse_timer.start()
        elif state == VoiceState.PROCESSING:
            self._spin_angle = 0.0
            self._spin_timer.start()
        elif state == VoiceState.SUCCESS:
            self._dismiss_timer.start(2000)
        elif state == VoiceState.ERROR:
            self._dismiss_timer.start(3000)
        elif state == VoiceState.PAUSED:
            self._dismiss_timer.start(1200)

        # Safety timeout: if stuck in PROCESSING with no result, auto-dismiss
        if state == VoiceState.PROCESSING:
            self._dismiss_timer.start(10000)

        if old_state == VoiceState.HIDDEN:
            self._opacity_effect.setOpacity(0.0)
            self.show()
            self.raise_()
            self._fade_to(1.0, duration=200)

        self.update()

    def show_listening(self) -> None:
        self.set_state(VoiceState.LISTENING)

    def show_processing(self) -> None:
        self.set_state(VoiceState.PROCESSING)

    def show_success(self) -> None:
        self.set_state(VoiceState.SUCCESS)

    def show_error(self) -> None:
        self.set_state(VoiceState.ERROR)

    def show_paused(self) -> None:
        self.set_state(VoiceState.PAUSED)

    def dismiss(self) -> None:
        self.set_state(VoiceState.HIDDEN)

    # ----- animations -----

    def _fade_to(self, target: float, duration: int = 300,
                 on_finished=None) -> None:
        if self._fade_anim is not None:
            self._fade_anim.stop()

        current = self._opacity_effect.opacity()
        remaining = abs(target - current)
        scaled = max(int(duration * remaining), 50)

        anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        anim.setDuration(scaled)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        if on_finished:
            anim.finished.connect(on_finished)

        self._fade_anim = anim
        anim.start()

    def _on_pulse_tick(self) -> None:
        self._pulse_phase += 0.04
        if self._pulse_phase > 1.0:
            self._pulse_phase -= 1.0
        self.update()

    def _on_spin_tick(self) -> None:
        self._spin_angle = (self._spin_angle + 6) % 360
        self.update()

    def _auto_dismiss(self) -> None:
        self.set_state(VoiceState.HIDDEN)

    # ----- painting -----

    def paintEvent(self, event) -> None:
        if self._state == VoiceState.HIDDEN:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        size = min(self.width(), self.height())
        cx = self.width() / 2
        cy = self.height() / 2
        radius = size / 2

        color = self.COLORS.get(self._state, QColor(100, 100, 100, 200))

        if self._state == VoiceState.LISTENING:
            self._paint_listening(painter, cx, cy, radius, color)
        elif self._state == VoiceState.PROCESSING:
            self._paint_processing(painter, cx, cy, radius, color)
        elif self._state == VoiceState.SUCCESS:
            self._paint_success(painter, cx, cy, radius, color)
        elif self._state == VoiceState.ERROR:
            self._paint_error(painter, cx, cy, radius, color)
        elif self._state == VoiceState.PAUSED:
            self._paint_paused(painter, cx, cy, radius, color)

        painter.end()

    def _paint_listening(self, painter: QPainter, cx: float, cy: float,
                         radius: float, color: QColor) -> None:
        pulse = 0.08 * math.sin(self._pulse_phase * 2 * math.pi)
        r = radius * (0.92 + pulse)

        # Outer pulse ring
        ring_r = radius * (1.0 + pulse * 0.5)
        ring_color = QColor(color)
        ring_color.setAlpha(60)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(ring_color))
        painter.drawEllipse(QRectF(cx - ring_r, cy - ring_r,
                                   ring_r * 2, ring_r * 2))

        # Main circle
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # "f" letter
        painter.setPen(QPen(QColor(255, 255, 255, 230)))
        font = QFont("Arial", int(r * 0.9), QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(cx - r, cy - r, r * 2, r * 2),
                         Qt.AlignCenter, "f")

    def _paint_processing(self, painter: QPainter, cx: float, cy: float,
                          radius: float, color: QColor) -> None:
        r = radius * 0.88

        # Dimmer inner circle
        dim_color = QColor(color)
        dim_color.setAlpha(140)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(dim_color))
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # "f" letter
        painter.setPen(QPen(QColor(255, 255, 255, 200)))
        font = QFont("Arial", int(r * 0.9), QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(cx - r, cy - r, r * 2, r * 2),
                         Qt.AlignCenter, "f")

        # Spinning arc
        arc_r = radius * 0.96
        pen = QPen(QColor(255, 255, 255, 180), radius * 0.08)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        painter.drawArc(rect, int(self._spin_angle * 16), int(90 * 16))

    def _paint_success(self, painter: QPainter, cx: float, cy: float,
                       radius: float, color: QColor) -> None:
        r = radius * 0.88
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Checkmark
        pen = QPen(QColor(255, 255, 255, 230), radius * 0.12)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(cx - r * 0.35, cy),
            QPointF(cx - r * 0.05, cy + r * 0.30),
        )
        painter.drawLine(
            QPointF(cx - r * 0.05, cy + r * 0.30),
            QPointF(cx + r * 0.35, cy - r * 0.25),
        )

    def _paint_error(self, painter: QPainter, cx: float, cy: float,
                     radius: float, color: QColor) -> None:
        r = radius * 0.88
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # X mark
        pen = QPen(QColor(255, 255, 255, 230), radius * 0.12)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        d = r * 0.32
        painter.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        painter.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))

    def _get_mic_pixmap(self, size: int) -> QPixmap:
        """Get a cached white microphone icon pixmap at the requested size."""
        if self._mic_pixmap is not None and self._mic_pixmap_size == size:
            return self._mic_pixmap
        try:
            from utils.helpers import platform_icon
            icon = platform_icon(
                "microphone", weight="bold", point_size=size,
                color="#ffffff", windows_name="\uE720",
            )
            pm = icon.pixmap(size, size)
            if not pm.isNull():
                self._mic_pixmap = pm
                self._mic_pixmap_size = size
                return pm
        except Exception:
            pass
        return QPixmap()

    def _paint_paused(self, painter: QPainter, cx: float, cy: float,
                      radius: float, color: QColor) -> None:
        r = radius * 0.88
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Draw mic icon (same as command bar, scaled up)
        icon_size = int(r * 1.2)
        pm = self._get_mic_pixmap(icon_size)
        if not pm.isNull():
            # Account for device pixel ratio for correct centering
            logical_w = pm.width() / pm.devicePixelRatio()
            logical_h = pm.height() / pm.devicePixelRatio()
            target = QRectF(cx - logical_w / 2, cy - logical_h / 2,
                            logical_w, logical_h)
            painter.drawPixmap(target.toAlignedRect(), pm)
        else:
            # Fallback: draw text mic symbol
            painter.setPen(QPen(QColor(255, 255, 255, 230)))
            font = QFont("Arial", int(r * 0.7), QFont.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(cx - r, cy - r, r * 2, r * 2),
                             Qt.AlignCenter, "\U0001f3a4")

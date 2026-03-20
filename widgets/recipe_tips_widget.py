"""Display-only widget for showing community recipe tips in the frosted overlay."""

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _relative_time(iso_str):
    """Convert an ISO 8601 timestamp to a relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 30:
            return f"{days}d ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        return f"{days // 365}y ago"
    except Exception:
        return iso_str or ""


class _TipCard(QFrame):
    """A single tip displayed as a styled card."""

    def __init__(self, text, author, timestamp, parent=None):
        super().__init__(parent)
        self.setObjectName("TipCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setObjectName("TipText")
        layout.addWidget(text_label)

        meta_label = QLabel(f"{author}  \u00b7  {timestamp}")
        meta_label.setObjectName("TipMeta")
        layout.addWidget(meta_label)


class RecipeTipsWidget(QWidget):
    """Scrollable list of community tips for a recipe. Read-only display."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title header
        self._title_label = QLabel("Recipe Tips")
        self._title_label.setObjectName("TipsTitle")
        layout.addWidget(self._title_label)

        # Scrollable tip area
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setObjectName("TipsScrollArea")

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(6, 6, 6, 6)
        self._container_layout.setSpacing(6)
        self._container_layout.addStretch()

        self._scroll_area.setWidget(self._container)
        layout.addWidget(self._scroll_area)

        # Empty state
        self._empty_label = QLabel("No tips yet")
        self._empty_label.setObjectName("TipsEmpty")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._container_layout.insertWidget(0, self._empty_label)

        self.setStyleSheet(self._build_style())

    def _build_style(self):
        return """
            QLabel#TipsTitle {
                background-color: transparent;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 6px 8px;
            }
            QScrollArea#TipsScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollArea#TipsScrollArea > QWidget > QWidget {
                background-color: transparent;
            }
            QFrame#TipCard {
                background-color: rgba(42, 42, 42, 180);
                border: 1px solid #3a3a3a;
                border-radius: 6px;
            }
            QLabel#TipText {
                color: #ffffff;
                font-size: 13px;
                background: transparent;
            }
            QLabel#TipMeta {
                color: #888888;
                font-size: 11px;
                background: transparent;
            }
            QLabel#TipsEmpty {
                color: #888888;
                font-size: 13px;
                padding: 20px;
                background: transparent;
            }
        """

    def load_tips(self, tips):
        """Populate with a list of tip dicts from the API.

        Each dict should have 'text', 'authorDisplayName', and 'createdAt' keys.
        """
        self.clear()
        if not tips:
            self._empty_label.show()
            return
        self._empty_label.hide()
        for tip in tips:
            card = _TipCard(
                text=tip.get("text", ""),
                author=tip.get("authorDisplayName", ""),
                timestamp=_relative_time(tip.get("createdAt", "")),
            )
            # Insert before the stretch
            idx = self._container_layout.count() - 1
            self._container_layout.insertWidget(idx, card)

    def clear(self):
        """Remove all tip cards and show empty state."""
        for i in reversed(range(self._container_layout.count())):
            item = self._container_layout.itemAt(i)
            w = item.widget()
            if w and isinstance(w, _TipCard):
                self._container_layout.removeWidget(w)
                w.deleteLater()
        self._empty_label.show()

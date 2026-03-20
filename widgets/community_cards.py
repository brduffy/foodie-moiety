"""Card widgets for the community homepage sections."""

import platform

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from widgets.marquee_label import MarqueeLabel

_IS_MACOS = platform.system() == "Darwin"

# Badge color mapping
BADGE_COLORS = {
    "recipe": ("#d97706", "Recipe"),
    "book": ("#2563eb", "Book"),
    "article": ("#059669", "Article"),
    "moiety": ("#0891b2", "Moiety"),
    "book_of_moiety": ("#7c3aed", "Book of Moiety"),
}


class CommunityCard(QFrame):
    """A content card for the community homepage (carousel, rows, feed)."""

    card_clicked = Signal(str)  # community_id

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._item = item
        self._community_id = item.get("community_id", "")
        self._has_thumbnail = False
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            CommunityCard {
                background-color: #2a2a2a;
                border: none;
                border-radius: 6px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)

        # Image area
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setScaledContents(True)
        self._image_label.setStyleSheet(
            "background-color: #1a1a1a; border-top-left-radius: 5px; "
            "border-top-right-radius: 5px;"
        )
        layout.addWidget(self._image_label)

        # Type badge (overlaid on image)
        item_type = item.get("type", "recipe")
        is_moiety = item.get("is_moiety", False)
        if is_moiety and item_type == "recipe":
            badge_key = "moiety"
        else:
            badge_key = item_type
        badge_color, badge_text = BADGE_COLORS.get(badge_key, ("#d97706", "Recipe"))

        self._type_badge = QLabel(badge_text, self._image_label)
        self._type_badge.setAlignment(Qt.AlignCenter)
        self._type_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {badge_color};
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
                padding: 2px 8px;
            }}
        """)
        self._type_badge.adjustSize()
        self._type_badge.move(6, 6)

        # Price badge for paid books
        self._price_badge = None
        if item_type == "book" and item.get("price_type") == "paid":
            price_cents = item.get("price_cents", 0)
            if price_cents:
                price_text = f"${price_cents / 100:.2f}"
                self._price_badge = QLabel(price_text, self._image_label)
                self._price_badge.setAlignment(Qt.AlignCenter)
                self._price_badge.setStyleSheet("""
                    QLabel {
                        background-color: #059669;
                        color: white;
                        border: none;
                        border-radius: 4px;
                        font-size: 11px;
                        font-weight: 600;
                        padding: 2px 8px;
                    }
                """)
                self._price_badge.adjustSize()

        # Text area
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(10, 8, 10, 0)
        text_layout.setSpacing(2)

        # Title (marquee scrolls on hover if text overflows)
        self._title_label = MarqueeLabel(item.get("title", ""))
        text_layout.addWidget(self._title_label)

        # Producer line
        producer = item.get("producer", "")
        if producer:
            producer_line = QWidget()
            producer_layout = QHBoxLayout(producer_line)
            producer_layout.setContentsMargins(0, 0, 0, 0)
            producer_layout.setSpacing(6)
            by_label = QLabel(f"by {producer}")
            by_label.setStyleSheet(
                "color: #999999; font-size: 11px; background: transparent;"
            )
            producer_layout.addWidget(by_label)

            # Recipe count pill for books
            recipe_count = item.get("recipe_count")
            if item_type == "book" and recipe_count:
                count_text = f"{recipe_count} recipe{'s' if recipe_count != 1 else ''}"
                count_pill = QLabel(count_text)
                count_pill.setStyleSheet("""
                    QLabel {
                        background-color: #1a3a6a;
                        color: #7aafff;
                        border: none;
                        border-radius: 8px;
                        font-size: 10px;
                        padding: 1px 6px;
                    }
                """)
                producer_layout.addWidget(count_pill)

            producer_layout.addStretch()
            text_layout.addWidget(producer_line)

        layout.addWidget(text_widget)

    def set_card_width(self, width):
        """Set the card width and calculate image height for 16:9."""
        self.setFixedWidth(width)
        img_height = int(width * 9 / 16)
        self._image_label.setFixedSize(width, img_height)
        # Reposition price badge to top-right
        if self._price_badge:
            self._price_badge.move(
                width - self._price_badge.width() - 6, 6
            )

    def set_thumbnail(self, pixmap):
        """Set the card thumbnail from a loaded pixmap."""
        self._image_label.setPixmap(pixmap)
        self._has_thumbnail = True

    @property
    def community_id(self):
        return self._community_id

    @property
    def has_thumbnail(self):
        return self._has_thumbnail

    @property
    def thumbnail_url(self):
        return self._item.get("thumbnail_url", "")

    def enterEvent(self, event):
        self._title_label.start_scroll()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._title_label.stop_scroll()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.card_clicked.emit(self._community_id)
        super().mousePressEvent(event)

    def sizeHint(self):
        w = self.width() or 200
        img_h = int(w * 9 / 16)
        return super().sizeHint().expandedTo(
            self.minimumSizeHint()
        )


class CreatorAvatar(QFrame):
    """Circular creator avatar with name and follower count."""

    creator_clicked = Signal(str)  # profileSlug

    def __init__(self, creator_data, parent=None):
        super().__init__(parent)
        self._data = creator_data
        self._slug = creator_data.get("profileSlug", "")
        self._has_thumbnail = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(90)
        self.setStyleSheet("CreatorAvatar { background: transparent; border: none; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 0, 5, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignHCenter)

        # Circular image
        self._avatar = QLabel()
        self._avatar.setFixedSize(80, 80)
        self._avatar.setAlignment(Qt.AlignCenter)
        self._avatar.setScaledContents(True)
        self._avatar.setStyleSheet("""
            QLabel {
                background-color: #2a2a2a;
                border-radius: 16px;
                border: 2px solid #333333;
            }
        """)
        # Fallback: first letter
        name = creator_data.get("displayName", "?")
        letter = name[0].upper() if name else "?"
        self._avatar.setText(letter)
        self._avatar.setFont(QFont("", 24, QFont.Bold))
        layout.addWidget(self._avatar, alignment=Qt.AlignHCenter)

        # Display name (marquee scrolls on hover if text overflows)
        self._name_label = MarqueeLabel(name)
        name_font = QFont()
        name_font.setPixelSize(11)
        self._name_label.setFont(name_font)
        self._name_label.setColor("#cccccc")
        layout.addWidget(self._name_label)

        # Follower count
        followers = creator_data.get("followerCount", 0)
        f_text = f"{followers} follower{'s' if followers != 1 else ''}"
        follower_label = QLabel(f_text)
        follower_label.setAlignment(Qt.AlignCenter)
        follower_label.setStyleSheet(
            "color: #888888; font-size: 10px; background: transparent;"
        )
        layout.addWidget(follower_label)

    def set_thumbnail(self, pixmap):
        """Set the avatar image from a loaded pixmap."""
        # Create circular pixmap
        size = 76  # inside the 2px border
        scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
        circular = QPixmap(size, size)
        circular.fill(Qt.transparent)
        painter = QPainter(circular)
        painter.setRenderHint(QPainter.Antialiasing)
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, 14, 14)
        painter.setClipPath(path)
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        self._avatar.setPixmap(circular)
        self._avatar.setText("")
        self._has_thumbnail = True

    @property
    def community_id(self):
        """Synthetic ID for thumbnail loading."""
        return f"creator:{self._data.get('userId', '')}"

    @property
    def has_thumbnail(self):
        return self._has_thumbnail

    @property
    def thumbnail_url(self):
        return self._data.get("profileImageUrl", "")

    def enterEvent(self, event):
        self._avatar.setStyleSheet("""
            QLabel {
                background-color: #2a2a2a;
                border-radius: 16px;
                border: 2px solid #3b82f6;
            }
        """)
        self._name_label.start_scroll()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._avatar.setStyleSheet("""
            QLabel {
                background-color: #2a2a2a;
                border-radius: 16px;
                border: 2px solid #333333;
            }
        """)
        self._name_label.stop_scroll()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.creator_clicked.emit(self._slug)
        super().mousePressEvent(event)

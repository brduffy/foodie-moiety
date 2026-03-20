"""Community homepage view — replicates the website home page layout natively."""

import platform

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from utils.helpers import platform_icon
from widgets.community_cards import CommunityCard, CreatorAvatar
from widgets.skeleton_widget import SkeletonWidget
from widgets.tag_filter import TagSidePanel

_IS_MACOS = platform.system() == "Darwin"


def _format_stat(n):
    """Format a number for the stats bar (e.g. 1234 → '1.2k')."""
    if n >= 1000:
        val = n / 1000
        if val == int(val):
            return f"{int(val)}k"
        return f"{val:.1f}k"
    return str(n)


# ---------------------------------------------------------------------------
# Horizontal scroll area with wheel remapping (from tag_filter.py pattern)
# ---------------------------------------------------------------------------

class _HScrollArea(QScrollArea):
    """QScrollArea that forwards vertical wheel events to horizontal scroll."""

    def wheelEvent(self, event):
        h_bar = self.horizontalScrollBar()
        if h_bar and h_bar.isVisible():
            dx = event.angleDelta().x()
            dy = event.angleDelta().y()
            if _IS_MACOS and dx != 0:
                super().wheelEvent(event)
                return
            h_bar.setValue(h_bar.value() - dy)
            event.accept()
        else:
            super().wheelEvent(event)


# ---------------------------------------------------------------------------
# FlowLayout for feed section (adapted from recipe_list.py)
# ---------------------------------------------------------------------------

class _FeedFlowLayout(QLayout):
    """Flow layout that arranges cards in a centered grid."""

    def __init__(self, parent=None, margin=0, h_spacing=16, v_spacing=16):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(width, test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect.width())

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, width, test_only=False):
        from PySide6.QtCore import QRect
        m = self.contentsMargins()
        effective = width - m.left() - m.right()
        if not self._items:
            return m.top() + m.bottom()
        rows = []
        current_row = []
        row_width = 0
        for item in self._items:
            iw = item.sizeHint().width()
            needed = iw if not current_row else iw + self._h_spacing
            if current_row and row_width + needed > effective:
                rows.append(current_row)
                current_row = [item]
                row_width = iw
            else:
                current_row.append(item)
                row_width += needed
        if current_row:
            rows.append(current_row)
        y = m.top()
        for row_items in rows:
            total_w = sum(it.sizeHint().width() for it in row_items)
            total_w += self._h_spacing * (len(row_items) - 1)
            x_off = m.left() + (effective - total_w) // 2
            row_h = max(it.sizeHint().height() for it in row_items)
            x = x_off
            for item in row_items:
                iw = item.sizeHint().width()
                ih = item.sizeHint().height()
                if not test_only:
                    item.setGeometry(QRect(x, y, iw, ih))
                x += iw + self._h_spacing
            y += row_h + self._v_spacing
        return y


# ---------------------------------------------------------------------------
# Stats Section
# ---------------------------------------------------------------------------

class StatsSection(QFrame):
    """Horizontal bar showing community stats counters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            StatsSection {
                background-color: #2a2a2a;
                border: 1px solid #333333;
                border-radius: 8px;
            }
        """)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 16)
        self._layout.setSpacing(0)

        self._counters = {}
        stats = [
            ("totalRecipes", "Recipes"),
            ("totalBooks", "Cookbooks"),
            ("totalCreators", "Creators"),
            ("totalDownloads", "Downloads"),
        ]
        for i, (key, label) in enumerate(stats):
            if i > 0:
                divider = QFrame()
                divider.setFixedWidth(1)
                divider.setStyleSheet("background-color: #444444;")
                self._layout.addWidget(divider)

            counter = QWidget()
            cl = QVBoxLayout(counter)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(2)
            cl.setAlignment(Qt.AlignCenter)

            num = QLabel("—")
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet(
                "color: white; font-size: 20px; font-weight: bold; "
                "background: transparent;"
            )
            cl.addWidget(num)

            cat = QLabel(label)
            cat.setAlignment(Qt.AlignCenter)
            cat.setStyleSheet(
                "color: #999999; font-size: 12px; background: transparent;"
            )
            cl.addWidget(cat)

            self._layout.addWidget(counter, stretch=1)
            self._counters[key] = num

    def set_stats(self, data):
        """Update the counter values from API response."""
        self.hide_skeleton()
        for key, label_widget in self._counters.items():
            val = data.get(key, 0)
            label_widget.setText(_format_stat(val))

    # --- Skeleton ---

    def show_skeleton(self):
        """Reset counters to placeholder dashes."""
        for label_widget in self._counters.values():
            label_widget.setText("—")

    def hide_skeleton(self):
        pass  # set_stats replaces the text


# ---------------------------------------------------------------------------
# Horizontal Scroll Row
# ---------------------------------------------------------------------------

class HomeSectionRow(QWidget):
    """A titled horizontal scroll row of cards with arrow navigation."""

    card_clicked = Signal(str)  # community_id
    creator_clicked = Signal(str)  # profileSlug

    def __init__(self, title, visible_count=3, gap=16, parent=None):
        super().__init__(parent)
        self._visible_count = visible_count
        self._cards = []
        self._scroll_anim = None
        self._gap = gap
        self._skeleton_widgets = []
        self._skeleton_visible = False
        self._skeleton_type = "card"  # "card" or "creator"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Section header: title + scroll arrows
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)
        header_layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            "color: white; font-size: 18px; font-weight: bold; "
            "background: transparent;"
        )
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        arrow_style = """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid #555;
                border-radius: 14px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.25); }
        """
        self._left_btn = QPushButton()
        self._left_btn.setFixedSize(28, 28)
        self._left_btn.setIcon(platform_icon(
            "chevron.left", weight="semibold", point_size=12, color="white"
        ))
        self._left_btn.setIconSize(QSize(14, 14))
        self._left_btn.setCursor(Qt.PointingHandCursor)
        self._left_btn.setStyleSheet(arrow_style)
        self._left_btn.clicked.connect(self._scroll_left)
        header_layout.addWidget(self._left_btn)

        self._right_btn = QPushButton()
        self._right_btn.setFixedSize(28, 28)
        self._right_btn.setIcon(platform_icon(
            "chevron.right", weight="semibold", point_size=12, color="white"
        ))
        self._right_btn.setIconSize(QSize(14, 14))
        self._right_btn.setCursor(Qt.PointingHandCursor)
        self._right_btn.setStyleSheet(arrow_style)
        self._right_btn.clicked.connect(self._scroll_right)
        header_layout.addWidget(self._right_btn)

        layout.addWidget(header)

        # Scroll area wrapped in a padded container
        scroll_wrapper = QWidget()
        scroll_wrapper.setStyleSheet("background: transparent;")
        wrapper_layout = QHBoxLayout(scroll_wrapper)
        wrapper_layout.setContentsMargins(24, 0, 24, 0)
        wrapper_layout.setSpacing(0)

        self._scroll_area = _HScrollArea()
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        self._row_container = QWidget()
        self._row_container.setStyleSheet("background: transparent;")
        self._row_layout = QHBoxLayout(self._row_container)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(self._gap)
        self._scroll_area.setWidget(self._row_container)
        wrapper_layout.addWidget(self._scroll_area)

        layout.addWidget(scroll_wrapper)

    def set_items(self, items):
        """Populate the row with CommunityCard widgets."""
        self.hide_skeleton()
        # Clear existing
        for card in self._cards:
            card.hide()
            card.deleteLater()
        self._cards.clear()

        for item in items:
            card = CommunityCard(item)
            card.card_clicked.connect(self.card_clicked.emit)
            self._row_layout.addWidget(card)
            self._cards.append(card)

        self._resize_cards()

    def set_creator_items(self, creators):
        """Populate the row with CreatorAvatar widgets (for creator spotlight)."""
        self.hide_skeleton()
        for card in self._cards:
            card.hide()
            card.deleteLater()
        self._cards.clear()

        for creator in creators:
            avatar = CreatorAvatar(creator)
            avatar.creator_clicked.connect(self.creator_clicked.emit)
            self._row_layout.addWidget(avatar)
            self._cards.append(avatar)

        self._resize_cards()

    def get_cards(self):
        """Return all card widgets in this row."""
        return self._cards

    def _card_width(self):
        """Calculate card width based on scroll area viewport."""
        vp_w = self._scroll_area.viewport().width()
        if vp_w <= 0:
            return 200
        return max(100, (vp_w - (self._visible_count - 1) * self._gap)
                   // self._visible_count)

    def _resize_cards(self):
        """Recalculate card widths and set container size for horizontal scroll."""
        w = self._card_width()
        img_h = int(w * 9 / 16)
        card_h = img_h + 60  # image + text area
        for card in self._cards:
            if isinstance(card, CommunityCard):
                card.set_card_width(w)
        # Set the row container width so the scroll area extends horizontally
        n = len(self._cards)
        if n > 0 and isinstance(self._cards[0], CommunityCard):
            total_w = n * w + (n - 1) * self._gap
            self._row_container.setFixedSize(total_w, card_h)
            self._scroll_area.setFixedHeight(card_h)
        elif n > 0:
            # CreatorAvatar — fixed width, compute total
            total_w = sum(c.width() for c in self._cards) + (n - 1) * self._gap
            row_h = 130  # avatar + name + followers
            self._row_container.setFixedSize(total_w, row_h)
            self._scroll_area.setFixedHeight(row_h)

    # --- Scroll button handlers ---

    def _scroll_page(self):
        """Return the scroll distance for one page (viewport width)."""
        return self._scroll_area.viewport().width()

    def _scroll_left(self):
        h_bar = self._scroll_area.horizontalScrollBar()
        target = max(0, h_bar.value() - self._scroll_page())
        self._animate_scroll(target)

    def _scroll_right(self):
        h_bar = self._scroll_area.horizontalScrollBar()
        target = min(h_bar.maximum(), h_bar.value() + self._scroll_page())
        self._animate_scroll(target)

    def _animate_scroll(self, target):
        if self._scroll_anim and self._scroll_anim.state() == QPropertyAnimation.Running:
            self._scroll_anim.stop()
        self._scroll_anim = QPropertyAnimation(
            self._scroll_area.horizontalScrollBar(), b"value"
        )
        self._scroll_anim.setDuration(300)
        self._scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    # --- Skeleton placeholders ---

    def show_skeleton(self):
        """Show pulsing placeholder cards matching visible_count."""
        self.hide_skeleton()
        for card in self._cards:
            card.hide()
            card.deleteLater()
        self._cards.clear()
        w = self._card_width()
        img_h = int(w * 9 / 16)
        card_h = img_h + 60
        for _ in range(self._visible_count):
            sk = SkeletonWidget(width=w, height=card_h, radius=6,
                                parent=self._row_container)
            self._row_layout.addWidget(sk)
            self._skeleton_widgets.append(sk)
        total_w = self._visible_count * w + (self._visible_count - 1) * self._gap
        self._row_container.setFixedSize(total_w, card_h)
        self._scroll_area.setFixedHeight(card_h)
        self._skeleton_visible = True
        self._skeleton_type = "card"

    def show_creator_skeleton(self):
        """Show circular avatar placeholders for creator row."""
        self.hide_skeleton()
        for card in self._cards:
            card.hide()
            card.deleteLater()
        self._cards.clear()
        row_h = 130
        for _ in range(self._visible_count):
            col = QWidget(self._row_container)
            col.setFixedWidth(90)
            col.setStyleSheet("background: transparent;")
            cl = QVBoxLayout(col)
            cl.setContentsMargins(5, 0, 5, 0)
            cl.setSpacing(4)
            cl.setAlignment(Qt.AlignHCenter)
            av = SkeletonWidget(width=80, height=80, radius=16, parent=col)
            cl.addWidget(av, alignment=Qt.AlignHCenter)
            self._skeleton_widgets.append(av)
            nm = SkeletonWidget(width=60, height=12, radius=3, parent=col)
            cl.addWidget(nm, alignment=Qt.AlignHCenter)
            self._skeleton_widgets.append(nm)
            fl = SkeletonWidget(width=50, height=10, radius=3, parent=col)
            cl.addWidget(fl, alignment=Qt.AlignHCenter)
            self._skeleton_widgets.append(fl)
            self._row_layout.addWidget(col)
            self._skeleton_widgets.append(col)
        total_w = self._visible_count * 90 + (self._visible_count - 1) * self._gap
        self._row_container.setFixedSize(total_w, row_h)
        self._scroll_area.setFixedHeight(row_h)
        self._skeleton_visible = True
        self._skeleton_type = "creator"

    def hide_skeleton(self):
        if not self._skeleton_visible:
            return
        for sk in self._skeleton_widgets:
            if isinstance(sk, SkeletonWidget):
                sk.stop_pulse()
            sk.hide()
            sk.deleteLater()
        self._skeleton_widgets.clear()
        self._skeleton_visible = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._skeleton_visible:
            # Resize existing skeletons in place (don't recreate)
            if self._skeleton_type == "card" and self._skeleton_widgets:
                w = self._card_width()
                img_h = int(w * 9 / 16)
                card_h = img_h + 60
                for sk in self._skeleton_widgets:
                    sk.setFixedSize(w, card_h)
                n = len(self._skeleton_widgets)
                total_w = n * w + (n - 1) * self._gap
                self._row_container.setFixedSize(total_w, card_h)
                self._scroll_area.setFixedHeight(card_h)
            # Creator skeletons have fixed sizes, no resize needed
        else:
            self._resize_cards()


# ---------------------------------------------------------------------------
# Carousel Section
# ---------------------------------------------------------------------------

class CarouselSection(QWidget):
    """Auto-rotating carousel — one card centered, neighbors peeking, infinite loop."""

    card_clicked = Signal(str)  # community_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._cards = []          # one CommunityCard per item (real + clones)
        self._real_count = 0      # number of real items (before cloning)
        self._current_index = 0   # index into _cards (includes clone offset)
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(7000)
        self._auto_timer.timeout.connect(self._advance)
        self._slide_anim = None
        self._hovered = False
        self._swipe_cooldown = False
        self._gap = 16
        self._card_width_ratio = 0.55  # card takes 55% of viewport width
        self._skeleton_widget = None
        self._skeleton_visible = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Clip viewport
        self._viewport = QWidget()
        self._viewport.setStyleSheet("background: transparent;")
        layout.addWidget(self._viewport)

        # Slide track (absolutely positioned inside viewport)
        self._track = QWidget(self._viewport)
        self._track.setStyleSheet("background: transparent;")

        # Navigation arrows (parented to viewport so they float on top)
        arrow_style = """
            QPushButton {
                background-color: rgba(0, 0, 0, 0.6);
                border: 1px solid white;
                border-radius: 18px;
            }
            QPushButton:hover { background-color: rgba(0, 0, 0, 0.9); }
        """
        left_icon = platform_icon(
            "chevron.left", weight="semibold", point_size=14, color="white"
        )
        self._left_arrow = QPushButton(self._viewport)
        self._left_arrow.setFixedSize(36, 36)
        self._left_arrow.setIcon(left_icon)
        self._left_arrow.setIconSize(QSize(16, 16))
        self._left_arrow.setCursor(Qt.PointingHandCursor)
        self._left_arrow.setStyleSheet(arrow_style)
        self._left_arrow.clicked.connect(self._go_prev)

        right_icon = platform_icon(
            "chevron.right", weight="semibold", point_size=14, color="white"
        )
        self._right_arrow = QPushButton(self._viewport)
        self._right_arrow.setFixedSize(36, 36)
        self._right_arrow.setIcon(right_icon)
        self._right_arrow.setIconSize(QSize(16, 16))
        self._right_arrow.setCursor(Qt.PointingHandCursor)
        self._right_arrow.setStyleSheet(arrow_style)
        self._right_arrow.clicked.connect(self._go_next)

        # Arrows hidden until hover
        self._left_arrow.hide()
        self._right_arrow.hide()

        # Dot indicators
        self._dot_container = QWidget()
        self._dot_layout = QHBoxLayout(self._dot_container)
        self._dot_layout.setContentsMargins(0, 0, 0, 0)
        self._dot_layout.setSpacing(6)
        self._dot_layout.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._dot_container)
        self._dots = []

    def set_items(self, items):
        """Populate carousel. Clones edges for infinite loop effect."""
        self.hide_skeleton()
        self._items = items
        self._real_count = len(items)
        # Clear old
        for c in self._cards:
            c.hide()
            c.deleteLater()
        self._cards.clear()
        for d in self._dots:
            d.hide()
            d.deleteLater()
        self._dots.clear()

        if not items:
            self._auto_timer.stop()
            self._viewport.setFixedHeight(0)
            return

        # Build card list: [clone_last] + real items + [clone_first]
        # This lets us animate past the edge, then snap back invisibly
        all_items = [items[-1]] + list(items) + [items[0]]
        for item in all_items:
            card = CommunityCard(item)
            card.card_clicked.connect(self.card_clicked.emit)
            card.setParent(self._track)
            self._cards.append(card)

        # Start on the first real item (index 1, since index 0 is the clone)
        self._current_index = 1

        # Create dots (one per real item)
        for i in range(self._real_count):
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setCursor(Qt.PointingHandCursor)
            dot.setStyleSheet(
                "background-color: white; border-radius: 4px;"
                if i == 0 else
                "background-color: #555555; border-radius: 4px;"
            )
            self._dot_layout.addWidget(dot)
            self._dots.append(dot)

        self._layout_cards()
        if not self._hovered:
            self._auto_timer.start()

    def get_cards(self):
        return self._cards

    def _layout_cards(self):
        """Position cards on the track. One centered, neighbors peek from edges."""
        vp_w = self._viewport.width()
        if vp_w <= 0:
            return
        card_w = int(vp_w * self._card_width_ratio)
        img_h = int(card_w * 9 / 16)
        card_h = img_h + 60
        self._viewport.setFixedHeight(card_h)
        # Enable clipping so peeking cards don't overflow
        self._viewport.setMinimumHeight(card_h)

        # Each card is spaced by card_w + gap; the track scrolls so the
        # current card is centered in the viewport
        stride = card_w + self._gap
        for i, card in enumerate(self._cards):
            card.set_card_width(card_w)
            card.setFixedHeight(card_h)
            card.move(i * stride, 0)
            card.show()

        total_w = len(self._cards) * stride - self._gap
        self._track.setFixedSize(total_w, card_h)
        self._snap_to_index(self._current_index, animate=False)
        self._position_arrows()

    def _center_x_for(self, index):
        """Track x-position that centers the card at `index` in the viewport."""
        vp_w = self._viewport.width()
        card_w = int(vp_w * self._card_width_ratio)
        stride = card_w + self._gap
        # Card left edge is at index * stride; center it in viewport
        return -(index * stride - (vp_w - card_w) // 2)

    def _snap_to_index(self, index, animate=True):
        """Move track so card at index is centered."""
        from PySide6.QtCore import QPoint
        target_x = self._center_x_for(index)

        if animate and self._track.x() != target_x:
            self._slide_anim = QPropertyAnimation(self._track, b"pos")
            self._slide_anim.setDuration(600)
            self._slide_anim.setStartValue(self._track.pos())
            self._slide_anim.setEndValue(QPoint(target_x, 0))
            self._slide_anim.setEasingCurve(QEasingCurve.InOutCubic)
            self._slide_anim.finished.connect(self._check_wraparound)
            self._slide_anim.start()
        else:
            self._track.move(QPoint(target_x, 0))

        # Update dots (map card index back to real item index)
        real_idx = (index - 1) % self._real_count if self._real_count else 0
        for i, dot in enumerate(self._dots):
            dot.setStyleSheet(
                "background-color: white; border-radius: 4px;"
                if i == real_idx else
                "background-color: #555555; border-radius: 4px;"
            )

    def _check_wraparound(self):
        """After animation, silently jump from clone back to the real card."""
        from PySide6.QtCore import QPoint
        n = self._real_count
        if n == 0:
            return
        if self._current_index == 0:
            # We animated to clone of last item; jump to real last item
            self._current_index = n
            self._track.move(QPoint(self._center_x_for(n), 0))
        elif self._current_index == n + 1:
            # We animated to clone of first item; jump to real first item
            self._current_index = 1
            self._track.move(QPoint(self._center_x_for(1), 0))

    def _position_arrows(self):
        card_w = int(self._viewport.width() * self._card_width_ratio)
        img_h = int(card_w * 9 / 16)  # 16:9 image area
        self._left_arrow.move(8, img_h - 36 - 10)
        self._right_arrow.move(self._viewport.width() - 44, img_h - 36 - 10)
        self._left_arrow.setVisible(self._hovered)
        self._right_arrow.setVisible(self._hovered)
        self._left_arrow.raise_()
        self._right_arrow.raise_()

    def _advance(self):
        if not self._cards:
            return
        # Force wraparound before advancing so rapid clicks don't overshoot
        self._force_wraparound()
        self._current_index += 1
        self._snap_to_index(self._current_index)

    def _go_prev(self):
        if not self._cards:
            return
        self._force_wraparound()
        self._current_index -= 1
        self._snap_to_index(self._current_index)

    def _force_wraparound(self):
        """Immediately snap from clone to real card if needed (for rapid clicks)."""
        from PySide6.QtCore import QPoint
        n = self._real_count
        if n == 0:
            return
        if self._slide_anim:
            self._slide_anim.stop()
        if self._current_index == 0:
            self._current_index = n
            self._track.move(QPoint(self._center_x_for(n), 0))
        elif self._current_index == n + 1:
            self._current_index = 1
            self._track.move(QPoint(self._center_x_for(1), 0))

    def _go_next(self):
        self._advance()

    def _clear_swipe_cooldown(self):
        self._swipe_cooldown = False

    def enterEvent(self, event):
        self._hovered = True
        self._auto_timer.stop()
        self._left_arrow.show()
        self._right_arrow.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._left_arrow.hide()
        self._right_arrow.hide()
        if self._items:
            self._auto_timer.start()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        """Handle trackpad swipe gestures (two-finger horizontal scroll)."""
        if self._swipe_cooldown:
            # Still let vertical scrolls through during horizontal cooldown
            dy = event.pixelDelta().y() or event.angleDelta().y()
            dx = event.pixelDelta().x() or event.angleDelta().x()
            if abs(dy) > abs(dx):
                super().wheelEvent(event)
                return
            event.accept()
            return
        dx = event.pixelDelta().x()
        dy = event.pixelDelta().y()
        if dx == 0:
            dx = event.angleDelta().x()
            dy = event.angleDelta().y()
        # Let vertical scrolls and noise/inertia propagate to parent
        if abs(dx) < 15 or abs(dy) > abs(dx):
            super().wheelEvent(event)
            return
        # Fire immediately on first qualifying event, then lock out
        self._swipe_cooldown = True
        QTimer.singleShot(800, self._clear_swipe_cooldown)
        if dx < 0:
            self._go_next()
        else:
            self._go_prev()
        self._auto_timer.stop()
        if self._items and not self._hovered:
            self._auto_timer.start()
        event.accept()

    # --- Skeleton ---

    def show_skeleton(self):
        self.hide_skeleton()
        for c in self._cards:
            c.hide()
            c.deleteLater()
        self._cards.clear()
        self._auto_timer.stop()
        vp_w = self._viewport.width()
        if vp_w <= 0:
            vp_w = 800
        card_w = int(vp_w * self._card_width_ratio)
        img_h = int(card_w * 9 / 16)
        card_h = img_h + 60
        self._viewport.setFixedHeight(card_h)
        self._viewport.setMinimumHeight(card_h)
        sk = SkeletonWidget(width=card_w, height=card_h, radius=6,
                            parent=self._viewport)
        sk.move((vp_w - card_w) // 2, 0)
        sk.show()
        self._skeleton_widget = sk
        self._skeleton_visible = True
        self._left_arrow.hide()
        self._right_arrow.hide()
        for d in self._dots:
            d.hide()

    def hide_skeleton(self):
        if not self._skeleton_visible:
            return
        if self._skeleton_widget:
            self._skeleton_widget.stop_pulse()
            self._skeleton_widget.hide()
            self._skeleton_widget.deleteLater()
            self._skeleton_widget = None
        self._skeleton_visible = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._skeleton_visible:
            # Reposition existing skeleton without recreating
            vp_w = self._viewport.width()
            if vp_w > 0 and self._skeleton_widget:
                card_w = int(vp_w * self._card_width_ratio)
                img_h = int(card_w * 9 / 16)
                card_h = img_h + 60
                self._skeleton_widget.setFixedSize(card_w, card_h)
                self._skeleton_widget.move((vp_w - card_w) // 2, 0)
        elif self._cards:
            self._layout_cards()


# ---------------------------------------------------------------------------
# Feed Section
# ---------------------------------------------------------------------------

class FeedSection(QWidget):
    """Paginated 3-column grid of content cards."""

    card_clicked = Signal(str)      # community_id
    load_more_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards = []
        self._card_width = 200
        self._skeleton_widgets = []
        self._skeleton_visible = False
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._recalc_cards)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Section title
        self._title = QLabel("Latest")
        self._title.setStyleSheet(
            "color: white; font-size: 18px; font-weight: bold; "
            "background: transparent; padding-left: 24px;"
        )
        layout.addWidget(self._title)

        # Card grid container
        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background: transparent;")
        self._grid_layout = _FeedFlowLayout(
            self._grid_widget, margin=24, h_spacing=16, v_spacing=16
        )
        layout.addWidget(self._grid_widget)

        # Load more button
        self._load_more = QPushButton("Load More")
        self._load_more.setCursor(Qt.PointingHandCursor)
        self._load_more.setFixedHeight(40)
        self._load_more.setStyleSheet("""
            QPushButton {
                background-color: #1a3a6a;
                color: white;
                border: 1px solid #2a5a9a;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
                padding: 8px 24px;
            }
            QPushButton:hover { background-color: #2a4a7a; }
        """)
        self._load_more.clicked.connect(self.load_more_clicked.emit)
        self._load_more.hide()
        layout.addWidget(self._load_more, alignment=Qt.AlignCenter)

        # Empty state
        self._empty_label = QLabel("No content yet. Check back soon!")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #999999; font-size: 14px; font-style: italic; "
            "background: transparent; padding: 40px;"
        )
        self._empty_label.hide()
        layout.addWidget(self._empty_label)

    def set_authenticated(self, is_auth):
        self._title.setText("Your Feed" if is_auth else "Latest")

    def set_items(self, items, has_more=False):
        """Set feed items (replaces existing)."""
        self.hide_skeleton()
        self._clear_cards()
        self._append_items(items)
        self._load_more.setVisible(has_more)
        self._empty_label.setVisible(not items)

    def append_items(self, items, has_more=False):
        """Append more items (pagination)."""
        self._append_items(items)
        self._load_more.setVisible(has_more)

    def _append_items(self, items):
        for item in items:
            card = CommunityCard(item)
            card.card_clicked.connect(self.card_clicked.emit)
            card.set_card_width(self._card_width)
            self._grid_layout.addWidget(card)
            self._cards.append(card)
        self._empty_label.hide()

    def _clear_cards(self):
        for card in self._cards:
            self._grid_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards.clear()

    def get_cards(self):
        return self._cards

    def _recalc_cards(self):
        """Recalculate card widths for 3-column layout."""
        w = self.width() - 48  # margins
        gap = 16
        cols = 3
        self._card_width = max(100, (w - (cols - 1) * gap) // cols)
        for card in self._cards:
            card.set_card_width(self._card_width)
        self._grid_widget.updateGeometry()

    # --- Skeleton ---

    def show_skeleton(self, count=6):
        """Show pulsing placeholder cards in the grid."""
        self.hide_skeleton()
        self._clear_cards()
        w = self.width() - 48
        cols = 3
        card_w = max(100, (w - (cols - 1) * 16) // cols)
        img_h = int(card_w * 9 / 16)
        card_h = img_h + 60
        for _ in range(count):
            sk = SkeletonWidget(width=card_w, height=card_h, radius=6,
                                parent=self._grid_widget)
            self._grid_layout.addWidget(sk)
            self._skeleton_widgets.append(sk)
        self._grid_widget.updateGeometry()
        self._empty_label.hide()
        self._load_more.hide()
        self._skeleton_visible = True

    def hide_skeleton(self):
        if not self._skeleton_visible:
            return
        for sk in self._skeleton_widgets:
            sk.stop_pulse()
            self._grid_layout.removeWidget(sk)
            sk.hide()
            sk.deleteLater()
        self._skeleton_widgets.clear()
        self._skeleton_visible = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._skeleton_visible:
            # Resize existing skeletons in place (don't recreate)
            w = self.width() - 48
            cols = 3
            card_w = max(100, (w - (cols - 1) * 16) // cols)
            img_h = int(card_w * 9 / 16)
            card_h = img_h + 60
            for sk in self._skeleton_widgets:
                sk.setFixedSize(card_w, card_h)
            self._grid_widget.updateGeometry()
        else:
            self._resize_timer.start()


# ---------------------------------------------------------------------------
# Main Community Home View
# ---------------------------------------------------------------------------

class CommunityHomeView(QWidget):
    """The community homepage — carousel, stats, rows, feed."""

    card_clicked = Signal(str)      # community_id from any section
    creator_clicked = Signal(str)   # profileSlug from creator avatar
    load_more_clicked = Signal()    # feed pagination
    search_load_more = Signal()     # search results pagination
    back_to_home = Signal()         # back from search results
    tags_changed = Signal()         # tag panel selection changed
    retry_clicked = Signal()        # retry failed section fetches

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #1a1a1a;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Command bar container (for reparenting)
        self._cb_container = QWidget()
        self._cb_container.setStyleSheet("background-color: #000000;")
        self._cb_layout = QHBoxLayout(self._cb_container)
        self._cb_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._cb_container)

        # Mode banner
        self._banner = QLabel("COMMUNITY")
        self._banner.setAlignment(Qt.AlignCenter)
        self._banner.setFixedHeight(28)
        self._banner.setStyleSheet(
            "background-color: #1a3a6a; color: white; font-size: 11px; "
            "font-weight: 600; letter-spacing: 2px;"
        )
        outer.addWidget(self._banner)

        # Retry banner (hidden by default)
        self._retry_banner = QWidget()
        self._retry_banner.setStyleSheet(
            "background-color: #2a1a1a; border-bottom: 1px solid #553333;"
        )
        rb_layout = QHBoxLayout(self._retry_banner)
        rb_layout.setContentsMargins(24, 10, 24, 10)
        rb_layout.setSpacing(12)
        self._retry_label = QLabel(
            "Unable to load — please check your internet connection"
        )
        self._retry_label.setStyleSheet(
            "color: #ddaaaa; font-size: 13px; border: none;"
        )
        rb_layout.addWidget(self._retry_label, 1)
        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setCursor(Qt.PointingHandCursor)
        self._retry_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: white; "
            "font-size: 13px; font-weight: bold; border: none; "
            "border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #1a8ae6; }"
        )
        self._retry_btn.clicked.connect(self._on_retry_clicked)
        rb_layout.addWidget(self._retry_btn)
        self._retry_banner.hide()
        outer.addWidget(self._retry_banner)

        # Main scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                width: 6px; background-color: transparent; border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #555555; border-radius: 3px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background-color: #777777; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 16, 0, 24)
        self._content_layout.setSpacing(24)

        # --- Sections ---

        # 1. Carousel
        self.carousel = CarouselSection()
        self.carousel.card_clicked.connect(self.card_clicked.emit)
        self._content_layout.addWidget(self.carousel)

        # 2. Book Row
        self.book_row = HomeSectionRow("New Cookbooks", visible_count=3)
        self.book_row.card_clicked.connect(self.card_clicked.emit)
        self._content_layout.addWidget(self.book_row)

        # 3. Creator Spotlight
        self.creator_row = HomeSectionRow("Featured Creators", visible_count=5, gap=24)
        self.creator_row.creator_clicked.connect(self.creator_clicked.emit)
        self._content_layout.addWidget(self.creator_row)

        # 4. Articles Row
        self.article_row = HomeSectionRow("Latest Articles", visible_count=4)
        self.article_row.card_clicked.connect(self.card_clicked.emit)
        self._content_layout.addWidget(self.article_row)

        # 5. Moieties Row
        self.moiety_row = HomeSectionRow("Community Moieties", visible_count=4)
        self.moiety_row.card_clicked.connect(self.card_clicked.emit)
        self._content_layout.addWidget(self.moiety_row)

        # 6. Stats
        self.stats_section = StatsSection()
        self._stats_wrapper = QWidget()
        sw_layout = QHBoxLayout(self._stats_wrapper)
        sw_layout.setContentsMargins(24, 0, 24, 0)
        sw_layout.addWidget(self.stats_section)
        self._content_layout.addWidget(self._stats_wrapper)

        # 7. Feed
        self.feed_section = FeedSection()
        self.feed_section.card_clicked.connect(self.card_clicked.emit)
        self.feed_section.load_more_clicked.connect(self.load_more_clicked.emit)
        self._content_layout.addWidget(self.feed_section)

        # --- Search results UI (hidden by default) ---
        self._search_mode = False

        # Search header: back button + result count
        self._search_header = QWidget()
        self._search_header.setStyleSheet("background: transparent;")
        sh_layout = QHBoxLayout(self._search_header)
        sh_layout.setContentsMargins(24, 0, 24, 0)
        sh_layout.setSpacing(8)
        back_icon = platform_icon(
            "chevron.left", weight="regular", point_size=14, color="white"
        )
        back_btn = QPushButton()
        back_btn.setIcon(back_icon)
        back_btn.setIconSize(QSize(16, 16))
        back_btn.setFixedSize(28, 28)
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            "QPushButton:hover { background-color: #333333; border-radius: 14px; }"
        )
        back_btn.clicked.connect(self.back_to_home.emit)
        sh_layout.addWidget(back_btn)
        self._search_label = QLabel("Search Results")
        self._search_label.setStyleSheet(
            "color: white; font-size: 18px; font-weight: bold; background: transparent;"
        )
        sh_layout.addWidget(self._search_label)
        sh_layout.addStretch()
        self._search_header.hide()
        self._content_layout.addWidget(self._search_header)

        # Search results grid (reuses FeedSection pattern)
        self._search_section = FeedSection()
        self._search_section.card_clicked.connect(self.card_clicked.emit)
        self._search_section.load_more_clicked.connect(self.search_load_more.emit)
        self._search_section._title.hide()  # we use _search_header instead
        self._search_section.hide()
        self._content_layout.addWidget(self._search_section)

        # Track homepage sections for hide/show toggling
        self._home_sections = [
            self.carousel, self.book_row, self.creator_row,
            self.article_row, self.moiety_row, self._stats_wrapper,
            self.feed_section,
        ]

        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)

        # Tag panel overlay (positioned at bottom, not in scroll layout)
        self.tag_side_panel = TagSidePanel(self)
        self.tag_side_panel.selectionChanged.connect(self._on_tag_selection_changed)
        self.tag_side_panel.clearAll.connect(self._on_tag_clear_all)
        self.tag_side_panel.hide()

    def set_command_bar(self, bar):
        """Reparent the shared command bar into this view's container."""
        self._cb_layout.addWidget(bar)

    @property
    def in_search_mode(self):
        return self._search_mode

    def enter_search_mode(self, label_text="Searching..."):
        """Hide homepage sections, show search results UI."""
        self._search_mode = True
        for section in self._home_sections:
            section.hide()
        self._search_label.setText(label_text)
        self._search_header.show()
        self._search_section.show()
        self.scroll_area.verticalScrollBar().setValue(0)

    def exit_search_mode(self):
        """Hide search UI, restore homepage sections."""
        self._search_mode = False
        self._search_header.hide()
        self._search_section.hide()
        self._search_section.set_items([], has_more=False)
        for section in self._home_sections:
            section.show()

    def set_search_results(self, items, has_more=False):
        """Populate search results (fresh)."""
        self._search_section.set_items(items, has_more=has_more)
        self._search_section._empty_label.setText(
            "No results found. Try a different search."
        )

    def append_search_results(self, items, has_more=False):
        """Append paginated search results."""
        self._search_section.append_items(items, has_more=has_more)

    def set_search_label(self, text):
        self._search_label.setText(text)

    # --- Tag panel ---

    def toggle_tag_panel(self):
        if self.tag_side_panel.isVisible():
            self.tag_side_panel.hide()
        else:
            self._position_tag_panel()
            self.tag_side_panel.show()
            self.tag_side_panel.raise_()

    def _position_tag_panel(self):
        h = self.tag_side_panel.panel_height()
        y = self.height() - h
        self.tag_side_panel.setGeometry(0, y, self.width(), h)
        self.tag_side_panel.raise_()

    def _on_tag_selection_changed(self):
        self._position_tag_panel()
        self.tags_changed.emit()

    def _on_tag_clear_all(self):
        self.tag_side_panel.set_selections([], [])
        self.tags_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.tag_side_panel.isVisible():
            self._position_tag_panel()

    # --- Card enumeration ---

    def all_cards(self):
        """Yield all card/avatar widgets across all sections for thumbnail loading."""
        if self._search_mode:
            yield from self._search_section.get_cards()
        yield from self.carousel.get_cards()
        yield from self.book_row.get_cards()
        yield from self.creator_row.get_cards()
        yield from self.article_row.get_cards()
        yield from self.moiety_row.get_cards()
        yield from self.feed_section.get_cards()

    def clear_all(self):
        """Reset all sections (clears data, no skeletons)."""
        if self._search_mode:
            self.exit_search_mode()
        self.carousel.set_items([])
        self.stats_section.show_skeleton()
        self.book_row.set_items([])
        self.creator_row.set_items([])
        self.article_row.set_items([])
        self.moiety_row.set_items([])
        self.feed_section.set_items([])

    def show_all_skeletons(self):
        """Show skeleton loading placeholders in all sections.

        Call AFTER the view is visible so widgets have valid geometry.
        Only shows skeleton for sections that haven't received data yet.
        """
        if not self.carousel.get_cards():
            self.carousel.show_skeleton()
        self.stats_section.show_skeleton()
        if not self.book_row.get_cards():
            self.book_row.show_skeleton()
        if not self.creator_row.get_cards():
            self.creator_row.show_creator_skeleton()
        if not self.article_row.get_cards():
            self.article_row.show_skeleton()
        if not self.moiety_row.get_cards():
            self.moiety_row.show_skeleton()
        if not self.feed_section.get_cards():
            self.feed_section.show_skeleton()

    def show_retry_banner(self):
        """Show the retry banner at the top of the view."""
        self._retry_banner.show()

    def hide_retry_banner(self):
        """Hide the retry banner."""
        self._retry_banner.hide()

    def _on_retry_clicked(self):
        self._retry_banner.hide()
        self.retry_clicked.emit()

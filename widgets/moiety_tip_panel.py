"""Right-half overlay panel explaining the moiety concept and Save as Moiety."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _moiety_tips_html(fs):
    """Return moiety tips HTML with the given body font size."""
    return f"""\
<p style="font-size: {fs}px; color: #cccccc; line-height: 1.6;">
A <b style="color: #ffffff;">moiety</b> (pronounced "moy-uh-tee") is a
reusable portion of a recipe &mdash; pie crusts, sauces, marinades, stocks,
side dishes, or any component you find yourself making again and again.
The word comes from chemistry, where it means "a part of" something larger.
</p>

<p style="font-size: {fs}px; color: #cccccc; line-height: 1.6;">
When you <b style="color: #ffffff;">Save as Moiety</b>, your recipe is
marked as a reusable building block. It stays in your recipe list like any
other recipe, but it also appears in the <b style="color: #ffffff;">Moiety
Panel</b> where you can browse and insert its steps into other recipes
with a single click.
</p>

<p style="font-size: {fs}px; color: #cccccc; line-height: 1.6;">
Think of moieties as your personal recipe toolkit. A homemade pizza dough,
a b&eacute;chamel sauce, a spice rub &mdash; save them once as moieties,
then pull them into any recipe that needs them without retyping ingredients
and directions.
</p>

<p style="font-size: {fs}px; color: #5EAAFF; line-height: 1.6;">
You can always change a moiety back to a regular recipe by choosing
"Save as Recipe" next time you save.
</p>
"""


class MoietyTipPanel(QWidget):
    """Overlay panel explaining the moiety concept.

    Positioned on the right half of the recipe detail view.
    Dismissed via the 'Got it' button or the close button.
    """

    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MoietyTipPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._font_size = 15

        self.setStyleSheet("""
            QWidget#MoietyTipPanel {
                background-color: rgba(20, 20, 20, 230);
                border-left: 1px solid rgba(255, 255, 255, 40);
            }
            QPushButton#TipCloseBtn {
                background: transparent;
                color: #888888;
                border: none;
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
                min-width: 32px; max-width: 32px;
                min-height: 32px; max-height: 32px;
            }
            QPushButton#TipCloseBtn:hover {
                color: white;
            }
            QLabel#TipTitle {
                color: #ffffff;
                font-weight: 600;
                background: transparent;
            }
            QLabel#TipBody {
                color: #dddddd;
                background: transparent;
            }
            QScrollArea#TipScroll {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 6px;
                background-color: transparent;
                border: none;
            }
            QScrollBar::handle:vertical {
                background-color: white;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QPushButton#GotItBtn {
                background-color: #3a6a3a;
                color: white;
                border: 1px solid #5a8a5a;
                border-radius: 6px;
                font-weight: 600;
                padding: 10px 32px;
            }
            QPushButton#GotItBtn:hover {
                background-color: #4a8a4a;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        # Header: title + close button
        header = QHBoxLayout()
        header.setSpacing(0)
        self._title = QLabel("What's a Moiety?")
        self._title.setObjectName("TipTitle")
        header.addWidget(self._title)
        header.addStretch()

        close_btn = QPushButton("\u2715")
        close_btn.setObjectName("TipCloseBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self._dismiss)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Subtitle
        self._subtitle = QLabel("Reusable recipe building blocks for your kitchen")
        self._subtitle.setWordWrap(True)
        layout.addWidget(self._subtitle)

        # Scrollable body
        self._body = QLabel()
        self._body.setObjectName("TipBody")
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._body.setText(_moiety_tips_html(self._font_size))

        scroll = QScrollArea()
        scroll.setObjectName("TipScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self._body)
        layout.addWidget(scroll, stretch=1)

        # "Got it" button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._got_it = QPushButton("Got it")
        self._got_it.setObjectName("GotItBtn")
        self._got_it.setCursor(Qt.PointingHandCursor)
        self._got_it.clicked.connect(self._dismiss)
        btn_row.addWidget(self._got_it)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._apply_font_sizes()
        self.hide()

    def adjust_font_size(self, delta):
        """Adjust font size by delta pixels (clamped to 14–24)."""
        self._font_size = max(14, min(24, self._font_size + delta))
        self._apply_font_sizes()

    def _apply_font_sizes(self):
        fs = self._font_size
        self._title.setStyleSheet(
            f"color: #ffffff; font-size: {fs + 3}px; font-weight: 600; background: transparent;"
        )
        self._subtitle.setStyleSheet(
            f"color: #5EAAFF; font-size: {fs - 1}px; font-style: italic; "
            f"background: transparent; padding-bottom: 8px;"
        )
        self._body.setText(_moiety_tips_html(fs))
        self._got_it.setStyleSheet(
            f"QPushButton#GotItBtn {{"
            f"  background-color: #3a6a3a; color: white; border: 1px solid #5a8a5a;"
            f"  border-radius: 6px; font-size: {fs}px; font-weight: 600;"
            f"  padding: 10px 32px;"
            f"}}"
            f"QPushButton#GotItBtn:hover {{ background-color: #4a8a4a; }}"
        )

    def _dismiss(self):
        self.hide()
        self.dismissed.emit()

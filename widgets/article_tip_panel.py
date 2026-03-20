"""Right-half overlay panel with article creation instructions."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _article_tips_html(fs):
    """Return article tips HTML with the given body font size."""
    return f"""\
<p style="font-size: {fs}px; color: #cccccc; line-height: 1.6;">
Article creation takes advantage of the same recipe editor you use for
creating foodie moiety recipes. Instead of creating steps in a recipe,
you create paragraphs in an article.
</p>

<p style="font-size: {fs}px; color: #cccccc; line-height: 1.6;">
You can choose to include images (free account) or images and/or video
(creator account) with each paragraph. The first paragraph in the article
will have its image and/or video placed above it in the beginning of the
article. Subsequent paragraphs will show image/video below the paragraph.
</p>

<p style="font-size: {fs}px; color: #cccccc; line-height: 1.6;">
You can associate tags with your article for filtering on the foodie
moiety website. The paragraph editor supports creating links in your
article as long as they stay inside the <b style="color: #ffffff;">foodiemoiety.com</b>
domain so you can link to your profile page or a specific recipe or book
inside the site. External links and affiliate links are forbidden in
foodie moiety articles.
</p>

<p style="font-size: {fs}px; color: #cccccc; line-height: 1.6;">
Articles are a great way to engage with the foodie moiety community about
foodie related subjects that are not recipes like restaurant reviews,
equipment and product recommendations &hellip; whatever culinary adventure
you want to discuss with the community. Articles count toward your upload
limit and image/video restrictions and file sizes are the same for
articles as recipes depending on your account type. Articles also go
through the same moderation process as recipes.
</p>

<p style="font-size: {fs}px; color: #5EAAFF; line-height: 1.6;">
Write an article and upload it to foodiemoiety.com to share with your followers!
</p>
"""


class ArticleTipPanel(QWidget):
    """Overlay panel showing article creation instructions.

    Positioned on the right half of the recipe detail view.
    Dismissed via the 'Got it' button or the close button.
    """

    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ArticleTipPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._font_size = 15

        self.setStyleSheet("""
            QWidget#ArticleTipPanel {
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
        self._title = QLabel("Creating Articles")
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
        self._subtitle = QLabel(
            "Creating articles for sharing with the community on foodiemoiety.com"
        )
        self._subtitle.setWordWrap(True)
        layout.addWidget(self._subtitle)

        # Scrollable body
        self._body = QLabel()
        self._body.setObjectName("TipBody")
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._body.setText(_article_tips_html(self._font_size))

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
        self._body.setText(_article_tips_html(fs))
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

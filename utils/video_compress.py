"""Video resolution detection using Qt multimedia.

Detects whether an imported video exceeds 1080p so the user can be
warned about file size implications before importing.
"""

import logging

from PySide6.QtCore import QEventLoop, QSize, QTimer, QUrl
from PySide6.QtMultimedia import QMediaMetaData, QMediaPlayer

log = logging.getLogger(__name__)


def get_video_resolution(file_path: str) -> tuple[int, int] | None:
    """Return (width, height) of a video file, or None if detection fails.

    Uses QMediaPlayer metadata (async internally, but we block with a
    local event loop for a synchronous API). Times out after 3 seconds.
    """
    player = QMediaPlayer()
    player.setSource(QUrl.fromLocalFile(file_path))

    result = [None]
    loop = QEventLoop()

    def _on_meta_changed():
        res = player.metaData().value(QMediaMetaData.Resolution)
        if isinstance(res, QSize) and res.width() > 0 and res.height() > 0:
            result[0] = (res.width(), res.height())
            loop.quit()

    def _on_error(error):
        loop.quit()

    player.metaDataChanged.connect(_on_meta_changed)
    player.errorOccurred.connect(_on_error)
    QTimer.singleShot(3000, loop.quit)  # 3s timeout

    loop.exec()

    player.stop()
    player.setSource(QUrl())
    return result[0]


def is_above_1080p(file_path: str) -> tuple[bool, tuple[int, int] | None]:
    """Check if a video exceeds 1080p resolution.

    Returns (is_above, resolution). If detection fails,
    returns (False, None) so the import proceeds normally.
    """
    res = get_video_resolution(file_path)
    if res is None:
        return (False, None)
    width, height = res
    return (width > 1920 or height > 1080, res)

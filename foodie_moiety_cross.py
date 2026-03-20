import platform
import sys

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

# --- HELPERS ---


class ClickableSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            new_value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), event.position().x(), self.width()
            )
            self.setValue(new_value)
            self.sliderMoved.emit(new_value)
        super().mousePressEvent(event)


class DoubleClickVideoWidget(QVideoWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.main_window.toggle_full_screen()
        super().mouseDoubleClickEvent(event)


def create_white_icon(icon_type):
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("white"))
    painter.setPen(Qt.NoPen)
    painter.scale(128 / 24, 128 / 24)
    path = QPainterPath()
    if icon_type == "play":
        path.moveTo(8, 5)
        path.lineTo(8, 19)
        path.lineTo(19, 12)
        path.closeSubpath()
    elif icon_type == "pause":
        path.addRect(6, 5, 4, 14)
        path.addRect(14, 5, 4, 14)
    elif icon_type == "skip_f":
        path.moveTo(7, 5)
        path.lineTo(7, 19)
        path.lineTo(15, 12)
        path.closeSubpath()
        path.addRect(16, 5, 2, 14)
    elif icon_type == "skip_b":
        path.moveTo(17, 5)
        path.lineTo(17, 19)
        path.lineTo(9, 12)
        path.closeSubpath()
        path.addRect(6, 5, 2, 14)
    elif icon_type == "volume":
        path.moveTo(11, 5)
        path.lineTo(6, 9)
        path.lineTo(2, 9)
        path.lineTo(2, 15)
        path.lineTo(6, 15)
        path.lineTo(11, 19)
        path.closeSubpath()
        path.addRect(14, 9, 1.5, 6)
    elif icon_type == "mute":
        path.moveTo(11, 5)
        path.lineTo(6, 9)
        path.lineTo(2, 9)
        path.lineTo(2, 15)
        path.lineTo(6, 15)
        path.lineTo(11, 19)
        path.closeSubpath()
        path.moveTo(15, 9)
        path.lineTo(19, 13)
        path.moveTo(19, 9)
        path.lineTo(15, 13)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


# --- MAIN PLAYER ---


class SVGPlayer(QMainWindow):
    def __init__(self, video_path):
        super().__init__()

        self.is_windows = platform.system() == "Windows"
        self.control_height = 65
        self.video_ratio = 0.5625
        self.is_transitioning = False
        self.skip_amount = 5000

        # Multimedia
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.7)
        self.media_player.setAudioOutput(self.audio_output)

        self.video_widget = DoubleClickVideoWidget(self)
        self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
        self.media_player.setVideoOutput(self.video_widget)

        # UI
        self.central_widget = QWidget()
        self.central_widget.setStyleSheet("background-color: black;")
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.main_layout.addWidget(self.video_widget)

        # Controls
        self.controls_widget = QWidget()
        self.controls_widget.setFixedHeight(self.control_height)
        self.controls_widget.setStyleSheet(
            "background-color: #121212; border-top: 1px solid #222;"
        )

        controls_layout = QHBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(15, 0, 15, 0)

        self.back_btn = QPushButton()
        self.back_btn.setIcon(create_white_icon("skip_b"))
        self.back_btn.setIconSize(QSize(26, 26))
        self.back_btn.clicked.connect(lambda: self.skip_video(-1))

        self.play_button = QPushButton()
        self.play_button.setIcon(create_white_icon("play"))
        self.play_button.setIconSize(QSize(36, 36))
        self.play_button.clicked.connect(self.toggle_play)

        self.fwd_btn = QPushButton()
        self.fwd_btn.setIcon(create_white_icon("skip_f"))
        self.fwd_btn.setIconSize(QSize(26, 26))
        self.fwd_btn.clicked.connect(lambda: self.skip_video(1))

        self.toggle_skip_btn = QPushButton("5s")
        self.toggle_skip_btn.setFixedSize(40, 24)
        self.toggle_skip_btn.setStyleSheet(
            "QPushButton { background-color: #2a2a2a; color: #bbb; border-radius: 3px; font-weight: bold; font-size: 10px; }"
        )
        self.toggle_skip_btn.clicked.connect(self.toggle_skip_amount)

        self.seek_slider = ClickableSlider(Qt.Horizontal)
        self.seek_slider.sliderMoved.connect(self.set_position)

        self.vol_button = QPushButton()
        self.vol_button.setIcon(create_white_icon("volume"))
        self.vol_button.setIconSize(QSize(20, 20))
        self.vol_button.clicked.connect(self.toggle_mute)

        self.vol_slider = ClickableSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.valueChanged.connect(self.set_volume)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(
            "color: white; font-family: Arial; font-weight: bold; font-size: 11px;"
        )

        controls_layout.addWidget(self.back_btn)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.fwd_btn)
        controls_layout.addSpacing(5)
        controls_layout.addWidget(self.toggle_skip_btn)
        controls_layout.addWidget(self.seek_slider)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(self.vol_button)
        controls_layout.addWidget(self.vol_slider)
        controls_layout.addWidget(self.time_label)
        self.main_layout.addWidget(self.controls_widget)

        # Signals
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.playbackStateChanged.connect(self.update_play_icon)
        self.media_player.tracksChanged.connect(self.sync_ratio_to_video)

        self.media_player.setSource(QUrl.fromLocalFile(video_path))
        self.resize(1000, 600)

    def sync_ratio_to_video(self):
        """Standardizes height to match video ratio. Used as 'snap_to_ratio'."""
        if self.isFullScreen() or self.is_transitioning:
            return

        size = self.media_player.videoSink().videoSize()
        if size.isValid() and size.width() > 0:
            self.video_ratio = size.height() / size.width()
            target_h = int(self.width() * self.video_ratio) + self.control_height
            if abs(self.height() - target_h) >= 1:
                self.setFixedHeight(target_h)

    def changeEvent(self, event):
        """Handles window state changes using the Mac transition logic."""
        if event.type() == QEvent.WindowStateChange:
            self.is_transitioning = True
            # Unlock any fixed heights
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)

            if self.is_windows:
                # Windows needs more aggressive snapping because it has no transition animation
                QTimer.singleShot(50, self.end_transition)
                QTimer.singleShot(250, self.end_transition)
                QTimer.singleShot(500, self.end_transition)
            else:
                # Mac uses the 500ms delay to wait for the OS zoom animation to finish
                QTimer.singleShot(500, self.end_transition)
        super().changeEvent(event)

    def end_transition(self):
        self.is_transitioning = False
        self.sync_ratio_to_video()

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        # Keeps snapping active during manual drags on both platforms
        # unless we are in the middle of a full-screen transition
        if not self.isFullScreen() and not self.is_transitioning:
            self.sync_ratio_to_video()
        super().resizeEvent(event)

    def toggle_skip_amount(self):
        self.skip_amount = 10000 if self.skip_amount == 5000 else 5000
        self.toggle_skip_btn.setText("10s" if self.skip_amount == 10000 else "5s")

    def set_volume(self, value):
        self.audio_output.setVolume(value / 100)
        self.update_vol_icon()

    def toggle_mute(self):
        self.audio_output.setMuted(not self.audio_output.isMuted())
        self.update_vol_icon()

    def update_vol_icon(self):
        icon = (
            "mute"
            if self.audio_output.isMuted() or self.vol_slider.value() == 0
            else "volume"
        )
        self.vol_button.setIcon(create_white_icon(icon))

    def skip_video(self, direction):
        target = max(0, self.media_player.position() + (direction * self.skip_amount))
        self.media_player.setPosition(target)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()
        elif event.key() == Qt.Key_Right:
            self.skip_video(1)
        elif event.key() == Qt.Key_Left:
            self.skip_video(-1)
        elif event.key() == Qt.Key_F:
            self.toggle_full_screen()
        super().keyPressEvent(event)

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def update_play_icon(self, state):
        icon = "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play"
        self.play_button.setIcon(create_white_icon(icon))

    def set_position(self, pos):
        self.media_player.setPosition(pos)

    def position_changed(self, pos):
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(pos)
        self.update_label()

    def duration_changed(self, dur):
        self.seek_slider.setRange(0, dur)

    def update_label(self):
        p, d = (
            self.media_player.position() // 1000,
            self.media_player.duration() // 1000,
        )
        self.time_label.setText(
            f"{p // 60:02d}:{p % 60:02d} / {d // 60:02d}:{d % 60:02d}"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if platform.system() == "Windows":
        path = r"C:\Users\brduf\source\videoPlayer\Landman.S02E10.1080p.HEVC.x265-MeGusta[EZTVx.to].mkv"
    else:
        path = "/Users/brianduffy/Documents/Landman.S02E10.1080p.HEVC.x265-MeGusta[EZTVx.to].mkv"
    player = SVGPlayer(path)
    player.show()
    sys.exit(app.exec())

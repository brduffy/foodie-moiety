"""Helper functions and utilities."""

import platform

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap


# ---------------------------------------------------------------------------
# Dark-themed dialog styling (shared by any widget that shows QMessageBox etc.)
# ---------------------------------------------------------------------------

DIALOG_STYLE = """
    QMessageBox, QInputDialog {
        background-color: #2a2a2a;
        color: white;
    }
    QLabel {
        color: white;
        background: transparent;
    }
    QLineEdit {
        background-color: #3a3a3a;
        color: white;
        border: 1px solid #555555;
        border-radius: 4px;
        padding: 6px;
    }
    QLineEdit:focus {
        border: 1px solid #0078d4;
    }
    QPushButton {
        background-color: #3a3a3a;
        color: white;
        border: 1px solid #555555;
        border-radius: 4px;
        padding: 8px 18px;
        min-width: 80px;
        font-size: 14px;
    }
    QPushButton:hover {
        background-color: #4a4a4a;
    }
    QPushButton:pressed {
        background-color: #2a2a2a;
    }
"""

PROGRESS_DIALOG_STYLE = """
    QDialog { background-color: #2a2a2a; }
    QLabel { color: white; font-size: 15px; }
    QProgressBar {
        background-color: #3a3a3a;
        border: 1px solid #555555;
        border-radius: 4px;
        height: 18px;
        text-align: center;
        color: white;
        font-size: 12px;
    }
    QProgressBar::chunk {
        background-color: #0078d4;
        border-radius: 3px;
    }
    QPushButton {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 8px 18px; min-width: 80px; font-size: 14px;
    }
    QPushButton:hover { background-color: #4a4a4a; }
    QPushButton:pressed { background-color: #2a2a2a; }
"""


def white_question_icon(size=48):
    """Create a white question-mark icon for dark-themed dialogs."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#555555"))
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("#ffffff"))
    font = QFont()
    font.setPixelSize(int(size * 0.6))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "?")
    p.end()
    return pm


# Mapping of icon names to Segoe Fluent Icons Unicode code points
# Reference: https://learn.microsoft.com/en-us/windows/apps/design/style/segoe-fluent-icons-font
FLUENT_ICON_MAP = {
    # Common UI actions
    "tag": "\uE8EC",
    "pencil": "\uE70F",
    "pencil.and.list.clipboard": "\uE9D5",  # ClipboardList
    "square.and.arrow.up": "\uE72D",  # Share/Export
    "square.and.arrow.down": "\uE896",  # Download/Import
    "delete.left": "\uE750",  # Delete
    "delete.backward": "\uE750",  # Delete
    "xmark.square": "\uE711",  # Cancel/Close
    "link": "\uE71B",
    "document.on.clipboard": "\uE8C8",  # Copy
    "list.bullet.clipboard.fill": "\uE8FD",  # List
    "photo.badge.arrow.down": "\uE8B9",  # Photo
    "video": "\uE714",
    "CopyTo": "\uF413",
    "InsertStep": "\uF461",
    "AppendStep": "\uE8B5",
    "Fullscreen": "\uE740",
    "Copy": "\uE8C8",
    "ClipboardList": "\uF0E3",
    "ClipboardListMirrored": "\uF0E4",
    "ArrowLeft8": "\uF0B0",
    "arrow.left": "\uF0B0",  # Maps SF Symbol name to Windows equivalent
    "waveform": "\uE767",  # Volume3 — speaker with waves (TTS toggle)
    "book": "\uE736",  # Library — book icon for recipe books
    "books.vertical": "\uE8F1",  # Library
    "person.3.sequence": "\uE902",  # Community / People
    "person.crop.circle": "\uE77B",  # Contact / Sign In
    "square.and.pencil": "\uF742",  # New Article / Edit
    "plus.circle": "\uECC8",  # New Recipe
    "chevron.left": "\uE76B",  # Left arrow
    "chevron.right": "\uE76C",  # Right arrow
    "pencil.tip.crop.circle": "\uE9D5",  # View Tips
    "pencil.tip.crop.circle.badge.plus": "\uEDFB",  # Add Tip
    "puzzlepiece": "\uEA86",  # Moieties
    "lightbulb.max": "\uE781",  # Article Tips
    "checkmark.circle": "\uE930",  # Review
    # Additional mappings can be added as needed
}


def winui_icon(name, point_size=16, color="#cccccc"):
    """Load a Segoe Fluent Icon by name as a QIcon (Windows only).

    Uses the same icon names as sf_symbol() for cross-platform compatibility.
    Returns a null QIcon on non-Windows or if the icon isn't found.

    Args:
        name: Icon name (uses SF Symbol naming convention for compatibility)
        point_size: Size in points (default 16)
        color: Hex color code (default "#cccccc")

    Returns:
        QIcon: The icon, or null QIcon if not available
    """
    if platform.system() != "Windows":
        return QIcon()

    # Look up the Unicode code point for this icon
    glyph = FLUENT_ICON_MAP.get(name)
    if not glyph:
        return QIcon()

    # Create a pixmap and render the icon
    size = int(point_size * 4)  # Scale up for quality
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    # Set up the Segoe Fluent Icons font (Windows 11 icon font)
    font = QFont("Segoe Fluent Icons")
    font.setPixelSize(int(size * 0.75))
    painter.setFont(font)

    # Set the color
    painter.setPen(QColor(color))

    # Draw the glyph centered
    painter.drawText(pixmap.rect(), Qt.AlignCenter, glyph)
    painter.end()

    return QIcon(pixmap)


def sf_symbol(name, weight="bold", point_size=16, color="#cccccc"):
    """Load an SF Symbol by name as a QIcon (macOS only).

    Returns a null QIcon on non-macOS or if the symbol isn't found.
    """
    if platform.system() != "Darwin":
        return QIcon()
    try:
        from AppKit import (NSImage, NSImageSymbolConfiguration, NSColor,
                            NSFontWeightRegular, NSFontWeightBold,
                            NSFontWeightHeavy, NSFontWeightBlack)
        from Foundation import NSArray
        weight_map = {"regular": NSFontWeightRegular, "bold": NSFontWeightBold,
                      "heavy": NSFontWeightHeavy, "black": NSFontWeightBlack}
        ns_weight = weight_map.get(weight, NSFontWeightBold)
        weight_config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            float(point_size), ns_weight)
        # Use palette colors so all symbol layers render at full opacity.
        # Pass a real NSArray — PyObjC's OC_PythonArray bridge can crash
        # when AppKit calls mutableCopyWithZone: during symbol rendering.
        qc = QColor(color)
        ns_color = NSColor.colorWithSRGBRed_green_blue_alpha_(
            qc.redF(), qc.greenF(), qc.blueF(), qc.alphaF())
        palette = NSArray.arrayWithArray_([ns_color, ns_color, ns_color])
        color_config = NSImageSymbolConfiguration.configurationWithPaletteColors_(
            palette)
        config = weight_config.configurationByApplyingConfiguration_(color_config)
        ns_image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if ns_image is None:
            return QIcon()
        ns_image = ns_image.imageWithSymbolConfiguration_(config)
        tiff_data = ns_image.TIFFRepresentation()
        qimage = QImage.fromData(bytes(tiff_data))
        return QIcon(QPixmap.fromImage(qimage))
    except Exception:
        return QIcon()


def platform_icon(name, weight="regular", point_size=16, color="#cccccc", windows_name=None):
    """Load a platform-appropriate icon by name.

    Automatically selects SF Symbols on macOS or Segoe Fluent Icons on Windows.
    Returns a null QIcon if the icon isn't available on the current platform.

    Args:
        name: Icon name (uses SF Symbol naming convention)
        weight: Font weight for SF Symbols ("regular", "bold", "heavy", "black")
        point_size: Size in points (default 16)
        color: Hex color code (default "#cccccc")
        windows_name: Optional different icon name for Windows (defaults to name)

    Returns:
        QIcon: The platform-appropriate icon, or null QIcon if not available
    """
    if platform.system() == "Darwin":
        return sf_symbol(name, weight=weight, point_size=point_size, color=color)
    elif platform.system() == "Windows":
        win_icon_name = windows_name if windows_name else name
        return winui_icon(win_icon_name, point_size=point_size, color=color)
    return QIcon()


def create_white_icon(icon_type):
    """
    Create a white icon for media controls.

    Args:
        icon_type: Type of icon to create. Options: "play", "pause", "skip_f",
                   "skip_b", "volume", "mute"

    Returns:
        QIcon: The generated icon
    """
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

    elif icon_type == "stop":
        path.addRect(7, 7, 10, 10)

    elif icon_type == "fullscreen":
        # Four corner brackets (standard expand/fullscreen icon)
        painter.setPen(QPen(QColor("white"), 2))
        painter.setBrush(Qt.NoBrush)
        # Top-left corner
        path.moveTo(8, 3)
        path.lineTo(3, 3)
        path.lineTo(3, 8)
        # Top-right corner
        path.moveTo(16, 3)
        path.lineTo(21, 3)
        path.lineTo(21, 8)
        # Bottom-right corner
        path.moveTo(21, 16)
        path.lineTo(21, 21)
        path.lineTo(16, 21)
        # Bottom-left corner
        path.moveTo(8, 21)
        path.lineTo(3, 21)
        path.lineTo(3, 16)

    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)

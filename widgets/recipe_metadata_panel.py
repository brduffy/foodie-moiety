"""Floating panel for recipe metadata displayed on the intro step."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class RecipeMetadataPanel(QWidget):
    """Floating panel showing recipe metadata on the intro step.

    Displays title, prep/cook/total time, and difficulty.
    Supports view mode (read-only) and edit mode (editable fields).
    """

    # Fraction of the available right-half space to occupy
    WIDTH_RATIO = 0.85
    MIN_WIDTH = 400
    MAX_HEIGHT = 120

    dataChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RecipeMetadataPanel")
        self._editing = False

        # Enable styled background painting for QWidget
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.setStyleSheet("""
            QWidget#RecipeMetadataPanel {
                background-color: rgba(0, 0, 0, 180);
                border: 1px solid #444444;
                border-radius: 10px;
            }
            QLabel {
                color: #cccccc;
                background: transparent;
            }
            QLabel#FieldLabel {
                color: #999999;
                font-size: 11px;
            }
            QLabel#TotalTimeValue {
                color: #ffffff;
                font-weight: 600;
            }
            QLineEdit {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
            QLineEdit:read-only {
                background-color: transparent;
                border: none;
                color: #ffffff;
            }
            QSpinBox {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 6px;
            }
            QSpinBox:focus {
                border-color: #0078d4;
            }
            QSpinBox:disabled {
                background-color: transparent;
                border: none;
                color: #ffffff;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0px;
            }
            QComboBox {
                background-color: #2a2a2a;
                color: white;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 12px;
                min-width: 100px;
            }
            QComboBox:disabled {
                background-color: transparent;
                border: none;
                color: #ffffff;
            }
            QComboBox::drop-down {
                border: none;
                width: 0px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                color: white;
                selection-background-color: #0078d4;
                border: 1px solid #4a4a4a;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 8, 14, 8)
        main_layout.setSpacing(4)

        # Row 1: Title (full width, no label, wraps text)
        self._title_edit = QTextEdit()
        self._title_edit.setObjectName("TitleEdit")
        self._title_edit.setStyleSheet("""
            QTextEdit#TitleEdit {
                background-color: transparent;
                color: #ffffff;
                border: none;
                font-size: 15px;
                font-weight: 600;
                padding: 0px;
            }
            QTextEdit#TitleEdit:focus {
                background-color: #2a2a2a;
                border: 1px solid #0078d4;
                border-radius: 4px;
            }
            QTextEdit#TitleEdit:disabled {
                background-color: transparent;
                border: none;
                color: #ffffff;
            }
        """)
        self._title_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._title_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._title_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self._title_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._title_edit.setFixedHeight(24)
        self._title_edit.textChanged.connect(self._on_title_changed)
        # Auto-resize height when document layout changes
        self._title_edit.document().documentLayout().documentSizeChanged.connect(
            self._on_doc_size_changed
        )
        main_layout.addWidget(self._title_edit)

        # Row 2: All metadata inline — Prep, Cook, Total, Difficulty, Producer
        fields_row = QHBoxLayout()
        fields_row.setSpacing(12)

        def _add_field(label_text, widget):
            lbl = QLabel(label_text)
            lbl.setObjectName("FieldLabel")
            fields_row.addWidget(lbl)
            fields_row.addWidget(widget)

        self._prep_spin = QSpinBox()
        self._prep_spin.setRange(0, 999)
        self._prep_spin.setSuffix(" min")
        self._prep_spin.setFixedWidth(70)
        self._prep_spin.valueChanged.connect(self._on_time_changed)
        _add_field("Prep", self._prep_spin)

        self._cook_spin = QSpinBox()
        self._cook_spin.setRange(0, 999)
        self._cook_spin.setSuffix(" min")
        self._cook_spin.setFixedWidth(70)
        self._cook_spin.valueChanged.connect(self._on_time_changed)
        _add_field("Cook", self._cook_spin)

        self._total_label = QLabel("0 min")
        self._total_label.setObjectName("TotalTimeValue")
        _add_field("Total", self._total_label)

        self._difficulty_combo = QComboBox()
        self._difficulty_combo.addItems(["", "Beginner", "Intermediate", "Expert"])
        self._difficulty_combo.currentTextChanged.connect(self._on_data_changed)
        _add_field("Diff", self._difficulty_combo)

        fields_row.addStretch()
        main_layout.addLayout(fields_row)

        self.set_read_only(True)
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_read_only(self, read_only: bool):
        """Toggle between view mode (read-only) and edit mode."""
        self._editing = not read_only

        self._title_edit.setEnabled(not read_only)
        self._prep_spin.setEnabled(not read_only)
        self._cook_spin.setEnabled(not read_only)
        self._difficulty_combo.setEnabled(not read_only)

    def load_data(
        self,
        title: str,
        prep_time: int | None,
        cook_time: int | None,
        difficulty: str | None,
        producer: str = "",
    ):
        """Load recipe metadata into the panel."""
        self._title_edit.blockSignals(True)
        self._prep_spin.blockSignals(True)
        self._cook_spin.blockSignals(True)
        self._difficulty_combo.blockSignals(True)

        self._title_edit.setPlainText(title or "")
        self._prep_spin.setValue(prep_time or 0)
        self._cook_spin.setValue(cook_time or 0)
        self._update_total_time()

        diff_text = difficulty or ""
        idx = self._difficulty_combo.findText(diff_text)
        self._difficulty_combo.setCurrentIndex(max(0, idx))

        self._title_edit.blockSignals(False)
        self._prep_spin.blockSignals(False)
        self._cook_spin.blockSignals(False)
        self._difficulty_combo.blockSignals(False)

    def get_data(self) -> dict:
        """Return current metadata values as a dict."""
        diff = self._difficulty_combo.currentText()
        return {
            "title": self._title_edit.toPlainText().strip(),
            "prep_time_min": self._prep_spin.value() or None,
            "cook_time_min": self._cook_spin.value() or None,
            "difficulty": diff if diff else None,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_data_changed(self):
        self.dataChanged.emit()

    def _on_title_changed(self):
        """Handle title text changes - emit signal."""
        self.dataChanged.emit()

    def _on_doc_size_changed(self, size):
        """Resize title QTextEdit when document layout changes."""
        # Add frame margins and clamp to reasonable bounds
        height = max(24, min(54, int(size.height()) + 6))
        self._title_edit.setFixedHeight(height)

    def _on_time_changed(self):
        self._update_total_time()
        self.dataChanged.emit()

    def _update_total_time(self):
        total = self._prep_spin.value() + self._cook_spin.value()
        self._total_label.setText(self._format_time(total))

    @staticmethod
    def _format_time(minutes: int) -> str:
        """Format minutes as 'Xh Ym' or 'X min'."""
        if minutes < 60:
            return f"{minutes} min"
        hours, mins = divmod(minutes, 60)
        if mins == 0:
            return f"{hours}h"
        return f"{hours}h {mins}m"

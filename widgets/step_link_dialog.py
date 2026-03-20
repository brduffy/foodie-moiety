"""Step link dialog - select a step to link to."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class StepLinkDialog(QDialog):
    """Dialog for selecting which step to link to."""

    def __init__(self, step_count, parent=None):
        """Initialize the dialog.

        Args:
            step_count: Total number of steps (including intro at index 0)
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Link to Step")
        self.setModal(True)
        self.setFixedWidth(320)

        self._selected_step = 0

        # Dark theme styling (matches app style)
        self.setStyleSheet("""
            QDialog {
                background-color: #2a2a2a;
            }
            QLabel {
                color: white;
                font-size: 13px;
            }
            QLabel#WarningLabel {
                color: #f0c040;
                font-size: 12px;
                padding: 8px;
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 4px;
            }
            QComboBox {
                background-color: #3a3a3a;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 6px 10px;
                min-width: 150px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid white;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: #3a3a3a;
                color: white;
                selection-background-color: #0078d4;
                border: 1px solid #555555;
            }
            QPushButton {
                background-color: #3a3a3a;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 6px 16px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QPushButton#PrimaryButton {
                background-color: #0078d4;
                border-color: #0078d4;
            }
            QPushButton#PrimaryButton:hover {
                background-color: #1084d8;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Instruction label
        instruction = QLabel("Select the step to link to:")
        layout.addWidget(instruction)

        # Step dropdown
        self._step_combo = QComboBox()
        for i in range(step_count):
            if i == 0:
                self._step_combo.addItem("Intro", 0)
            else:
                self._step_combo.addItem(f"Step {i}", i)
        layout.addWidget(self._step_combo)

        # Warning label
        warning = QLabel(
            "Note: Step links only work in view mode.\n"
            "They cannot be clicked while editing."
        )
        warning.setObjectName("WarningLabel")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("Create Link")
        ok_btn.setObjectName("PrimaryButton")
        ok_btn.clicked.connect(self._on_ok_clicked)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

    def _on_ok_clicked(self):
        """Handle OK button click."""
        self._selected_step = self._step_combo.currentData()
        self.accept()

    def selected_step(self):
        """Return the selected step index (0-based)."""
        return self._selected_step

"""Create recipe dialog - enter title and description for a new recipe."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)


class CreateRecipeDialog(QDialog):
    """Dialog for entering title and description when creating a recipe from clipboard."""

    def __init__(self, parent=None):
        """Initialize the dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Create Recipe from Clipboard")
        self.setModal(True)
        self.setFixedWidth(400)

        # Dark theme styling (matches app style)
        self.setStyleSheet("""
            QDialog {
                background-color: #2a2a2a;
            }
            QLabel {
                color: white;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #3a3a3a;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 8px 10px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
            QTextEdit {
                background-color: #3a3a3a;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 8px;
                font-size: 13px;
            }
            QTextEdit:focus {
                border-color: #0078d4;
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
            QPushButton#PrimaryButton:disabled {
                background-color: #555555;
                border-color: #555555;
                color: #888888;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Title input
        title_label = QLabel("Title:")
        layout.addWidget(title_label)

        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("Enter recipe title...")
        self._title_input.textChanged.connect(self._update_create_button)
        layout.addWidget(self._title_input)

        # Description input
        desc_label = QLabel("Description:")
        layout.addWidget(desc_label)

        self._desc_input = QTextEdit()
        self._desc_input.setPlaceholderText("Enter a brief description...")
        self._desc_input.setFixedHeight(100)
        layout.addWidget(self._desc_input)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        self._create_btn = QPushButton("Create")
        self._create_btn.setObjectName("PrimaryButton")
        self._create_btn.clicked.connect(self.accept)
        self._create_btn.setDefault(True)
        self._create_btn.setEnabled(False)  # Disabled until title is entered
        button_layout.addWidget(self._create_btn)

        layout.addLayout(button_layout)

        # Focus the title input
        self._title_input.setFocus()

    def _update_create_button(self):
        """Enable/disable create button based on title input."""
        has_title = bool(self._title_input.text().strip())
        self._create_btn.setEnabled(has_title)

    def get_title(self):
        """Return the entered title."""
        return self._title_input.text().strip()

    def get_description(self):
        """Return the entered description."""
        return self._desc_input.toPlainText().strip()

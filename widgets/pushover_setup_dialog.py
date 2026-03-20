"""Pushover setup dialog — configure API credentials for push notifications."""

from PySide6.QtCore import QSettings, Qt
from utils.paths import SETTINGS_PATH
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)


class PushoverSetupDialog(QDialog):
    """Dialog for configuring Pushover API credentials."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pushover Setup")
        self.setModal(True)
        self.setMinimumWidth(560)

        self.setStyleSheet("""
            QDialog { background-color: #2a2a2a; }
            QLabel { color: white; font-size: 15px; }
            QLabel#SectionHeader {
                color: #66ccff; font-size: 18px; font-weight: bold;
            }
            QLabel#ExplainerText {
                color: #aaaaaa; font-size: 14px; line-height: 1.4;
            }
            QLabel#StatusLabel { font-size: 14px; padding: 4px 0px; }
            QLineEdit {
                background-color: #3a3a3a; color: white;
                border: 1px solid #555555; border-radius: 4px;
                padding: 8px 10px; font-size: 15px;
                font-family: monospace;
            }
            QLineEdit:focus { border-color: #0078d4; }
            QPushButton {
                background-color: #3a3a3a; color: white;
                border: 1px solid #555555; border-radius: 4px;
                padding: 8px 18px; min-width: 80px; font-size: 14px;
            }
            QPushButton:hover { background-color: #4a4a4a; }
            QPushButton:pressed { background-color: #2a2a2a; }
            QPushButton#PrimaryButton {
                background-color: #0078d4; border-color: #0078d4;
            }
            QPushButton#PrimaryButton:hover { background-color: #1084d8; }
            QPushButton#PrimaryButton:disabled {
                background-color: #555555; border-color: #555555; color: #888888;
            }
            QPushButton#TestButton {
                background-color: #2a5a2a; border-color: #3a7a3a;
            }
            QPushButton#TestButton:hover { background-color: #3a7a3a; }
            QPushButton#TestButton:disabled {
                background-color: #555555; border-color: #555555; color: #888888;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # --- Explainer ---
        header = QLabel("Send Ingredients to Your Phone")
        header.setObjectName("SectionHeader")
        layout.addWidget(header)

        explainer = QLabel(
            "This feature uses Pushover to send your ingredient list "
            "as a push notification to your phone.\n\n"
            "Pushover costs a one-time $5 per platform (iOS or Android) "
            "after a 30-day free trial. There are no subscription fees. "
            "Your purchase also works with many other apps and services "
            "that support Pushover."
        )
        explainer.setObjectName("ExplainerText")
        explainer.setWordWrap(True)
        explainer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
        layout.addWidget(explainer)

        # --- Setup steps ---
        steps = QLabel(
            "<b>Setup steps:</b><br><br>"
            "1. Install the <b>Pushover</b> app on your phone "
            "(App Store or Google Play)<br>"
            "2. Create an account at "
            "<span style='color:#66ccff'>pushover.net</span><br>"
            "3. Copy your <b>User Key</b> from the dashboard<br>"
            "4. Click <b>Create an Application/API Token</b> and "
            "copy the <b>API Token</b><br>"
            "5. Enter both below"
        )
        steps.setObjectName("ExplainerText")
        steps.setWordWrap(True)
        steps.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
        layout.addWidget(steps)

        layout.addSpacing(6)

        # --- API Token ---
        layout.addWidget(QLabel("API Token (Application Token):"))
        self._token_input = QLineEdit()
        self._token_input.setPlaceholderText("e.g. azGDORePK8gMaC0QOYAMyEEuzJnyUi")
        self._token_input.setMaxLength(30)
        self._token_input.textChanged.connect(self._on_fields_changed)
        layout.addWidget(self._token_input)

        # --- User Key ---
        layout.addWidget(QLabel("User Key:"))
        self._user_input = QLineEdit()
        self._user_input.setPlaceholderText("e.g. uQiRzpo4DXghDmr9QZehQ27cQ76hY8")
        self._user_input.setMaxLength(30)
        self._user_input.textChanged.connect(self._on_fields_changed)
        layout.addWidget(self._user_input)

        # --- Status label ---
        self._status_label = QLabel("")
        self._status_label.setObjectName("StatusLabel")
        self._status_label.setWordWrap(True)
        self._status_label.hide()
        layout.addWidget(self._status_label)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._test_btn = QPushButton("Test Connection")
        self._test_btn.setObjectName("TestButton")
        self._test_btn.setEnabled(False)
        self._test_btn.clicked.connect(self._on_test)
        btn_layout.addWidget(self._test_btn)

        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("PrimaryButton")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.accept)
        self._save_btn.setDefault(True)
        btn_layout.addWidget(self._save_btn)

        layout.addLayout(btn_layout)

        # --- Pre-fill from settings ---
        settings = QSettings(str(SETTINGS_PATH), QSettings.IniFormat)
        saved_token = settings.value("pushover_api_token", "", type=str)
        saved_user = settings.value("pushover_user_key", "", type=str)
        if saved_token:
            self._token_input.setText(saved_token)
        if saved_user:
            self._user_input.setText(saved_user)

        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

    def _on_fields_changed(self):
        has_both = bool(self._token_input.text().strip()) and bool(
            self._user_input.text().strip()
        )
        self._save_btn.setEnabled(has_both)
        self._test_btn.setEnabled(has_both)
        self._status_label.hide()

    def _on_test(self):
        from utils.pushover import send_pushover_message

        token = self._token_input.text().strip()
        user = self._user_input.text().strip()

        self._test_btn.setEnabled(False)
        self._test_btn.setText("Sending...")
        # Process events so button text updates before blocking call
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        ok, detail = send_pushover_message(
            token, user,
            message="Foodie Moiety connected successfully!",
            title="Test Notification",
        )

        self._test_btn.setText("Test Connection")
        self._test_btn.setEnabled(True)
        self._status_label.show()

        if ok:
            self._status_label.setText("Test sent \u2014 check your phone!")
            self._status_label.setStyleSheet("color: #44cc44; font-size: 14px;")
        else:
            self._status_label.setText(f"Failed: {detail}")
            self._status_label.setStyleSheet("color: #ff6666; font-size: 14px;")

    def get_credentials(self) -> tuple[str, str]:
        """Return (api_token, user_key) entered by the user."""
        return (
            self._token_input.text().strip(),
            self._user_input.text().strip(),
        )

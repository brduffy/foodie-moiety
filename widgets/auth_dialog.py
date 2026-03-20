"""Authentication dialog — sign in, verify email, reset password.

Account creation is handled on the website to properly set expectations
about what accounts are for (cloud/community features, not local library).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
)

# Page indices
_PAGE_SIGN_IN = 0
_PAGE_VERIFY_EMAIL = 1
_PAGE_FORGOT_PASSWORD = 2
_PAGE_RESET_PASSWORD = 3

_STYLESHEET = """
    QDialog { background-color: #2a2a2a; }
    QLabel { color: white; font-size: 15px; }
    QLabel#SectionHeader {
        color: #66ccff; font-size: 18px; font-weight: bold;
    }
    QLabel#HintText {
        color: #cccccc; font-size: 13px;
    }
    QLabel#LinkLabel {
        color: #66ccff; font-size: 14px;
    }
    QLabel#StatusLabel { font-size: 14px; padding: 4px 0px; }
    QLineEdit {
        background-color: #3a3a3a; color: white;
        border: 1px solid #555555; border-radius: 4px;
        padding: 8px 10px; font-size: 15px;
    }
    QLineEdit:hover { border-color: #888888; background-color: #404040; }
    QLineEdit:focus { border-color: #0078d4; background-color: #333333; }
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
"""


class AuthDialog(QDialog):
    """Multi-page authentication dialog for Cognito sign-in / sign-up."""

    def __init__(self, parent=None, prefill_email="", heading="Sign In to Community"):
        super().__init__(parent)
        self.setWindowTitle("Sign In")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(_STYLESHEET)

        self._email_for_verify = ""
        self._tokens: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        self._stack.addWidget(self._build_sign_in_page(prefill_email, heading))
        self._stack.addWidget(self._build_verify_page())
        self._stack.addWidget(self._build_forgot_password_page())
        self._stack.addWidget(self._build_reset_password_page())

        self._stack.setCurrentIndex(_PAGE_SIGN_IN)
        # Auto-focus: if email is prefilled, focus password; otherwise email
        if prefill_email:
            self._si_password.setFocus()
        else:
            self._si_email.setFocus()

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _build_sign_in_page(self, prefill_email: str, heading: str = "Sign In to Community"):
        page = QVBoxLayout()
        page.setSpacing(12)

        header = QLabel(heading)
        header.setObjectName("SectionHeader")
        page.addWidget(header)
        page.addSpacing(4)

        context = QLabel(
            "An account lets you upload recipes, purchase books, and access "
            "the Foodie Moiety marketplace. Free accounts can upload and "
            "download. Creator accounts can also sell recipe books. Your "
            "local recipe library is available with or without an account."
        )
        context.setObjectName("HintText")
        context.setWordWrap(True)
        page.addWidget(context)
        page.addSpacing(4)

        page.addWidget(QLabel("Email:"))
        self._si_email = QLineEdit()
        self._si_email.setPlaceholderText("you@example.com")
        self._si_email.setCursor(Qt.IBeamCursor)
        if prefill_email:
            self._si_email.setText(prefill_email)
        self._si_email.textChanged.connect(self._on_si_fields_changed)
        page.addWidget(self._si_email)

        page.addWidget(QLabel("Password:"))
        self._si_password = QLineEdit()
        self._si_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._si_password.setPlaceholderText("Your password")
        self._si_password.setCursor(Qt.IBeamCursor)
        self._si_password.textChanged.connect(self._on_si_fields_changed)
        page.addWidget(self._si_password)

        self._si_status = QLabel("")
        self._si_status.setObjectName("StatusLabel")
        self._si_status.setWordWrap(True)
        self._si_status.hide()
        page.addWidget(self._si_status)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._si_btn = QPushButton("Sign In")
        self._si_btn.setObjectName("PrimaryButton")
        self._si_btn.setEnabled(False)
        self._si_btn.setDefault(True)
        self._si_btn.clicked.connect(self._on_sign_in)
        btn_row.addWidget(self._si_btn)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        page.addLayout(btn_row)

        # Links
        page.addSpacing(8)
        links = QHBoxLayout()
        create_link = QLabel(
            "<a style='color:#66ccff; text-decoration:none' href='#'>"
            "Sign up on FoodieMoiety.com</a>"
        )
        create_link.setObjectName("LinkLabel")
        create_link.setCursor(Qt.PointingHandCursor)
        create_link.linkActivated.connect(self._open_signup_website)
        links.addWidget(create_link)
        links.addStretch()
        forgot_link = QLabel("<a style='color:#66ccff; text-decoration:none' href='#'>Forgot Password?</a>")
        forgot_link.setObjectName("LinkLabel")
        forgot_link.setCursor(Qt.PointingHandCursor)
        forgot_link.linkActivated.connect(lambda: self._go_to_forgot())
        links.addWidget(forgot_link)
        page.addLayout(links)

        w = self._wrap_page(page)
        # Connect Enter key on password field
        self._si_password.returnPressed.connect(self._on_sign_in)
        return w

    def _build_verify_page(self):
        page = QVBoxLayout()
        page.setSpacing(12)

        header = QLabel("Verify Your Email")
        header.setObjectName("SectionHeader")
        page.addWidget(header)
        page.addSpacing(4)

        self._vf_instruction = QLabel("Enter the 6-digit code sent to your email.")
        self._vf_instruction.setWordWrap(True)
        page.addWidget(self._vf_instruction)

        page.addWidget(QLabel("Verification Code:"))
        self._vf_code = QLineEdit()
        self._vf_code.setPlaceholderText("123456")
        self._vf_code.setMaxLength(6)
        self._vf_code.setCursor(Qt.IBeamCursor)
        self._vf_code.textChanged.connect(self._on_vf_fields_changed)
        page.addWidget(self._vf_code)

        self._vf_status = QLabel("")
        self._vf_status.setObjectName("StatusLabel")
        self._vf_status.setWordWrap(True)
        self._vf_status.hide()
        page.addWidget(self._vf_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._vf_btn = QPushButton("Verify")
        self._vf_btn.setObjectName("PrimaryButton")
        self._vf_btn.setEnabled(False)
        self._vf_btn.setDefault(True)
        self._vf_btn.clicked.connect(self._on_verify)
        btn_row.addWidget(self._vf_btn)
        btn_row.addStretch()
        page.addLayout(btn_row)

        page.addSpacing(8)
        resend_link = QLabel("<a style='color:#66ccff; text-decoration:none' href='#'>Resend Code</a>")
        resend_link.setObjectName("LinkLabel")
        resend_link.setCursor(Qt.PointingHandCursor)
        resend_link.linkActivated.connect(self._on_resend_code)
        page.addWidget(resend_link)

        self._vf_code.returnPressed.connect(self._on_verify)
        return self._wrap_page(page)

    def _build_forgot_password_page(self):
        page = QVBoxLayout()
        page.setSpacing(12)

        header = QLabel("Reset Password")
        header.setObjectName("SectionHeader")
        page.addWidget(header)
        page.addSpacing(4)

        explain = QLabel("Enter your email and we'll send a reset code.")
        explain.setWordWrap(True)
        page.addWidget(explain)

        page.addWidget(QLabel("Email:"))
        self._fp_email = QLineEdit()
        self._fp_email.setPlaceholderText("you@example.com")
        self._fp_email.setCursor(Qt.IBeamCursor)
        self._fp_email.textChanged.connect(self._on_fp_fields_changed)
        page.addWidget(self._fp_email)

        self._fp_status = QLabel("")
        self._fp_status.setObjectName("StatusLabel")
        self._fp_status.setWordWrap(True)
        self._fp_status.hide()
        page.addWidget(self._fp_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._fp_btn = QPushButton("Send Reset Code")
        self._fp_btn.setObjectName("PrimaryButton")
        self._fp_btn.setEnabled(False)
        self._fp_btn.setDefault(True)
        self._fp_btn.clicked.connect(self._on_forgot_password)
        btn_row.addWidget(self._fp_btn)
        btn_row.addStretch()
        page.addLayout(btn_row)

        page.addSpacing(8)
        back_link = QLabel("<a style='color:#66ccff; text-decoration:none' href='#'>Back to Sign In</a>")
        back_link.setObjectName("LinkLabel")
        back_link.setCursor(Qt.PointingHandCursor)
        back_link.linkActivated.connect(lambda: self._go_to_page(_PAGE_SIGN_IN))
        page.addWidget(back_link)

        self._fp_email.returnPressed.connect(self._on_forgot_password)
        return self._wrap_page(page)

    def _build_reset_password_page(self):
        page = QVBoxLayout()
        page.setSpacing(12)

        header = QLabel("Set New Password")
        header.setObjectName("SectionHeader")
        page.addWidget(header)
        page.addSpacing(4)

        self._rp_instruction = QLabel("Enter the code sent to your email and your new password.")
        self._rp_instruction.setWordWrap(True)
        page.addWidget(self._rp_instruction)

        page.addWidget(QLabel("Verification Code:"))
        self._rp_code = QLineEdit()
        self._rp_code.setPlaceholderText("123456")
        self._rp_code.setMaxLength(6)
        self._rp_code.setCursor(Qt.IBeamCursor)
        self._rp_code.textChanged.connect(self._on_rp_fields_changed)
        page.addWidget(self._rp_code)

        page.addWidget(QLabel("New Password:"))
        self._rp_password = QLineEdit()
        self._rp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._rp_password.setPlaceholderText("Min 8 chars")
        self._rp_password.setCursor(Qt.IBeamCursor)
        self._rp_password.textChanged.connect(self._on_rp_fields_changed)
        page.addWidget(self._rp_password)

        page.addWidget(QLabel("Confirm Password:"))
        self._rp_confirm = QLineEdit()
        self._rp_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self._rp_confirm.setPlaceholderText("Re-enter password")
        self._rp_confirm.setCursor(Qt.IBeamCursor)
        self._rp_confirm.textChanged.connect(self._on_rp_fields_changed)
        page.addWidget(self._rp_confirm)

        hint = QLabel("8+ characters with uppercase, lowercase, and a digit")
        hint.setObjectName("HintText")
        page.addWidget(hint)

        self._rp_status = QLabel("")
        self._rp_status.setObjectName("StatusLabel")
        self._rp_status.setWordWrap(True)
        self._rp_status.hide()
        page.addWidget(self._rp_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._rp_btn = QPushButton("Reset Password")
        self._rp_btn.setObjectName("PrimaryButton")
        self._rp_btn.setEnabled(False)
        self._rp_btn.setDefault(True)
        self._rp_btn.clicked.connect(self._on_reset_password)
        btn_row.addWidget(self._rp_btn)
        btn_row.addStretch()
        page.addLayout(btn_row)

        self._rp_confirm.returnPressed.connect(self._on_reset_password)
        return self._wrap_page(page)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_page(layout):
        """Wrap a QVBoxLayout in a container widget."""
        from PySide6.QtWidgets import QWidget
        w = QWidget()
        w.setLayout(layout)
        return w

    def _go_to_page(self, index):
        self._stack.setCurrentIndex(index)
        titles = {
            _PAGE_SIGN_IN: "Sign In",
            _PAGE_VERIFY_EMAIL: "Verify Email",
            _PAGE_FORGOT_PASSWORD: "Reset Password",
            _PAGE_RESET_PASSWORD: "Set New Password",
        }
        self.setWindowTitle(titles.get(index, "Sign In"))

    def _open_signup_website(self, _link=""):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from services.community_api import WEBSITE_URL
        QDesktopServices.openUrl(QUrl(f"{WEBSITE_URL}/subscription?source=desktop"))

    def _go_to_forgot(self):
        email = self._si_email.text().strip()
        if email:
            self._fp_email.setText(email)
        self._go_to_page(_PAGE_FORGOT_PASSWORD)

    def _show_status(self, label, text, error=True):
        label.setText(text)
        color = "#ff6666" if error else "#44cc44"
        label.setStyleSheet(f"color: {color}; font-size: 14px;")
        label.show()

    def _set_busy(self, button, busy, busy_text="Working..."):
        """Disable button and show busy text, or restore."""
        button.setEnabled(not busy)
        if busy:
            button._original_text = button.text()
            button.setText(busy_text)
        else:
            button.setText(getattr(button, "_original_text", button.text()))
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    # ------------------------------------------------------------------
    # Field validators
    # ------------------------------------------------------------------

    def _on_si_fields_changed(self):
        has_both = bool(self._si_email.text().strip()) and bool(self._si_password.text())
        self._si_btn.setEnabled(has_both)
        self._si_status.hide()

    def _on_vf_fields_changed(self):
        self._vf_btn.setEnabled(len(self._vf_code.text().strip()) >= 6)
        self._vf_status.hide()

    def _on_fp_fields_changed(self):
        self._fp_btn.setEnabled(bool(self._fp_email.text().strip()))
        self._fp_status.hide()

    def _on_rp_fields_changed(self):
        has_code = len(self._rp_code.text().strip()) >= 6
        has_pw = bool(self._rp_password.text())
        has_confirm = bool(self._rp_confirm.text())
        self._rp_btn.setEnabled(has_code and has_pw and has_confirm)
        self._rp_status.hide()

    # ------------------------------------------------------------------
    # Actions (blocking — call from main thread for simplicity since
    # the dialog is modal; consider _IOWorker for longer operations)
    # ------------------------------------------------------------------

    def _on_sign_in(self):
        if not self._si_btn.isEnabled():
            return
        from services.cognito_auth import AuthError, sign_in

        email = self._si_email.text().strip()
        password = self._si_password.text()

        self._set_busy(self._si_btn, True, "Signing in...")
        try:
            tokens = sign_in(email, password)
            self._tokens = tokens
            self._tokens["email"] = email
            self.accept()
        except AuthError as e:
            if e.code == "UserNotConfirmedException":
                self._email_for_verify = email
                self._vf_instruction.setText(
                    f"Your email <b>{email}</b> hasn't been verified yet. "
                    "Enter the code we sent."
                )
                self._go_to_page(_PAGE_VERIFY_EMAIL)
            else:
                self._show_status(self._si_status, str(e))
        except Exception:
            self._show_status(self._si_status, "Unable to connect — please check your internet connection")
        finally:
            self._set_busy(self._si_btn, False)

    def _on_verify(self):
        if not self._vf_btn.isEnabled():
            return
        from services.cognito_auth import AuthError, confirm_sign_up

        code = self._vf_code.text().strip()

        self._set_busy(self._vf_btn, True, "Verifying...")
        try:
            confirm_sign_up(self._email_for_verify, code)
            # Pre-fill sign-in page and switch back
            self._si_email.setText(self._email_for_verify)
            self._si_password.clear()
            self._show_status(self._si_status, "Email verified — sign in to continue", error=False)
            self._go_to_page(_PAGE_SIGN_IN)
        except AuthError as e:
            self._show_status(self._vf_status, str(e))
        except Exception:
            self._show_status(self._vf_status, "Unable to connect — please check your internet connection")
        finally:
            self._set_busy(self._vf_btn, False)

    def _on_resend_code(self):
        from services.cognito_auth import AuthError, resend_confirmation_code
        try:
            resend_confirmation_code(self._email_for_verify)
            self._show_status(self._vf_status, "New code sent — check your email", error=False)
        except AuthError as e:
            self._show_status(self._vf_status, str(e))
        except Exception:
            self._show_status(self._vf_status, "Unable to connect — please check your internet connection")

    def _on_forgot_password(self):
        if not self._fp_btn.isEnabled():
            return
        from services.cognito_auth import AuthError, forgot_password

        email = self._fp_email.text().strip()

        self._set_busy(self._fp_btn, True, "Sending code...")
        try:
            forgot_password(email)
            self._email_for_verify = email
            self._rp_instruction.setText(
                f"Enter the reset code sent to <b>{email}</b> and your new password."
            )
            self._go_to_page(_PAGE_RESET_PASSWORD)
        except AuthError as e:
            self._show_status(self._fp_status, str(e))
        except Exception:
            self._show_status(self._fp_status, "Unable to connect — please check your internet connection")
        finally:
            self._set_busy(self._fp_btn, False)

    def _on_reset_password(self):
        if not self._rp_btn.isEnabled():
            return
        from services.cognito_auth import AuthError, confirm_forgot_password

        code = self._rp_code.text().strip()
        password = self._rp_password.text()
        confirm = self._rp_confirm.text()

        if password != confirm:
            self._show_status(self._rp_status, "Passwords don't match")
            return

        self._set_busy(self._rp_btn, True, "Resetting...")
        try:
            confirm_forgot_password(self._email_for_verify, code, password)
            self._si_email.setText(self._email_for_verify)
            self._si_password.clear()
            self._show_status(self._si_status, "Password reset — sign in with your new password", error=False)
            self._go_to_page(_PAGE_SIGN_IN)
        except AuthError as e:
            self._show_status(self._rp_status, str(e))
        except Exception:
            self._show_status(self._rp_status, "Unable to connect — please check your internet connection")
        finally:
            self._set_busy(self._rp_btn, False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_tokens(self) -> dict | None:
        """Return auth tokens after successful sign-in, or None."""
        return self._tokens

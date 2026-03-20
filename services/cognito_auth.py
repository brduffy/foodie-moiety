"""Cognito authentication service — SRP-based sign-in via pycognito."""

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pycognito import AWSSRP

from utils.config import (
    COGNITO_REGION as REGION,
    COGNITO_USER_POOL_ID as USER_POOL_ID,
    COGNITO_CLIENT_ID as CLIENT_ID,
)

_boto_config = Config(region_name=REGION)


class AuthError(Exception):
    """Raised when a Cognito operation fails with a user-facing message."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def _client():
    """Create a fresh cognito-idp client (no credentials needed for public flows)."""
    return boto3.client(
        "cognito-idp",
        config=_boto_config,
        aws_access_key_id="",
        aws_secret_access_key="",
    )


def _friendly_error(e: ClientError) -> AuthError:
    """Convert a Cognito ClientError to a user-friendly AuthError."""
    code = e.response["Error"]["Code"]
    msg = e.response["Error"]["Message"]
    friendly = {
        "NotAuthorizedException": "Incorrect email or password",
        "UserNotFoundException": "No account found with that email",
        "UsernameExistsException": "An account with that email already exists",
        "InvalidPasswordException": msg,
        "CodeMismatchException": "Incorrect verification code",
        "ExpiredCodeException": "Verification code has expired — request a new one",
        "LimitExceededException": "Too many attempts — please wait and try again",
        "TooManyRequestsException": "Too many requests — please wait and try again",
        "InvalidParameterException": msg,
        "UserNotConfirmedException": "Email not verified",
    }
    return AuthError(friendly.get(code, msg), code)


def sign_in(email: str, password: str) -> dict:
    """Authenticate via SRP. Returns dict with id_token, access_token,
    refresh_token, and expires_in. Raises AuthError on failure."""
    try:
        srp = AWSSRP(
            username=email,
            password=password,
            pool_id=USER_POOL_ID,
            client_id=CLIENT_ID,
            client=_client(),
        )
        tokens = srp.authenticate_user()
        result = tokens["AuthenticationResult"]
        return {
            "id_token": result["IdToken"],
            "access_token": result["AccessToken"],
            "refresh_token": result["RefreshToken"],
            "expires_in": result["ExpiresIn"],
        }
    except ClientError as e:
        raise _friendly_error(e) from e


def sign_up(email: str, password: str) -> bool:
    """Create a new account. Returns True on success. Raises AuthError."""
    try:
        client = _client()
        client.sign_up(
            ClientId=CLIENT_ID,
            Username=email,
            Password=password,
            UserAttributes=[{"Name": "email", "Value": email}],
        )
        return True
    except ClientError as e:
        raise _friendly_error(e) from e


def confirm_sign_up(email: str, code: str) -> bool:
    """Confirm account with verification code. Returns True. Raises AuthError."""
    try:
        client = _client()
        client.confirm_sign_up(
            ClientId=CLIENT_ID,
            Username=email,
            ConfirmationCode=code,
        )
        return True
    except ClientError as e:
        raise _friendly_error(e) from e


def resend_confirmation_code(email: str) -> bool:
    """Resend the email verification code. Returns True. Raises AuthError."""
    try:
        client = _client()
        client.resend_confirmation_code(
            ClientId=CLIENT_ID,
            Username=email,
        )
        return True
    except ClientError as e:
        raise _friendly_error(e) from e


def refresh_tokens(refresh_token: str) -> dict:
    """Get fresh id/access tokens using a refresh token. Returns dict with
    id_token, access_token, and expires_in. Raises AuthError."""
    try:
        client = _client()
        resp = client.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
        )
        result = resp["AuthenticationResult"]
        return {
            "id_token": result["IdToken"],
            "access_token": result["AccessToken"],
            "expires_in": result["ExpiresIn"],
        }
    except ClientError as e:
        raise _friendly_error(e) from e


def forgot_password(email: str) -> bool:
    """Send a password reset code to the user's email. Raises AuthError."""
    try:
        client = _client()
        client.forgot_password(
            ClientId=CLIENT_ID,
            Username=email,
        )
        return True
    except ClientError as e:
        raise _friendly_error(e) from e


def confirm_forgot_password(email: str, code: str, new_password: str) -> bool:
    """Reset password with verification code. Raises AuthError."""
    try:
        client = _client()
        client.confirm_forgot_password(
            ClientId=CLIENT_ID,
            Username=email,
            ConfirmationCode=code,
            Password=new_password,
        )
        return True
    except ClientError as e:
        raise _friendly_error(e) from e

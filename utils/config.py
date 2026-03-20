"""Load application configuration from config.json.

In dev mode, reads from the project root.
In frozen (PyInstaller) builds, reads from the bundle directory.

If config.json is missing, raises an error with setup instructions.
"""

import json
import sys
from pathlib import Path


def _load_config() -> dict:
    if getattr(sys, "frozen", False):
        config_path = Path(sys._MEIPASS) / "config.json"
    else:
        config_path = Path(__file__).resolve().parent.parent / "config.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Missing {config_path}.\n"
            "Copy config.template.json to config.json and fill in your values."
        )

    with open(config_path, "r") as f:
        return json.load(f)


_cfg = _load_config()

# AWS Cognito
COGNITO_REGION = _cfg["cognito_region"]
COGNITO_USER_POOL_ID = _cfg["cognito_user_pool_id"]
COGNITO_CLIENT_ID = _cfg["cognito_client_id"]

# API
API_BASE_URL = _cfg["api_base_url"]
WEBSITE_URL = _cfg["website_url"]
API_KEY = _cfg["api_key"]

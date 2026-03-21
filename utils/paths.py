"""Centralized path definitions for sandbox readiness.

Two categories:
- BUNDLE_DIR: Read-only bundled assets (models, default images, static docs).
  Resolves to sys._MEIPASS in PyInstaller, project root in dev.
- DATA_DIR: Writable user data (DB, media, clipboard, logs).
  Resolves to OS data directory in production, project root in dev.

In dev mode both resolve to the project root, so behaviour is unchanged.
"""

import os
import platform
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _app_bundle_dir() -> Path:
    """Read-only installation directory (bundled assets)."""
    if _is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _app_data_dir() -> Path:
    """Writable user data directory."""
    if _is_frozen():
        system = platform.system()
        if system == "Windows":
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            return base / "FoodieMoiety"
        elif system == "Darwin":
            return Path.home() / "Library" / "Application Support" / "FoodieMoiety"
        else:
            return Path.home() / ".foodiemoiety"
    # Dev mode — use project root
    return Path(__file__).resolve().parent.parent


BUNDLE_DIR = _app_bundle_dir()
DATA_DIR = _app_data_dir()

# ---------------------------------------------------------------------------
# Bundled assets (read-only)
# ---------------------------------------------------------------------------
WHISPER_MODEL = BUNDLE_DIR / "models" / "whisper" / "small.en"
VOSK_MODEL = BUNDLE_DIR / "models" / "vosk" / "small-en-us"
WAKEWORD_MODEL = BUNDLE_DIR / "models" / "wakeword" / "hey_foodie.onnx"
PIPER_ONNX = BUNDLE_DIR / "models" / "tts" / "en_US-hfc_female-medium.onnx"
DEFAULT_IMAGE = BUNDLE_DIR / "media" / "default.jpg"
CSAM_REPORT_DOC = BUNDLE_DIR / "csam_reporting_procedure.md"

# ---------------------------------------------------------------------------
# User data (writable)
# ---------------------------------------------------------------------------
DB_PATH = DATA_DIR / "foodie_moiety.db"
MEDIA_DIR = DATA_DIR / "media"
RECIPES_MEDIA = DATA_DIR / "media" / "recipes"
BOOKS_MEDIA = DATA_DIR / "media" / "books"
CLIPBOARD_PATH = DATA_DIR / "clipboard.json"
LOG_PATH = DATA_DIR / "foodie.log"
SETTINGS_PATH = DATA_DIR / "settings.ini"

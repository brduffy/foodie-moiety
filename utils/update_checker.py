"""Check for app updates by fetching a version manifest from S3/CDN.

Expected manifest format (JSON):
{
    "version": "1.2.0",
    "url": "https://example.com/FoodieMoiety.dmg",
    "notes": "Optional release notes"
}

The check is fully async (QNetworkAccessManager) and non-blocking.
If the network request fails or the manifest is malformed, the check
is silently ignored — the user is never interrupted by update errors.
"""

import json
import logging
import platform

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

log = logging.getLogger(__name__)

# Replace with your real CDN/S3 URL hosting latest_version.json
_UPDATE_URL = "https://foodiemoiety.com/desktop/latest_version.json"


class UpdateChecker(QObject):
    """Async update checker. Emits ``update_available`` if a newer version exists."""

    update_available = Signal(str, str, str)  # (new_version, download_url, notes)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self._current = _parse_version(current_version)
        self._nam = QNetworkAccessManager(self)

    def check(self):
        """Fire an async GET to the update manifest URL."""
        log.info("Checking for updates at %s", _UPDATE_URL)
        request = QNetworkRequest(QUrl(_UPDATE_URL))
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_reply(reply))

    def _on_reply(self, reply: QNetworkReply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                log.warning("Update check failed: %s", reply.errorString())
                return

            data = json.loads(bytes(reply.readAll()).decode("utf-8"))
            remote_version = data.get("version", "")
            remote_parsed = _parse_version(remote_version)
            if not remote_parsed:
                log.warning("Update check: invalid version in manifest: %r", remote_version)
                return

            if remote_parsed > self._current:
                # Pick platform-specific URL if provided, else fall back to generic
                system = platform.system()
                if system == "Darwin":
                    url = data.get("mac_url") or data.get("url", "")
                else:
                    url = data.get("win_url") or data.get("url", "")
                notes = data.get("notes", "")
                log.info("Update available: %s → %s", self._current, remote_version)
                self.update_available.emit(remote_version, url, notes)
            else:
                log.info("App is up to date (%s)", remote_version)
        except Exception as e:
            log.warning("Update check parse error: %s", e)
        finally:
            reply.deleteLater()


def _parse_version(v: str) -> tuple[int, ...] | None:
    """Parse '1.2.3' into (1, 2, 3). Returns None on failure."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except (ValueError, AttributeError):
        return None

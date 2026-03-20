"""Pushover push notification service for sending ingredient lists to phone."""

import json
import urllib.error
import urllib.parse
import urllib.request

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


def send_pushover_message(api_token: str, user_key: str, message: str,
                          title: str = "") -> tuple[bool, str, bool]:
    """Send a push notification via the Pushover API.

    Returns (success, detail, is_client_error).
    - success: True if the message was accepted.
    - detail: "" on success, error description on failure.
    - is_client_error: True for 4xx (bad credentials/input), False for
      network or server errors.
    """
    data = urllib.parse.urlencode({
        "token": api_token,
        "user": user_key,
        "message": message,
        "title": title,
        "html": "1",
    }).encode("utf-8")

    req = urllib.request.Request(PUSHOVER_API_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("status") == 1:
                return True, "", False
            return False, "; ".join(body.get("errors", ["Unknown error"])), True
    except urllib.error.HTTPError as e:
        is_client = 400 <= e.code < 500
        try:
            body = json.loads(e.read().decode("utf-8"))
            return False, "; ".join(body.get("errors", [str(e)])), is_client
        except Exception:
            return False, str(e), is_client
    except urllib.error.URLError as e:
        return False, f"Network error: {e.reason}", False
    except Exception as e:
        return False, str(e), False


def format_ingredient_list(ingredients: list[dict],
                           recipe_title: str) -> tuple[str, str]:
    """Format ingredient dicts into a Pushover message.

    Args:
        ingredients: List of dicts with keys: quantity, unit, item_name.
        recipe_title: Recipe title for the notification title.

    Returns:
        (title, body) ready for send_pushover_message.
    """
    lines = []
    for ing in ingredients:
        qty = ing.get("quantity", "").strip()
        unit = ing.get("unit", "").strip()
        name = ing.get("item_name", "").strip()
        if not name:
            continue
        parts = []
        if qty:
            parts.append(qty)
        if unit:
            parts.append(unit)
        parts.append(name)
        lines.append("• " + " ".join(parts))

    body = "\n\n".join(lines) if lines else "(no ingredients)"
    title = f"Shopping List: {recipe_title}" if recipe_title else "Shopping List"
    return title, body

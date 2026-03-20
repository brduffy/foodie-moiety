"""Regex-based intent parser for voice command recognition."""

from __future__ import annotations

import logging
import re

from services.intent_schema import IntentName, ParsedIntent

log = logging.getLogger(__name__)


_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
}


def _parse_number(s: str) -> float | None:
    """Parse a number from digits, decimal, or a spelled-out word."""
    s = s.strip().lower()
    try:
        return float(s)
    except ValueError:
        pass
    return _WORD_TO_NUM.get(s)


# Regex fragment matching a number (digits or spelled-out word)
_NUM_WORDS = "|".join(_WORD_TO_NUM.keys())
_NUM_PATTERN = rf"(?:\d+(?:\.\d+)?|{_NUM_WORDS})"


class FallbackParser:
    """Deterministic regex-based intent parser for recipe detail and video
    player voice commands.  Recipe list has no voice control.
    """

    # Order matters — more specific patterns must come before generic ones.
    _SCALE_WORDS = {"half": 0.5, "quarter": 0.25}

    _PATTERNS = [
        # --- Recipe scaling ---
        # "scale by 3", "scale by half", "scale by quarter"
        (
            re.compile(
                rf"scale\s+by\s+({_NUM_PATTERN}|half|quarter)",
                re.I,
            ),
            IntentName.SCALE_RECIPE,
            lambda m: {"factor": FallbackParser._SCALE_WORDS.get(m.group(1).lower())
                        or _parse_number(m.group(1)) or 1.0},
        ),
        # --- Scroll pane ---
        # "more ingredients", "more directions" → scroll down
        (
            re.compile(
                r"\bmore\s+(ingredients|directions)\b",
                re.I,
            ),
            IntentName.SCROLL_PANE,
            lambda m: {"pane": m.group(1).lower(), "direction": "down"},
        ),
        # "less ingredients", "less directions" → scroll up
        (
            re.compile(
                r"\bless\s+(ingredients|directions)\b",
                re.I,
            ),
            IntentName.SCROLL_PANE,
            lambda m: {"pane": m.group(1).lower(), "direction": "up"},
        ),
        # Bare "more" / "less" — pane inferred from current view context.
        (
            re.compile(r"^\s*more\s*$", re.I),
            IntentName.SCROLL_PANE,
            lambda m: {"direction": "down"},
        ),
        (
            re.compile(r"^\s*less\s*$", re.I),
            IntentName.SCROLL_PANE,
            lambda m: {"direction": "up"},
        ),
        # --- Change view ---
        # "[show] ingredients and directions"
        (
            re.compile(
                r"(?:show\s+)?ingredients\s+and\s+directions",
                re.I,
            ),
            IntentName.CHANGE_VIEW,
            lambda m: {"view_mode": "both"},
        ),
        # "show ingredients", "show directions", "show image", "show details"
        (
            re.compile(
                r"show\s+(ingredients|directions|image|details)",
                re.I,
            ),
            IntentName.CHANGE_VIEW,
            lambda m: {"view_mode": m.group(1).lower()},
        ),
        # Bare view name: "ingredients", "directions", "image", "details"
        # Full-string match prevents "next ingredients" from matching here.
        (
            re.compile(
                r"^\s*(ingredients|directions|image|details)\s*$",
                re.I,
            ),
            IntentName.CHANGE_VIEW,
            lambda m: {"view_mode": m.group(1).lower()},
        ),
        # --- Font size ---
        (re.compile(r"max\s+font", re.I), IntentName.ADJUST_FONT_SIZE,
         lambda m: {"direction": "max"}),
        (re.compile(r"min\s+font", re.I), IntentName.ADJUST_FONT_SIZE,
         lambda m: {"direction": "min"}),
        (re.compile(r"bigger\s+font", re.I), IntentName.ADJUST_FONT_SIZE,
         lambda m: {"direction": "increase"}),
        (re.compile(r"smaller\s+font", re.I), IntentName.ADJUST_FONT_SIZE,
         lambda m: {"direction": "decrease"}),
        # --- TTS control ---
        # "disable voice responses"
        (
            re.compile(r"disable\s+voice\s+responses", re.I),
            IntentName.DISABLE_TTS,
            lambda m: {},
        ),
        # "enable voice responses"
        (
            re.compile(r"enable\s+voice\s+responses", re.I),
            IntentName.ENABLE_TTS,
            lambda m: {},
        ),
        # --- Help ---
        # "commands", "voice commands", "show commands"
        (
            re.compile(
                r"^\s*(?:(?:voice\s+|show\s+)?commands)\s*$",
                re.I,
            ),
            IntentName.SHOW_HELP,
            lambda m: {},
        ),
        # --- Dismiss overlay ---
        # "close", "dismiss"
        (
            re.compile(r"^\s*(?:close|dismiss)\s*$", re.I),
            IntentName.DISMISS,
            lambda m: {},
        ),
        # --- Listening control ---
        # "pause listening"
        (
            re.compile(r"pause\s+listening", re.I),
            IntentName.PAUSE_LISTENING,
            lambda m: {},
        ),
        # "resume listening"
        (
            re.compile(r"resume\s+listening", re.I),
            IntentName.RESUME_LISTENING,
            lambda m: {},
        ),
        # --- Video control ---
        # "unmute" must be before "mute" to avoid partial match.
        (re.compile(r"^\s*unmute\s*$", re.I), IntentName.VIDEO_CONTROL,
         lambda m: {"video_action": "unmute"}),
        (re.compile(r"^\s*stop\s*$", re.I), IntentName.VIDEO_CONTROL,
         lambda m: {"video_action": "stop"}),
        (re.compile(r"^\s*play\s*$", re.I), IntentName.VIDEO_CONTROL,
         lambda m: {"video_action": "play"}),
        (re.compile(r"^\s*pause\s*$", re.I), IntentName.VIDEO_CONTROL,
         lambda m: {"video_action": "pause"}),
        (re.compile(r"skip\s+back", re.I), IntentName.VIDEO_CONTROL,
         lambda m: {"video_action": "skip_back"}),
        (re.compile(r"skip\s+forward", re.I), IntentName.VIDEO_CONTROL,
         lambda m: {"video_action": "skip_forward"}),
        (re.compile(r"^\s*mute\s*$", re.I), IntentName.VIDEO_CONTROL,
         lambda m: {"video_action": "mute"}),
        # "play video", "play"
        # Bare "play" also matches VIDEO_CONTROL above (which comes first),
        # so in practice only "play video" reaches here.
        (
            re.compile(r"^\s*play(?:\s+video)?\s*$", re.I),
            IntentName.PLAY_VIDEO,
            lambda m: {},
        ),
        # --- Step navigation ---
        # "next"
        (re.compile(r"^\s*next\s*$", re.I), IntentName.NAVIGATE_STEP,
         lambda m: {"target": "next"}),
        # "previous"
        (re.compile(r"^\s*previous\s*$", re.I), IntentName.NAVIGATE_STEP,
         lambda m: {"target": "previous"}),
        # "intro"
        (re.compile(r"^\s*intro\s*$", re.I), IntentName.NAVIGATE_STEP,
         lambda m: {"target": 0}),
        # "step 5", "step intro" — accepts digits, spelled-out numbers, or "intro"
        (
            re.compile(r"^\s*step\s+(\w+)\s*$", re.I),
            IntentName.NAVIGATE_STEP,
            lambda m: (
                {"target": 0} if m.group(1).lower() == "intro"
                else ({"target": int(n)} if (n := _parse_number(m.group(1))) is not None else None)
            ),
        ),
    ]

    def parse(self, user_text: str, active_view: str = "recipe_detail") -> ParsedIntent:
        """Attempt regex-based intent classification.

        Returns ParsedIntent with confidence=0.85 on match, or UNKNOWN
        with confidence=0.0 on no match.

        *active_view* is ``"recipe_detail"`` or ``"video_player"``.
        Recipe list has no voice commands.
        """
        text = user_text.strip()
        log.info("PARSE input=%r view=%s", text, active_view)
        for pattern, intent, entity_extractor in self._PATTERNS:
            match = pattern.search(text)
            if match:
                entities = entity_extractor(match)
                if entities is None:
                    log.debug("  pattern=%s matched but entity_extractor returned None, skipping",
                              pattern.pattern[:60])
                    continue
                log.info("PARSE MATCH intent=%s entities=%s pattern=%s matched=%r",
                         intent.value, entities, pattern.pattern[:80], match.group(0))
                return ParsedIntent(
                    intent=intent,
                    entities=entities,
                    confidence=0.85,
                    raw_text=user_text,
                )

        log.info("PARSE NO MATCH — returning UNKNOWN for %r", text)
        return ParsedIntent(
            intent=IntentName.UNKNOWN,
            entities={},
            confidence=0.0,
            raw_text=user_text,
        )

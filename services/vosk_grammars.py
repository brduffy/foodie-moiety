"""Vosk grammar definitions for voice command recognition.

Each grammar is a JSON array of exact phrases that Vosk will constrain
recognition to.  The special ``[unk]`` token catches any speech that
does not match a grammar phrase, preventing false-positive triggers on
background noise or unrelated speech.

Grammars are swapped at runtime via ``KaldiRecognizer.SetGrammar()``
when the active view changes.
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

_WORD_NUMS = [
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
]
_ORDINALS = ["first", "second", "third", "fourth", "fifth"]

_STEP_PHRASES = (
    [f"step {w}" for w in _WORD_NUMS]
    + [f"step {o}" for o in _ORDINALS]
    + ["step intro"]
)

_SCALE_PHRASES = (
    ["scale by half", "scale by quarter"]
    + [f"scale by {w}" for w in _WORD_NUMS]
    + [f"scale by {o}" for o in _ORDINALS]
)

_GLOBAL_COMMANDS = [
    "pause listening",
    "resume listening",
    "disable voice responses",
    "enable voice responses",
]

_HELP_COMMANDS = [
    "commands",
    "voice commands",
    "show commands",
    "close",
    "dismiss",
]


def _build_grammar(phrases: list[str]) -> str:
    """Build a Vosk grammar JSON string from a list of phrases."""
    return json.dumps(phrases + ["[unk]"])


# ---------------------------------------------------------------------------
# Per-view grammars
# ---------------------------------------------------------------------------

RECIPE_DETAIL_GRAMMAR = _build_grammar(
    # Navigation
    ["next", "previous", "intro"]
    + _STEP_PHRASES
    # Scrolling
    + ["more", "less",
       "more ingredients", "more directions",
       "less ingredients", "less directions"]
    # View switching
    + ["ingredients and directions",
       "show ingredients", "show directions", "show image", "show details",
       "ingredients", "directions", "image", "details"]
    # Scaling
    + _SCALE_PHRASES
    # Font
    + ["bigger font", "smaller font", "max font", "min font"]
    # Video
    + ["play video"]
    # Help & global
    + _HELP_COMMANDS
    + _GLOBAL_COMMANDS
)

VIDEO_PLAYER_GRAMMAR = _build_grammar(
    # Playback
    ["play", "pause", "stop"]
    # Volume
    + ["mute", "unmute"]
    # Seek
    + ["skip forward", "skip back"]
    # Navigation
    + ["next", "previous"]
    + _STEP_PHRASES
    # Help & global
    + _HELP_COMMANDS
    + _GLOBAL_COMMANDS
)

# Views with no voice commands get a minimal grammar (global only).
_GLOBAL_ONLY_GRAMMAR = _build_grammar(_HELP_COMMANDS + _GLOBAL_COMMANDS)

VIEW_GRAMMARS: dict[str, str] = {
    "recipe_detail": RECIPE_DETAIL_GRAMMAR,
    "video_player": VIDEO_PLAYER_GRAMMAR,
    "recipe_list": _GLOBAL_ONLY_GRAMMAR,
    "book_view": _GLOBAL_ONLY_GRAMMAR,
}

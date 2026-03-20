"""Intent classification schema — the contract between LLM and workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IntentName(str, Enum):
    """All recognized intents. Extend this enum to add new intents."""

    NAVIGATE_STEP = "navigate_step"

    SCALE_RECIPE = "scale_recipe"
    ADJUST_FONT_SIZE = "adjust_font_size"
    CHANGE_VIEW = "change_view"
    PLAY_VIDEO = "play_video"
    VIDEO_CONTROL = "video_control"
    SCROLL_PANE = "scroll_pane"
    PAUSE_LISTENING = "pause_listening"
    RESUME_LISTENING = "resume_listening"
    DISABLE_TTS = "disable_tts"
    ENABLE_TTS = "enable_tts"
    SHOW_HELP = "show_help"
    DISMISS = "dismiss"
    UNKNOWN = "unknown"


@dataclass
class ParsedIntent:
    """Result of intent classification from the LLM or fallback parser.

    Attributes:
        intent: The classified intent name.
        entities: Extracted entities as a flat dict. Keys depend on intent:
            - navigate_step: {"target": "next" | "previous" | int}

            - scale_recipe: {"factor": float}
            - adjust_font_size: {"direction": "increase" | "decrease" | "max" | "min"}
            - change_view: {"view_mode": "both" | "ingredients" | "directions" | "image" | "tags"}
            - scroll_pane: {"pane": "ingredients" | "directions", "direction": "down" | "up"}
            - video_control: {"video_action": str}
        confidence: 0.0 to 1.0, how confident the classifier is.
        raw_text: The original user input.
    """

    intent: IntentName
    entities: dict = field(default_factory=dict)
    confidence: float = 0.0
    raw_text: str = ""


@dataclass
class WorkflowResult:
    """Result returned by a workflow function to be displayed to the user.

    Attributes:
        success: Whether the workflow completed successfully.
        message: Human-readable message to display in the response panel.
        data: Optional structured data for the UI (e.g., action instructions).
    """

    success: bool
    message: str
    data: dict | None = None

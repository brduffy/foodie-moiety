"""Starter workflow implementations."""

from __future__ import annotations

import logging

from services.app_context import AppContext

log = logging.getLogger(__name__)
from services.intent_schema import IntentName, ParsedIntent, WorkflowResult
from services.workflow_registry import WorkflowRegistry


def register_all_workflows(registry: WorkflowRegistry) -> None:
    """Register all workflows with the given registry."""
    registry.register(IntentName.NAVIGATE_STEP, navigate_step_workflow)

    registry.register(IntentName.SCALE_RECIPE, scale_recipe_workflow)
    registry.register(IntentName.ADJUST_FONT_SIZE, adjust_font_size_workflow)
    registry.register(IntentName.CHANGE_VIEW, change_view_workflow)
    registry.register(IntentName.PLAY_VIDEO, play_video_workflow)
    registry.register(IntentName.VIDEO_CONTROL, video_control_workflow)
    registry.register(IntentName.SCROLL_PANE, scroll_pane_workflow)
    registry.register(IntentName.PAUSE_LISTENING, pause_listening_workflow)
    registry.register(IntentName.RESUME_LISTENING, resume_listening_workflow)
    registry.register(IntentName.DISABLE_TTS, disable_tts_workflow)
    registry.register(IntentName.ENABLE_TTS, enable_tts_workflow)
    registry.register(IntentName.SHOW_HELP, show_help_workflow)
    registry.register(IntentName.DISMISS, dismiss_workflow)


# ------------------------------------------------------------------
# Workflow: Step Navigation
# ------------------------------------------------------------------


def navigate_step_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'go to step 5', 'next step', 'previous step'."""
    if ctx.active_view not in ("recipe_detail", "video_player"):
        return WorkflowResult(
            success=False,
            message="Open a recipe first to navigate steps.",
        )

    target = intent.entities.get("target")
    current = ctx.current_step_index
    total = ctx.total_steps

    if target == "next":
        new_index = current + 1
    elif target == "previous":
        new_index = current - 1
    elif isinstance(target, (int, float)):
        # Step 0 is the intro; user-facing steps are 1..N matching their index.
        new_index = int(target)
    else:
        return WorkflowResult(success=False, message=f"Invalid step target: {target}")

    if new_index < 0:
        return WorkflowResult(success=False, message="Already at the first step.")
    if new_index >= total:
        return WorkflowResult(success=False, message="Already at the last step.")

    return WorkflowResult(
        success=True,
        message=f"Showing step {new_index}." if new_index > 0 else "Showing intro.",
        data={"action": "navigate_step", "step_index": new_index},
    )


# ------------------------------------------------------------------
# Workflow: Scale Recipe
# ------------------------------------------------------------------

def _format_quantity(value: float) -> str:
    """Format a number nicely — drop trailing zeros, cap at 4 decimals."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


_SCALE_WORDS: dict[str, float] = {
    "double": 2.0, "doubled": 2.0,
    "triple": 3.0, "tripled": 3.0,
    "quadruple": 4.0, "quadrupled": 4.0,
    "halve": 0.5, "halved": 0.5, "half": 0.5,
}


def scale_recipe_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'double this recipe', 'halve the ingredients', 'scale by 3'."""
    if ctx.recipe_data is None:
        return WorkflowResult(
            success=False,
            message="Open a recipe first to scale ingredients.",
        )

    rd = ctx.recipe_data
    factor = intent.entities.get("factor", 2.0)

    # Resolve word-based factors (in case LLM passes the word instead of a number)
    if isinstance(factor, str):
        factor = _SCALE_WORDS.get(factor.lower(), None)
        if factor is None:
            try:
                factor = float(intent.entities.get("factor", 2.0))
            except (ValueError, TypeError):
                return WorkflowResult(
                    success=False,
                    message="I couldn't determine the scaling factor.",
                )

    factor = float(factor)
    if factor <= 0:
        return WorkflowResult(
            success=False,
            message="The scaling factor must be a positive number.",
        )

    ingredients = rd.intro_ingredients
    if not ingredients:
        return WorkflowResult(
            success=False,
            message="This recipe has no ingredients to scale.",
        )

    # Format factor description
    if factor == 2.0:
        label = "Doubled"
    elif factor == 3.0:
        label = "Tripled"
    elif factor == 0.5:
        label = "Halved"
    elif factor == int(factor):
        label = f"Scaled ×{int(factor)}"
    else:
        label = f"Scaled ×{_format_quantity(factor)}"

    lines = [f"{label} ingredients for '{rd.title}':"]
    for ing in ingredients:
        scaled_qty = ing.quantity * factor
        lines.append(f"  {_format_quantity(scaled_qty)} {ing.unit} {ing.item_name}")

    return WorkflowResult(
        success=True,
        message="\n".join(lines),
        data={"action": "scale_recipe"},
    )


# ------------------------------------------------------------------
# Workflow: Adjust Font Size
# ------------------------------------------------------------------


def adjust_font_size_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'increase font size', 'make text bigger', 'max font', etc."""
    direction = intent.entities.get("direction", "increase")

    if direction == "max":
        delta = 10  # Clamped to 24 by widgets
        message = "Font size set to maximum."
    elif direction == "min":
        delta = -10  # Clamped to 14 by widgets
        message = "Font size set to minimum."
    elif direction == "increase":
        delta = 1
        message = "Font size increased."
    else:
        delta = -1
        message = "Font size decreased."

    return WorkflowResult(
        success=True,
        message=message,
        data={"action": "adjust_font_size", "delta": delta},
    )


# ------------------------------------------------------------------
# Workflow: Change View
# ------------------------------------------------------------------


_VIEW_LABELS = {
    "both": "ingredients and directions",
    "ingredients": "ingredients",
    "directions": "directions",
    "image": "image",
    "tags": "tags",
    "details": "details",
}


def change_view_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'show ingredients', 'view directions', etc."""
    view_mode = intent.entities.get("view_mode", "both")

    return WorkflowResult(
        success=True,
        message=f"Showing {_VIEW_LABELS.get(view_mode, view_mode)}.",
        data={"action": "change_view", "view_mode": view_mode},
    )


# ------------------------------------------------------------------
# Workflow: Scroll Pane
# ------------------------------------------------------------------


def scroll_pane_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'more ingredients', 'less directions', bare 'more'/'less', etc."""
    if ctx.active_view not in ("recipe_detail", "video_player"):
        return WorkflowResult(
            success=False,
            message="Open a recipe first to scroll.",
        )

    pane = intent.entities.get("pane")
    direction = intent.entities.get("direction", "down")

    # Resolve bare "more"/"less" from current layout mode
    if pane is None:
        if ctx.layout_mode == "ingredients":
            pane = "ingredients"
        else:
            # "directions", "both", or any other mode → default to directions
            pane = "directions"

    return WorkflowResult(
        success=True,
        message="",
        data={"action": "scroll_pane", "pane": pane, "direction": direction},
    )


# ------------------------------------------------------------------
# Workflow: Play Video
# ------------------------------------------------------------------


def play_video_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'play video', 'show video', 'video'."""
    if ctx.active_view != "recipe_detail":
        return WorkflowResult(
            success=False,
            message="Open a recipe first to play a video.",
        )

    return WorkflowResult(
        success=True,
        message="Playing video.",
        data={"action": "play_video"},
    )


# ------------------------------------------------------------------
# Workflow: Video Control (play/pause/stop/skip in video player)
# ------------------------------------------------------------------


_VIDEO_MESSAGES = {
    "stop": "Stopping video.",
    "play": "Resuming playback.",
    "resume": "Resuming playback.",
    "pause": "Video paused.",
    "skip_back": "Skipping back.",
    "skip_forward": "Skipping forward.",
    "mute": "Video muted.",
    "unmute": "Video unmuted.",
}


def video_control_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle stop, play, pause, skip back, skip forward in video player.

    Context-aware: when the video player is active, controls playback.
    When not in the video player, only "play" falls through to play_video.
    """
    video_action = intent.entities.get("video_action", "play")
    log.info("VIDEO_CONTROL action=%s view=%s raw=%r",
             video_action, ctx.active_view, intent.raw_text)

    if ctx.active_view == "video_player":
        log.info("VIDEO_CONTROL → dispatching %s to video player", video_action)
        return WorkflowResult(
            success=True,
            message=_VIDEO_MESSAGES.get(video_action, "OK."),
            data={"action": "video_control", "video_action": video_action},
        )

    # Not in video player — "play" falls through to play_video,
    # everything else is silently ignored (no TTS, no error message).
    if video_action == "play":
        if ctx.active_view == "recipe_detail":
            log.info("VIDEO_CONTROL → play outside video player, falling through to play_video")
            return WorkflowResult(
                success=True,
                message="Playing video.",
                data={"action": "play_video"},
            )
        return WorkflowResult(success=False, message="")

    log.info("VIDEO_CONTROL → %s ignored outside video player (raw=%r)",
             video_action, intent.raw_text)
    return WorkflowResult(success=False, message="")


# ------------------------------------------------------------------
# Workflow: Pause / Resume Listening
# ------------------------------------------------------------------


def pause_listening_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'pause listening', 'mute'."""
    return WorkflowResult(
        success=True,
        message="Listening paused.",
        data={"action": "pause_listening"},
    )


def resume_listening_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'resume listening', 'unmute'."""
    return WorkflowResult(
        success=True,
        message="Listening resumed.",
        data={"action": "resume_listening"},
    )


# ------------------------------------------------------------------
# Workflow: Disable / Enable TTS
# ------------------------------------------------------------------


def disable_tts_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'cancel voice responses', 'turn off speech'."""
    return WorkflowResult(
        success=True,
        message="Voice responses disabled.",
        data={"action": "disable_tts"},
    )


def enable_tts_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'allow voice responses', 'turn on speech'."""
    return WorkflowResult(
        success=True,
        message="Voice responses enabled.",
        data={"action": "enable_tts"},
    )


# ------------------------------------------------------------------
# Workflow: Show Help
# ------------------------------------------------------------------


def show_help_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'help', 'voice commands'."""
    return WorkflowResult(
        success=True,
        message="",
        data={"action": "show_help"},
    )


def dismiss_workflow(intent: ParsedIntent, ctx: AppContext) -> WorkflowResult:
    """Handle 'close', 'dismiss' — hides any visible overlay panel."""
    return WorkflowResult(
        success=True,
        message="",
        data={"action": "dismiss"},
    )



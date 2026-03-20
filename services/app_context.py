"""Application context — read-only state snapshot for workflows."""

from __future__ import annotations

from dataclasses import dataclass, field

from models.recipe_data import RecipeData


@dataclass
class AppContext:
    """Snapshot of current application state, passed to every workflow.

    Built by MainWindow each time a command is processed. Workflows
    read from this; they never write to it. Actions that change UI state
    are returned as WorkflowResult.data for MainWindow to execute.

    Attributes:
        recipe_data: The currently loaded RecipeData, or None if on list view.
        current_step_index: 0-based index of the currently displayed step.
        total_steps: Total number of navigable steps (including intro at 0).
        active_view: "recipe_list" | "recipe_detail" | "video_player"
        layout_mode: "both" | "ingredients" | "directions" | "image" | "tags" | "details"
        visible_recipes: [(id, title, type), ...] in card display order.
            Index+1 matches the number badge on each card.
            *type* is ``"book"`` or ``"recipe"``.
    """

    recipe_data: RecipeData | None
    current_step_index: int
    total_steps: int
    active_view: str
    layout_mode: str = "both"
    visible_recipes: list[tuple[int, str, str]] = field(default_factory=list)

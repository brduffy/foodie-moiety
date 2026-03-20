"""In-memory data structures for recipe editing and display."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field


@dataclass
class SpeedRange:
    """A time range on a video timeline where playback should speed up."""
    start_ms: int
    end_ms: int
    playback_rate: float = 4.0


@dataclass
class IngredientData:
    ingredient_id: int | None  # None for new/unsaved ingredients
    item_name: str
    quantity: float
    unit: str
    amount_override: str | None = None  # e.g., "remaining half", "chilled"


@dataclass
class StepData:
    step_id: int | None  # None for new/unsaved steps
    step_number: int
    instruction: str  # HTML content from RichTextEditor
    image_path: str | None = None
    is_timer_required: bool = False
    timer_duration_sec: int = 0
    is_critical: bool = False
    video_path: str | None = None
    ingredients: list[IngredientData] = field(default_factory=list)


@dataclass
class RecipeData:
    recipe_id: int | None  # None for new recipes
    title: str
    description: str
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    cuisine_type: str | None = None
    difficulty: str | None = None
    main_image_path: str | None = None
    intro_video_path: str | None = None  # Video for intro step (chef's introduction)
    producer: str = ""  # Optional attribution (e.g. "Chef John")
    community_origin_id: str | None = None       # community recipeId this was downloaded from
    community_origin_uploader: str | None = None  # Cognito userId of the original uploader
    steps: list[StepData] = field(default_factory=list)
    intro_ingredients: list[IngredientData] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    dirty: bool = False
    content_type: str = "recipe"  # "recipe" or "article"
    is_moiety: bool = False

    def aggregate_ingredients(self) -> list[IngredientData]:
        """Compute the intro step's aggregated ingredient list.

        Iterates all steps, groups by exact item_name match, and sums
        quantities. Returns a new list of IngredientData (no IDs, no
        amount_override) suitable for display on the intro step.

        Note: The intro step is a virtual UI concept — it does not exist
        in rd.steps. All entries in rd.steps are real cooking steps.
        """
        totals: dict[str, IngredientData] = {}
        for step in self.steps:
            for ing in step.ingredients:
                key = ing.item_name
                if key in totals:
                    totals[key].quantity += ing.quantity
                else:
                    totals[key] = IngredientData(
                        ingredient_id=ing.ingredient_id,
                        item_name=ing.item_name,
                        quantity=ing.quantity,
                        unit=ing.unit,
                    )
        return list(totals.values())


@dataclass
class BookCategoryData:
    """A category (chapter) within a book's table of contents."""
    category_id: int | None  # None for new/unsaved categories
    name: str
    display_order: int = 0
    recipes: list[dict] = field(default_factory=list)
    # Each dict: {"recipe_id": int, "title": str, "book_description": str | None}


@dataclass
class BookData:
    """A curated book (digital cookbook) containing categorized recipes."""
    book_id: int | None  # None for new books
    title: str
    description: str  # HTML content, same format as recipe descriptions
    producer: str = ""
    community_origin_id: str | None = None
    community_origin_uploader: str | None = None
    community_price_type: str | None = None
    cover_image_path: str | None = None
    intro_video_path: str | None = None
    is_book_of_moiety: bool = False
    categories: list[BookCategoryData] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    dirty: bool = False


def build_clipboard_recipe(steps: list[StepData]) -> RecipeData:
    """Create a clipboard RecipeData from a list of steps (deep copied)."""
    copied_steps = []
    for i, step in enumerate(steps, start=1):
        s = copy.deepcopy(step)
        s.step_id = None
        s.step_number = i
        for ing in s.ingredients:
            ing.ingredient_id = None
        copied_steps.append(s)
    return RecipeData(
        recipe_id=None,
        title="Clipboard",
        description=f"{len(copied_steps)} step(s) on clipboard",
        steps=copied_steps,
    )

"""JSON persistence for the step clipboard."""

import json
import os
from dataclasses import asdict

from models.recipe_data import IngredientData, RecipeData, StepData
from utils.paths import CLIPBOARD_PATH

_CLIPBOARD_PATH = str(CLIPBOARD_PATH)


def save_clipboard(recipe_data: RecipeData) -> None:
    """Serialize clipboard RecipeData to JSON on disk."""
    try:
        d = asdict(recipe_data)
        d.pop("dirty", None)
        with open(_CLIPBOARD_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def clear_clipboard() -> None:
    """Delete the clipboard JSON file from disk."""
    try:
        if os.path.isfile(_CLIPBOARD_PATH):
            os.remove(_CLIPBOARD_PATH)
    except OSError:
        pass


def load_clipboard() -> RecipeData | None:
    """Deserialize clipboard RecipeData from JSON on disk.

    Returns None if the file is missing or corrupt.
    """
    if not os.path.isfile(_CLIPBOARD_PATH):
        return None
    try:
        with open(_CLIPBOARD_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        steps = []
        for sd in d.pop("steps", []):
            ingredients = [IngredientData(**ing) for ing in sd.pop("ingredients", [])]
            steps.append(StepData(**sd, ingredients=ingredients))
        d.pop("dirty", None)
        return RecipeData(**d, steps=steps)
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        return None

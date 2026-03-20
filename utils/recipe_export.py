"""Export and import recipes as zip archives for sharing between users."""

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import asdict, fields

from models.recipe_data import IngredientData, RecipeData, StepData

# Known field names for each dataclass — used to filter unknown keys from JSON
_RECIPE_FIELDS = {f.name for f in fields(RecipeData)}
_STEP_FIELDS = {f.name for f in fields(StepData)}
_INGREDIENT_FIELDS = {f.name for f in fields(IngredientData)}
from utils.database import (
    get_canonical_tags, insert_recipe_data, load_recipe_data, load_speed_ranges,
    mark_recipe_viewed, save_recipe_data, save_speed_range,
)

from utils.paths import DATA_DIR, DEFAULT_IMAGE

_PROJECT_ROOT = str(DATA_DIR)
_DEFAULT_IMAGE = os.path.join("media", "default.jpg")


def peek_recipe_zip(zip_path):
    """Read title and producer from a recipe zip without importing.

    Returns a dict with 'title' and 'producer', or raises ValueError.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if "article.json" in names:
            json_name = "article.json"
        elif "recipe.json" in names:
            json_name = "recipe.json"
        else:
            raise ValueError("Zip archive does not contain recipe.json or article.json")
        with zf.open(json_name) as f:
            d = json.load(f)
    return {"title": d.get("title", ""), "producer": d.get("producer", "")}


def export_recipe_to_zip(recipe_id, zip_path):
    """Export a recipe and its media files to a zip archive.

    Args:
        recipe_id: Database ID of the recipe to export.
        zip_path: Destination path for the zip file.

    Raises:
        ValueError: If the recipe_id doesn't exist.
    """
    rd = load_recipe_data(recipe_id)
    if rd is None:
        raise ValueError(f"Recipe with id {recipe_id} not found")

    d = asdict(rd)
    d.pop("dirty", None)
    canonical = get_canonical_tags()
    d["tags"] = [t for t in (d.get("tags") or []) if t in canonical]

    # Clear all database IDs so the export is portable
    d["recipe_id"] = None
    for step in d.get("steps", []):
        step["step_id"] = None
        for ing in step.get("ingredients", []):
            ing["ingredient_id"] = None
    for ing in d.get("intro_ingredients", []):
        ing["ingredient_id"] = None

    # Collect media files and remap paths to zip-internal paths
    media_map = {}  # zip_internal_path -> absolute_path
    path_remap = {}  # original_rel_path -> zip_internal_path

    def _remap(rel_path):
        if not rel_path or rel_path == _DEFAULT_IMAGE:
            return None
        abs_path = os.path.join(_PROJECT_ROOT, rel_path)
        if not os.path.isfile(abs_path):
            return None
        filename = os.path.basename(rel_path)
        zip_internal = f"media/{filename}"
        media_map[zip_internal] = abs_path
        path_remap[rel_path] = zip_internal
        return zip_internal

    d["main_image_path"] = _remap(d.get("main_image_path"))
    d["intro_video_path"] = _remap(d.get("intro_video_path"))
    for step in d.get("steps", []):
        step["image_path"] = _remap(step.get("image_path"))
        step["video_path"] = _remap(step.get("video_path"))

    # Export video speed ranges for all remapped video paths.
    # Speed ranges are stored with absolute paths in the DB, so convert
    # the relative RecipeData paths to absolute before querying.
    speed_ranges = []
    for orig_path, zip_path_internal in path_remap.items():
        abs_orig = os.path.join(_PROJECT_ROOT, orig_path)
        for start_ms, end_ms, rate in load_speed_ranges(abs_orig):
            speed_ranges.append({
                "video_path": zip_path_internal,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "playback_rate": rate,
            })
    if speed_ranges:
        d["video_speed_ranges"] = speed_ranges

    with zipfile.ZipFile(zip_path, "w") as zf:
        # Compress JSON (small, text-based)
        json_name = "article.json" if rd.content_type == "article" else "recipe.json"
        zf.writestr(json_name, json.dumps(d, ensure_ascii=False, indent=2),
                     compress_type=zipfile.ZIP_DEFLATED)
        # Store media files uncompressed — they're already compressed (JPEG, MP4, MKV)
        for zip_internal, abs_path in media_map.items():
            zf.write(abs_path, zip_internal, compress_type=zipfile.ZIP_STORED)


def import_recipe_from_zip(zip_path, community_origin_id=None,
                           community_origin_uploader=None):
    """Import a recipe from a zip archive into the database.

    Args:
        zip_path: Path to the zip file to import.
        community_origin_id: Optional community recipeId this was downloaded from.
        community_origin_uploader: Optional Cognito userId of the original uploader.

    Returns:
        The newly assigned recipe_id.

    Raises:
        ValueError: If the zip is malformed or missing recipe.json.
    """
    tmp_dir = tempfile.mkdtemp(prefix="foodie_import_")
    dest_dir = os.path.join(_PROJECT_ROOT, "media", "recipes", "new")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    raise ValueError(f"Unsafe path in zip: {info.filename}")
            zf.extractall(tmp_dir)

        json_path = os.path.join(tmp_dir, "article.json")
        is_article = os.path.isfile(json_path)
        if not is_article:
            json_path = os.path.join(tmp_dir, "recipe.json")
        if not os.path.isfile(json_path):
            raise ValueError("Zip archive does not contain recipe.json or article.json")

        with open(json_path, "r", encoding="utf-8") as f:
            d = json.load(f)

        # Reconstruct RecipeData (filter unknown keys so server-added fields don't crash)
        steps = []
        for sd in d.pop("steps", []):
            ingredients = [
                IngredientData(**{k: v for k, v in ing.items() if k in _INGREDIENT_FIELDS})
                for ing in sd.pop("ingredients", [])
            ]
            sd = {k: v for k, v in sd.items() if k in _STEP_FIELDS}
            steps.append(StepData(**sd, ingredients=ingredients))

        intro_ingredients = [
            IngredientData(**{k: v for k, v in ing.items() if k in _INGREDIENT_FIELDS})
            for ing in d.pop("intro_ingredients", [])
        ]
        speed_ranges_data = d.pop("video_speed_ranges", [])
        d.pop("dirty", None)
        d.pop("tags", None)  # Tags are local — don't import from other users
        d = {k: v for k, v in d.items() if k in _RECIPE_FIELDS}
        rd = RecipeData(**d, steps=steps, intro_ingredients=intro_ingredients)

        # Set content type from zip filename
        if is_article:
            rd.content_type = "article"

        # Set community origin (explicit params override JSON values)
        if community_origin_id is not None:
            rd.community_origin_id = community_origin_id
        if community_origin_uploader is not None:
            rd.community_origin_uploader = community_origin_uploader

        # Ensure IDs are None for insert
        rd.recipe_id = None
        for step in rd.steps:
            step.step_id = None
            for ing in step.ingredients:
                ing.ingredient_id = None
        for ing in rd.intro_ingredients:
            ing.ingredient_id = None

        # Copy media files from extracted zip to media/recipes/new/ with UUID filenames
        os.makedirs(dest_dir, exist_ok=True)
        media_path_remap = {}  # zip_internal -> new_rel_path

        def _copy_media(zip_internal):
            if not zip_internal:
                return None
            src = os.path.join(tmp_dir, zip_internal)
            if not os.path.isfile(src):
                return None
            ext = os.path.splitext(zip_internal)[1].lower()
            # Downscale images to 1920x1080 max, save as JPEG quality 85
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                from PySide6.QtCore import Qt
                from PySide6.QtGui import QImage
                img = QImage(src)
                if not img.isNull():
                    max_w, max_h = 1920, 1080
                    if img.width() > max_w or img.height() > max_h:
                        img = img.scaled(
                            max_w, max_h,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation,
                        )
                    new_name = f"{uuid.uuid4().hex}.jpg"
                    dst = os.path.join(dest_dir, new_name)
                    img.save(dst, "JPEG", 85)
                    new_path = f"media/recipes/new/{new_name}"
                    media_path_remap[zip_internal] = new_path
                    return new_path
            new_name = f"{uuid.uuid4().hex}{ext}"
            dst = os.path.join(dest_dir, new_name)
            shutil.copy2(src, dst)
            new_path = f"media/recipes/new/{new_name}"
            media_path_remap[zip_internal] = new_path
            return new_path

        rd.main_image_path = _copy_media(rd.main_image_path)
        rd.intro_video_path = _copy_media(rd.intro_video_path)
        for step in rd.steps:
            step.image_path = _copy_media(step.image_path)
            step.video_path = _copy_media(step.video_path)

        if not rd.main_image_path:
            rd.main_image_path = _DEFAULT_IMAGE

        # Insert into database
        new_id = insert_recipe_data(rd)

        # Rename media folder from "new" to actual recipe ID and update paths
        final_dir = os.path.join(_PROJECT_ROOT, "media", "recipes", str(new_id))
        if os.path.isdir(dest_dir) and os.listdir(dest_dir):
            if os.path.exists(final_dir):
                for fname in os.listdir(dest_dir):
                    shutil.move(os.path.join(dest_dir, fname), final_dir)
                os.rmdir(dest_dir)
            else:
                shutil.move(dest_dir, final_dir)

            old_prefix = "media/recipes/new/"
            new_prefix = f"media/recipes/{new_id}/"
            if rd.main_image_path and rd.main_image_path.startswith(old_prefix):
                rd.main_image_path = rd.main_image_path.replace(old_prefix, new_prefix, 1)
            if rd.intro_video_path and rd.intro_video_path.startswith(old_prefix):
                rd.intro_video_path = rd.intro_video_path.replace(old_prefix, new_prefix, 1)
            for step in rd.steps:
                if step.image_path and step.image_path.startswith(old_prefix):
                    step.image_path = step.image_path.replace(old_prefix, new_prefix, 1)
                if step.video_path and step.video_path.startswith(old_prefix):
                    step.video_path = step.video_path.replace(old_prefix, new_prefix, 1)

            rd.recipe_id = new_id
            save_recipe_data(rd)

        # Import video speed ranges with remapped paths.
        # Speed ranges are stored with absolute paths in the DB (the video
        # player resolves relative paths to absolute before saving ranges).
        if speed_ranges_data:
            old_prefix = "media/recipes/new/"
            new_prefix = f"media/recipes/{new_id}/"
            for sr in speed_ranges_data:
                zip_video_path = sr.get("video_path")
                new_video_path = media_path_remap.get(zip_video_path)
                if new_video_path:
                    if new_video_path.startswith(old_prefix):
                        new_video_path = new_video_path.replace(old_prefix, new_prefix, 1)
                    abs_video_path = os.path.join(_PROJECT_ROOT, new_video_path)
                    save_speed_range(
                        abs_video_path,
                        sr["start_ms"],
                        sr["end_ms"],
                        sr.get("playback_rate", 4.0),
                    )

        mark_recipe_viewed(new_id)
        return new_id

    except Exception:
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

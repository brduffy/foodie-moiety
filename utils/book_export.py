"""Export and import books as zip archives for sharing between users."""

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import asdict, fields

from models.recipe_data import (
    BookCategoryData,
    BookData,
    IngredientData,
    RecipeData,
    StepData,
)
from utils.database import (
    get_canonical_tags,
    insert_book_data,
    insert_recipe_data,
    load_book_data,
    load_recipe_data,
    load_speed_ranges,
    save_book_data,
    save_recipe_data,
    save_speed_range,
)

from utils.paths import DATA_DIR, DEFAULT_IMAGE

_PROJECT_ROOT = str(DATA_DIR)
_DEFAULT_IMAGE = os.path.join("media", "default.jpg")


def peek_book_zip(zip_path):
    """Read title and producer from a book zip without importing.

    Returns a dict with 'title' and 'producer', or raises ValueError.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        if "book.json" not in zf.namelist():
            raise ValueError("Zip archive does not contain book.json")
        with zf.open("book.json") as f:
            d = json.load(f)
    return {"title": d.get("title", ""), "producer": d.get("producer", "")}


def export_book_to_zip(book_id, zip_path):
    """Export a book and all its recipes/media to a zip archive.

    The export is fully portable — each recipe is embedded as complete data
    so the book can be imported on a system with no pre-existing recipes.

    Args:
        book_id: Database ID of the book to export.
        zip_path: Destination path for the zip file.

    Raises:
        ValueError: If the book_id doesn't exist.
    """
    bd = load_book_data(book_id)
    if bd is None:
        raise ValueError(f"Book with id {book_id} not found")

    # Collect media files and remap paths to zip-internal paths
    media_map = {}  # zip_internal_path -> absolute_path

    def _remap(rel_path):
        """Remap a relative media path to a UUID-prefixed zip-internal path."""
        if not rel_path or rel_path == _DEFAULT_IMAGE:
            return None
        abs_path = os.path.join(_PROJECT_ROOT, rel_path)
        if not os.path.isfile(abs_path):
            return None
        ext = os.path.splitext(rel_path)[1].lower()
        unique_name = f"{uuid.uuid4().hex}{ext}"
        zip_internal = f"media/{unique_name}"
        media_map[zip_internal] = abs_path
        return zip_internal

    canonical = get_canonical_tags()

    # Build book dict
    book_dict = {
        "title": bd.title,
        "description": bd.description,
        "producer": bd.producer,
        "cover_image_path": _remap(bd.cover_image_path),
        "intro_video_path": _remap(bd.intro_video_path),
        "is_book_of_moiety": bd.is_book_of_moiety,
        "tags": [t for t in (bd.tags or []) if t in canonical],
        "categories": [],
    }

    for cat in bd.categories:
        cat_dict = {
            "name": cat.name,
            "display_order": cat.display_order,
            "recipes": [],
        }
        for recipe_ref in cat.recipes:
            recipe_id = recipe_ref.get("recipe_id")
            book_description = recipe_ref.get("book_description")

            # Load full recipe data
            rd = load_recipe_data(recipe_id) if recipe_id else None
            if rd is None:
                # Recipe no longer exists — skip it
                continue

            # Serialize recipe (same approach as recipe_export.py)
            d = asdict(rd)
            d.pop("dirty", None)
            d["tags"] = [t for t in (d.get("tags") or []) if t in canonical]
            d["recipe_id"] = None
            for step in d.get("steps", []):
                step["step_id"] = None
                for ing in step.get("ingredients", []):
                    ing["ingredient_id"] = None
            for ing in d.get("intro_ingredients", []):
                ing["ingredient_id"] = None

            # Remap recipe media paths
            # Track original paths for speed range remapping
            path_remap = {}  # original_rel_path -> zip_internal_path

            def _remap_recipe(rel_path):
                zp = _remap(rel_path)
                if zp and rel_path:
                    path_remap[rel_path] = zp
                return zp

            d["main_image_path"] = _remap_recipe(d.get("main_image_path"))
            d["intro_video_path"] = _remap_recipe(d.get("intro_video_path"))
            for step in d.get("steps", []):
                step["image_path"] = _remap_recipe(step.get("image_path"))
                step["video_path"] = _remap_recipe(step.get("video_path"))

            # Export video speed ranges
            speed_ranges = []
            for orig_path, zip_internal in path_remap.items():
                abs_orig = os.path.join(_PROJECT_ROOT, orig_path)
                for start_ms, end_ms, rate in load_speed_ranges(abs_orig):
                    speed_ranges.append({
                        "video_path": zip_internal,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "playback_rate": rate,
                    })
            if speed_ranges:
                d["video_speed_ranges"] = speed_ranges

            cat_dict["recipes"].append({
                "book_description": book_description,
                "recipe": d,
            })

        book_dict["categories"].append(cat_dict)

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "book.json",
            json.dumps(book_dict, ensure_ascii=False, indent=2),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for zip_internal, abs_path in media_map.items():
            zf.write(abs_path, zip_internal, compress_type=zipfile.ZIP_STORED)


def import_book_from_zip(zip_path, community_origin_id=None,
                         community_origin_uploader=None):
    """Import a book from a zip archive into the database.

    Every recipe is created fresh as an owned copy of the book — no dedup
    against existing recipes in the database.

    Args:
        zip_path: Path to the zip file to import.
        community_origin_id: Optional community bookId this was downloaded from.
        community_origin_uploader: Optional Cognito userId of the original uploader.

    Returns:
        The newly assigned book_id.

    Raises:
        ValueError: If the zip is malformed or missing book.json.
    """
    tmp_dir = tempfile.mkdtemp(prefix="foodie_book_import_")
    recipe_dest_dir = os.path.join(_PROJECT_ROOT, "media", "recipes", "new")
    book_dest_dir = os.path.join(_PROJECT_ROOT, "media", "books", "new")
    try:
        # Extract zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    raise ValueError(f"Unsafe path in zip: {info.filename}")
            zf.extractall(tmp_dir)

        json_path = os.path.join(tmp_dir, "book.json")
        if not os.path.isfile(json_path):
            raise ValueError("Zip archive does not contain book.json")

        with open(json_path, "r", encoding="utf-8") as f:
            book_dict = json.load(f)

        # --- Import each recipe ---
        categories = []
        for cat_dict in book_dict.get("categories", []):
            recipe_refs = []
            for entry in cat_dict.get("recipes", []):
                book_description = entry.get("book_description")
                recipe_dict = entry.get("recipe")
                if not recipe_dict:
                    continue

                title = recipe_dict.get("title", "")

                # Always create a new recipe (owned by this book)
                recipe_id = _import_recipe_from_dict(
                    recipe_dict, tmp_dir, recipe_dest_dir
                )
                # Load title back from DB in case it was cleaned up
                rd = load_recipe_data(recipe_id)
                recipe_refs.append({
                    "recipe_id": recipe_id,
                    "title": rd.title if rd else title,
                    "book_description": book_description,
                })

            categories.append(BookCategoryData(
                category_id=None,
                name=cat_dict.get("name", "Uncategorized"),
                display_order=cat_dict.get("display_order", 0),
                recipes=recipe_refs,
            ))

        # --- Copy book-level media ---
        os.makedirs(book_dest_dir, exist_ok=True)

        def _copy_book_media(zip_internal):
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
                    dst = os.path.join(book_dest_dir, new_name)
                    img.save(dst, "JPEG", 85)
                    return f"media/books/new/{new_name}"
            new_name = f"{uuid.uuid4().hex}{ext}"
            dst = os.path.join(book_dest_dir, new_name)
            shutil.copy2(src, dst)
            return f"media/books/new/{new_name}"

        cover_path = _copy_book_media(book_dict.get("cover_image_path"))
        intro_path = _copy_book_media(book_dict.get("intro_video_path"))

        # --- Insert book into DB ---
        bd = BookData(
            book_id=None,
            title=book_dict.get("title", "Untitled Book"),
            description=book_dict.get("description", ""),
            producer=book_dict.get("producer", ""),
            community_price_type=book_dict.get("community_price_type"),
            cover_image_path=cover_path or _DEFAULT_IMAGE,
            intro_video_path=intro_path,
            is_book_of_moiety=book_dict.get("is_book_of_moiety", False),
            categories=categories,
        )

        # Set community origin — explicit params override JSON values
        bd.community_origin_id = (
            community_origin_id
            if community_origin_id is not None
            else book_dict.get("community_origin_id")
        )
        bd.community_origin_uploader = (
            community_origin_uploader
            if community_origin_uploader is not None
            else book_dict.get("community_origin_uploader")
        )
        new_book_id = insert_book_data(bd)

        # --- Rename book media folder and update paths ---
        final_book_dir = os.path.join(
            _PROJECT_ROOT, "media", "books", str(new_book_id)
        )
        if os.path.isdir(book_dest_dir) and os.listdir(book_dest_dir):
            if os.path.exists(final_book_dir):
                for fname in os.listdir(book_dest_dir):
                    shutil.move(
                        os.path.join(book_dest_dir, fname), final_book_dir
                    )
                os.rmdir(book_dest_dir)
            else:
                shutil.move(book_dest_dir, final_book_dir)

            old_prefix = "media/books/new/"
            new_prefix = f"media/books/{new_book_id}/"
            if bd.cover_image_path and bd.cover_image_path.startswith(old_prefix):
                bd.cover_image_path = bd.cover_image_path.replace(
                    old_prefix, new_prefix, 1
                )
            if bd.intro_video_path and bd.intro_video_path.startswith(old_prefix):
                bd.intro_video_path = bd.intro_video_path.replace(
                    old_prefix, new_prefix, 1
                )
            bd.book_id = new_book_id
            bd.categories = categories
            save_book_data(bd)

        return new_book_id

    except Exception:
        # Clean up on failure
        if os.path.isdir(book_dest_dir):
            shutil.rmtree(book_dest_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _import_recipe_from_dict(recipe_dict, tmp_dir, dest_dir):
    """Import a single recipe from its dict representation.

    Handles media copying, DB insertion, folder renaming, and speed ranges.
    Returns the new recipe_id.
    """
    # Reconstruct RecipeData (filter unknown keys so server-added fields don't crash)
    _recipe_fields = {f.name for f in fields(RecipeData)}
    _step_fields = {f.name for f in fields(StepData)}
    _ingredient_fields = {f.name for f in fields(IngredientData)}

    d = dict(recipe_dict)  # shallow copy
    steps = []
    for sd in d.pop("steps", []):
        ingredients = [
            IngredientData(**{k: v for k, v in ing.items() if k in _ingredient_fields})
            for ing in sd.pop("ingredients", [])
        ]
        sd = {k: v for k, v in sd.items() if k in _step_fields}
        steps.append(StepData(**sd, ingredients=ingredients))

    intro_ingredients = [
        IngredientData(**{k: v for k, v in ing.items() if k in _ingredient_fields})
        for ing in d.pop("intro_ingredients", [])
    ]
    speed_ranges_data = d.pop("video_speed_ranges", [])
    d.pop("dirty", None)
    d.pop("tags", None)
    d = {k: v for k, v in d.items() if k in _recipe_fields}
    rd = RecipeData(**d, steps=steps, intro_ingredients=intro_ingredients)

    # Ensure IDs are None
    rd.recipe_id = None
    for step in rd.steps:
        step.step_id = None
        for ing in step.ingredients:
            ing.ingredient_id = None
    for ing in rd.intro_ingredients:
        ing.ingredient_id = None

    # Copy media files
    os.makedirs(dest_dir, exist_ok=True)
    media_path_remap = {}

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

    # Rename media folder
    final_dir = os.path.join(_PROJECT_ROOT, "media", "recipes", str(new_id))
    if os.path.isdir(dest_dir) and os.listdir(dest_dir):
        if os.path.exists(final_dir):
            for fname in os.listdir(dest_dir):
                shutil.move(os.path.join(dest_dir, fname), final_dir)
            # Don't rmdir dest_dir here — other recipes may still need it
        else:
            # Move the whole folder, but recreate dest_dir for remaining recipes
            shutil.move(dest_dir, final_dir)
            os.makedirs(dest_dir, exist_ok=True)

        old_prefix = "media/recipes/new/"
        new_prefix = f"media/recipes/{new_id}/"
        if rd.main_image_path and rd.main_image_path.startswith(old_prefix):
            rd.main_image_path = rd.main_image_path.replace(
                old_prefix, new_prefix, 1
            )
        if rd.intro_video_path and rd.intro_video_path.startswith(old_prefix):
            rd.intro_video_path = rd.intro_video_path.replace(
                old_prefix, new_prefix, 1
            )
        for step in rd.steps:
            if step.image_path and step.image_path.startswith(old_prefix):
                step.image_path = step.image_path.replace(
                    old_prefix, new_prefix, 1
                )
            if step.video_path and step.video_path.startswith(old_prefix):
                step.video_path = step.video_path.replace(
                    old_prefix, new_prefix, 1
                )

        rd.recipe_id = new_id
        save_recipe_data(rd)

    # Import video speed ranges
    if speed_ranges_data:
        old_prefix = "media/recipes/new/"
        new_prefix = f"media/recipes/{new_id}/"
        for sr in speed_ranges_data:
            zip_video_path = sr.get("video_path")
            new_video_path = media_path_remap.get(zip_video_path)
            if new_video_path:
                if new_video_path.startswith(old_prefix):
                    new_video_path = new_video_path.replace(
                        old_prefix, new_prefix, 1
                    )
                abs_video_path = os.path.join(_PROJECT_ROOT, new_video_path)
                save_speed_range(
                    abs_video_path,
                    sr["start_ms"],
                    sr["end_ms"],
                    sr.get("playback_rate", 4.0),
                )

    return new_id

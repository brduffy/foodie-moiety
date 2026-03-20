"""Database access functions for the Foodie Moiety app."""

import os
import sqlite3

from utils.paths import DB_PATH, DATA_DIR

_DB_PATH = str(DB_PATH)


def _get_connection(db_path=None):
    """Return a sqlite3 connection with row_factory = sqlite3.Row."""
    path = db_path or _DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_recipes(db_path=None):
    """Return all recipes as a list of dicts, ordered by recently viewed then title."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT id, title, description, prep_time_min, cook_time_min, "
        "cuisine_type, difficulty, main_image_path, producer, content_type, is_moiety FROM recipes "
        "WHERE book_id IS NULL AND COALESCE(book_of_moiety_candidate, 0) = 0 "
        "ORDER BY last_viewed_at DESC NULLS LAST, title"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_moieties(db_path=None):
    """Return all moiety recipes as a list of dicts, ordered by title.

    Includes both standalone moieties (book_id IS NULL) and book-owned
    moieties (from Book of Moiety volumes).  Book-owned moieties include
    ``book_title`` and ``category_name`` from their parent book/category;
    standalone moieties have these fields set to None.
    """
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT r.id, r.title, r.description, r.producer, "
        "  r.main_image_path, r.book_id, "
        "  b.title AS book_title, "
        "  bc.name AS category_name "
        "FROM recipes r "
        "LEFT JOIN books b ON r.book_id = b.id "
        "LEFT JOIN book_categories bc ON r.book_category_id = bc.id "
        "WHERE r.is_moiety = 1 AND COALESCE(r.book_of_moiety_candidate, 0) = 0 "
        "ORDER BY r.title"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_books(db_path=None):
    """Return all books as a list of dicts, ordered by title.

    Each dict includes a recipe_count computed from the book_categories/recipes
    relationship (currently hardcoded to 0 since TOC is in-memory only).
    """
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT id, title, description, producer, cover_image_path, "
        "community_origin_id, community_price_type "
        "FROM books WHERE COALESCE(temp_review, 0) = 0 "
        "ORDER BY last_viewed_at DESC NULLS LAST, created_at DESC, title"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_books_with_tags(
    query: str = "",
    tags: list[str] = None,
    producers: list[str] = None,
    db_path=None,
):
    """Filter books by title, required tags, and producers.

    Args:
        query: Text to search in title (case-insensitive)
        tags: List of tag names - book must have ALL of these tags
        producers: List of producer names - book must match ANY of them
        db_path: Optional database path override

    Returns:
        List of book dicts matching the filters
    """
    conn = _get_connection(db_path)
    tags = tags or []
    producers = producers or []

    sql = """
        SELECT DISTINCT b.id, b.title, b.description, b.producer,
               b.cover_image_path, b.created_at, b.last_viewed_at,
               b.community_origin_id, b.community_price_type,
               b.is_book_of_moiety
        FROM books b
    """
    params = []

    if tags:
        sql += """
            JOIN book_tags bt ON b.id = bt.book_id
            JOIN tags t ON bt.tag_id = t.id
        """

    conditions = []
    if query:
        like = f"%{query}%"
        conditions.append("b.title LIKE ?")
        params.append(like)

    if tags:
        placeholders = ", ".join("?" * len(tags))
        conditions.append(f"t.tag_name IN ({placeholders})")
        params.extend(tags)

    if producers:
        placeholders = ", ".join("?" * len(producers))
        conditions.append(f"b.producer IN ({placeholders})")
        params.extend(producers)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    if tags:
        sql += f" GROUP BY b.id HAVING COUNT(DISTINCT t.tag_name) = ?"
        params.append(len(tags))

    sql += " ORDER BY b.last_viewed_at DESC NULLS LAST, b.created_at DESC, b.title"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_recipes(query, db_path=None):
    """Filter recipes where title or description contains the query (case-insensitive)."""
    conn = _get_connection(db_path)
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT id, title, description, prep_time_min, cook_time_min, "
        "cuisine_type, difficulty, main_image_path, producer FROM recipes "
        "WHERE book_id IS NULL AND COALESCE(book_of_moiety_candidate, 0) = 0 "
        "AND (title LIKE ? OR description LIKE ?) "
        "ORDER BY last_viewed_at DESC NULLS LAST, title",
        (like, like),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recipe_by_id(recipe_id, db_path=None):
    """Return a single recipe dict or None."""
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT id, title, description, prep_time_min, cook_time_min, "
        "cuisine_type, difficulty, main_image_path, producer FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def search_recipes_with_tags(
    query: str = "",
    tags: list[str] = None,
    producers: list[str] = None,
    db_path=None,
    bom_candidates_only=False,
):
    """Filter recipes by title/description, required tags, and producers.

    Args:
        query: Text to search in title or description (case-insensitive)
        tags: List of tag names - recipe must have ALL of these tags
        producers: List of producer names - recipe must match ANY of them
        db_path: Optional database path override

    Returns:
        List of recipe dicts matching the filters
    """
    conn = _get_connection(db_path)
    tags = tags or []
    producers = producers or []

    # Base query
    sql = """
        SELECT DISTINCT r.id, r.title, r.description, r.prep_time_min,
               r.cook_time_min, r.cuisine_type, r.difficulty, r.main_image_path,
               r.producer, r.last_viewed_at, r.content_type, r.is_moiety
        FROM recipes r
    """
    params = []

    # Join tags if filtering by them
    if tags:
        sql += """
            JOIN recipe_tags rt ON r.id = rt.recipe_id
            JOIN tags t ON rt.tag_id = t.id
        """

    # WHERE clause — always exclude book-owned copies
    conditions = ["r.book_id IS NULL"]
    if bom_candidates_only:
        conditions.append("r.book_of_moiety_candidate = 1")
    else:
        conditions.append("COALESCE(r.book_of_moiety_candidate, 0) = 0")
    if query:
        like = f"%{query}%"
        conditions.append("(r.title LIKE ? OR r.description LIKE ?)")
        params.extend([like, like])

    if tags:
        # Filter to recipes that have ALL specified tags
        placeholders = ", ".join("?" * len(tags))
        conditions.append(f"t.tag_name IN ({placeholders})")
        params.extend(tags)

    if producers:
        placeholders = ", ".join("?" * len(producers))
        conditions.append(f"r.producer IN ({placeholders})")
        params.extend(producers)

    sql += " WHERE " + " AND ".join(conditions)

    # Group by recipe and ensure it has ALL tags (not just any)
    if tags:
        sql += f" GROUP BY r.id HAVING COUNT(DISTINCT t.tag_name) = ?"
        params.append(len(tags))

    sql += " ORDER BY r.last_viewed_at DESC NULLS LAST, r.title"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    results = [dict(r) for r in rows]
    for r in results:
        r["type"] = "article" if r.get("content_type") == "article" else "recipe"
    return results


def search_all_with_tags(
    query: str = "",
    tags: list[str] = None,
    producers: list[str] = None,
    db_path=None,
):
    """Search recipes and books, returning a merged list with type annotations.

    Each dict in the result has a ``type`` field: ``"recipe"`` or ``"book"``.
    Sorted by most recently touched (last_viewed_at for recipes, created_at
    for books), so recently viewed/created items appear first.
    """
    recipes = search_recipes_with_tags(query, tags, producers, db_path)
    books = search_books_with_tags(query, tags, producers, db_path)
    for b in books:
        b["type"] = "book"
        # Normalise column names so RecipeCard can render books too
        b.setdefault("main_image_path", b.get("cover_image_path"))
    # Merge by timestamp so recently touched items appear first
    combined = recipes + books
    combined.sort(
        key=lambda x: x.get("last_viewed_at") or x.get("created_at") or "",
        reverse=True,
    )
    return combined


def get_all_producers(db_path=None):
    """Return a sorted list of distinct non-empty producer names from recipes and books."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT producer FROM ("
        "  SELECT producer FROM recipes "
        "  WHERE producer IS NOT NULL AND producer != '' "
        "    AND book_id IS NULL "
        "  UNION "
        "  SELECT producer FROM books "
        "  WHERE producer IS NOT NULL AND producer != ''"
        ") ORDER BY producer COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [row["producer"] for row in rows]



def get_all_cuisines(db_path=None):
    """Return a sorted list of distinct non-empty cuisine types from recipes."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT cuisine_type FROM recipes "
        "WHERE cuisine_type IS NOT NULL AND cuisine_type != '' "
        "AND book_id IS NULL ORDER BY cuisine_type COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [row["cuisine_type"] for row in rows]


def get_total_recipe_count(db_path=None):
    """Return the total number of recipes in the database."""
    conn = _get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM recipes WHERE book_id IS NULL "
        "AND COALESCE(book_of_moiety_candidate, 0) = 0"
    ).fetchone()[0]
    conn.close()
    return count


def get_all_tags(db_path=None):
    """Return a list of all unique tag names from the tags table."""
    conn = _get_connection(db_path)
    rows = conn.execute("SELECT tag_name FROM tags ORDER BY tag_name COLLATE NOCASE").fetchall()
    conn.close()
    return [row["tag_name"] for row in rows]


def create_tag(tag_name, db_path=None):
    """Create a new tag in the tags table.

    Returns True if the tag was created, False if it already exists.
    """
    conn = _get_connection(db_path)
    try:
        conn.execute("INSERT INTO tags (tag_name) VALUES (?)", (tag_name,))
        conn.commit()
        return True
    except Exception:
        # Tag already exists (UNIQUE constraint)
        return False
    finally:
        conn.close()


def is_canonical_tag(tag_name, db_path=None):
    """Return True if the tag is canonical (not user-created)."""
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT is_canonical FROM tags WHERE tag_name = ?", (tag_name,)
    ).fetchone()
    conn.close()
    return bool(row and row["is_canonical"])


def rename_tag(old_name, new_name, db_path=None):
    """Rename a tag. Returns True on success, False if new name already exists
    or if the tag is canonical."""
    if is_canonical_tag(old_name, db_path):
        return False
    conn = _get_connection(db_path)
    try:
        conn.execute(
            "UPDATE tags SET tag_name = ? WHERE tag_name = ?",
            (new_name, old_name),
        )
        conn.commit()
        return True
    except Exception:
        # New name already exists (UNIQUE constraint)
        return False
    finally:
        conn.close()


def delete_tag(tag_name, db_path=None):
    """Delete a tag and remove it from all recipes. Refuses canonical tags."""
    if is_canonical_tag(tag_name, db_path):
        return False
    conn = _get_connection(db_path)
    tag_row = conn.execute(
        "SELECT id FROM tags WHERE tag_name = ?", (tag_name,)
    ).fetchone()
    if tag_row:
        conn.execute("DELETE FROM recipe_tags WHERE tag_id = ?", (tag_row["id"],))
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_row["id"],))
        conn.commit()
    conn.close()
    return True


def get_canonical_tags(db_path=None):
    """Return the set of canonical tag names (for export filtering)."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT tag_name FROM tags WHERE is_canonical = 1"
    ).fetchall()
    conn.close()
    return {row["tag_name"] for row in rows}


def get_tag_usage_count(tag_name, db_path=None):
    """Return the number of recipes using a tag."""
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM recipe_tags rt "
        "JOIN tags t ON t.id = rt.tag_id "
        "WHERE t.tag_name = ?",
        (tag_name,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


# Default tags to seed on first run
_DEFAULT_TAGS = [
    # Meal Type
    "Appetizer", "Breakfast", "Brunch", "Dessert", "Dinner", "Lunch", "Side Dish", "Snack",
    # Cuisine
    "American", "Asian", "Chinese", "French", "Greek", "Indian", "Italian",
    "Japanese", "Mediterranean", "Mexican", "Middle Eastern", "Thai",
    # Dietary
    "Dairy-Free", "Gluten-Free", "Keto", "Low-Carb", "Paleo", "Vegan", "Vegetarian", "Whole30",
    # Cooking Method
    "Air Fryer", "Baked", "Grilled", "Instant Pot", "No-Cook", "One-Pot", "Slow Cooker", "Stovetop",
    # Time & Effort
    "Freezer-Friendly", "Make Ahead", "Meal Prep", "Quick", "Weeknight",
    # Occasion
    "BBQ", "Comfort Food", "Date Night", "Holiday", "Party", "Potluck", "Summer", "Winter",
    # Protein
    "Beef", "Chicken", "Pork", "Seafood", "Tofu",
    # Characteristics
    "Budget-Friendly", "Crowd-Pleaser", "Healthy", "Kid-Friendly", "Spicy",
    # Building Blocks
    "Moiety",
]


def seed_default_tags(db_path=None):
    """Seed the tags table with default canonical tags if they don't exist.

    Uses INSERT OR IGNORE to safely skip existing tags.
    """
    conn = _get_connection(db_path)
    conn.executemany(
        "INSERT OR IGNORE INTO tags (tag_name, is_canonical) VALUES (?, 1)",
        [(tag,) for tag in _DEFAULT_TAGS],
    )
    conn.commit()
    conn.close()


def ensure_schema_migrations(db_path=None):
    """Apply any missing schema migrations.

    Safe to call multiple times - uses IF NOT EXISTS / try-except patterns.
    """
    conn = _get_connection(db_path)
    # Add last_viewed_at column for "recently viewed" sorting
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN last_viewed_at TIMESTAMP")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass
    # Add intro_video_path column for intro step videos
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN intro_video_path TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass
    # Add producer column for recipe attribution
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN producer TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass
    # Add video_speed_ranges table for speed-up zones on video timelines
    try:
        conn.execute("""
            CREATE TABLE video_speed_ranges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_path TEXT NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                playback_rate REAL DEFAULT 4.0,
                UNIQUE(video_path, start_ms)
            )
        """)
        conn.commit()
    except sqlite3.OperationalError:
        # Table already exists
        pass
    # Books table
    try:
        conn.execute("""
            CREATE TABLE books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                producer TEXT,
                cover_image_path TEXT,
                intro_video_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Book categories table
    try:
        conn.execute("""
            CREATE TABLE book_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id),
                name TEXT NOT NULL,
                display_order INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Recipe-to-book columns
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN book_id INTEGER REFERENCES books(id)")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN book_category_id INTEGER REFERENCES book_categories(id)")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN book_order INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN book_description TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Book tags table (same schema pattern as recipe_tags)
    try:
        conn.execute("""
            CREATE TABLE book_tags (
                book_id INTEGER NOT NULL REFERENCES books(id),
                tag_id INTEGER NOT NULL REFERENCES tags(id),
                PRIMARY KEY (book_id, tag_id)
            )
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Add last_viewed_at column to books for "recently viewed" sorting
    try:
        conn.execute("ALTER TABLE books ADD COLUMN last_viewed_at TIMESTAMP")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Junction table for many-to-many recipe-to-book links
    try:
        conn.execute("""
            CREATE TABLE book_recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id),
                book_category_id INTEGER NOT NULL REFERENCES book_categories(id),
                recipe_id INTEGER NOT NULL REFERENCES recipes(id),
                book_order INTEGER DEFAULT 0,
                book_description TEXT
            )
        """)
        conn.commit()
        # Migrate existing data from recipe columns to junction table
        conn.execute("""
            INSERT INTO book_recipes
                (book_id, book_category_id, recipe_id, book_order, book_description)
            SELECT book_id, book_category_id, id, book_order, book_description
            FROM recipes
            WHERE book_id IS NOT NULL AND book_category_id IS NOT NULL
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Migrate book_recipes junction data back to recipe columns (owned copies model)
    try:
        rows = conn.execute(
            "SELECT recipe_id, book_id, book_category_id, book_order, "
            "book_description FROM book_recipes"
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE recipes SET book_id=?, book_category_id=?, book_order=?, "
                "book_description=? WHERE id=?",
                (r["book_id"], r["book_category_id"], r["book_order"],
                 r["book_description"], r["recipe_id"]),
            )
        if rows:
            conn.execute("DELETE FROM book_recipes")
        conn.commit()
    except Exception:
        pass
    # Add is_canonical column to tags table
    try:
        conn.execute(
            "ALTER TABLE tags ADD COLUMN is_canonical INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Mark all default tags as canonical
    try:
        _canonical_set = set(_DEFAULT_TAGS)
        existing = conn.execute("SELECT id, tag_name FROM tags").fetchall()
        for row in existing:
            if row["tag_name"] in _canonical_set:
                conn.execute(
                    "UPDATE tags SET is_canonical = 1 WHERE id = ?", (row["id"],)
                )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Grocery list table
    try:
        conn.execute("""
            CREATE TABLE grocery_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Community origin tracking
    for col in ("community_origin_id TEXT", "community_origin_uploader TEXT"):
        for table in ("recipes", "books"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    # Community price type for blocking export of purchased books
    try:
        conn.execute("ALTER TABLE books ADD COLUMN community_price_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Temp review flag for hiding temp-imported books from the book list
    try:
        conn.execute("ALTER TABLE books ADD COLUMN temp_review INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Book of Moiety flag for curated moiety collections
    try:
        conn.execute("ALTER TABLE books ADD COLUMN is_book_of_moiety INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Content type for distinguishing recipes from articles
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN content_type TEXT DEFAULT 'recipe'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Moiety flag for recipes that serve as reusable building blocks
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN is_moiety INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Book of Moiety candidate flag (set when approved from review)
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN book_of_moiety_candidate INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Clean up any leftover temp review imports from previous sessions.
    # Use delete_book / delete_recipe which handle DB + media cleanup.
    try:
        temp_book_ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM books WHERE temp_review = 1"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        temp_book_ids = []
    try:
        temp_recipe_ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM recipes WHERE book_id = -1"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        temp_recipe_ids = []
    conn.close()
    for bid in temp_book_ids:
        try:
            delete_book(bid)
        except Exception:
            pass
    for rid in temp_recipe_ids:
        try:
            delete_recipe(rid)
        except Exception:
            pass

    # Clean up leftover staging directories from interrupted imports.
    _project_root = str(DATA_DIR)
    for staging in ("media/recipes/new", "media/books/new"):
        staging_dir = os.path.join(_project_root, staging)
        if os.path.isdir(staging_dir):
            import shutil
            shutil.rmtree(staging_dir, ignore_errors=True)


def mark_recipe_viewed(recipe_id, db_path=None):
    """Update the last_viewed_at timestamp for a recipe.

    Called when a recipe is opened in the detail view.
    """
    conn = _get_connection(db_path)
    conn.execute(
        "UPDATE recipes SET last_viewed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (recipe_id,),
    )
    conn.commit()
    conn.close()


def mark_book_viewed(book_id, db_path=None):
    """Update the last_viewed_at timestamp for a book.

    Called when a book is opened in the book view.
    """
    conn = _get_connection(db_path)
    conn.execute(
        "UPDATE books SET last_viewed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (book_id,),
    )
    conn.commit()
    conn.close()


def get_standalone_ids_by_titles(titles, db_path=None):
    """Return standalone recipe IDs whose titles match the given set.

    Used to map book-owned copies back to their original standalone recipes
    so the add-to-book UI can mark them as already added.
    """
    if not titles:
        return set()
    conn = _get_connection(db_path)
    placeholders = ", ".join("?" * len(titles))
    rows = conn.execute(
        f"SELECT id FROM recipes WHERE book_id IS NULL AND title IN ({placeholders})",
        list(titles),
    ).fetchall()
    conn.close()
    return {r["id"] for r in rows}


def load_recipe_data(recipe_id, db_path=None):
    """Load a full RecipeData from the database.

    Queries the recipe, its ingredients, steps, step_ingredients, and tags,
    then assembles them into a RecipeData dataclass instance.

    Returns None if the recipe_id doesn't exist.
    """
    from models.recipe_data import IngredientData, RecipeData, StepData

    conn = _get_connection(db_path)

    # Recipe header
    recipe_row = conn.execute(
        "SELECT id, title, description, prep_time_min, cook_time_min, "
        "cuisine_type, difficulty, main_image_path, intro_video_path, producer, "
        "community_origin_id, community_origin_uploader, content_type, is_moiety "
        "FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if not recipe_row:
        conn.close()
        return None

    # All ingredients for this recipe (keyed by id for lookup)
    ingredient_rows = conn.execute(
        "SELECT id, item_name, total_quantity, unit FROM ingredients "
        "WHERE recipe_id = ? ORDER BY id",
        (recipe_id,),
    ).fetchall()
    ingredients_by_id = {row["id"]: dict(row) for row in ingredient_rows}

    # Steps ordered by step_number
    step_rows = conn.execute(
        "SELECT id, step_number, instruction, image_path, "
        "is_timer_required, timer_duration_sec, is_critical, video_path "
        "FROM recipe_steps WHERE recipe_id = ? ORDER BY step_number",
        (recipe_id,),
    ).fetchall()

    # Step-ingredient links
    si_rows = conn.execute(
        "SELECT si.step_id, si.ingredient_id, si.amount_override "
        "FROM step_ingredients si "
        "JOIN recipe_steps rs ON si.step_id = rs.id "
        "WHERE rs.recipe_id = ?",
        (recipe_id,),
    ).fetchall()
    # Group by step_id
    si_by_step = {}
    for si in si_rows:
        si_by_step.setdefault(si["step_id"], []).append(dict(si))

    # Tags
    tag_rows = conn.execute(
        "SELECT t.tag_name FROM tags t "
        "JOIN recipe_tags rt ON t.id = rt.tag_id "
        "WHERE rt.recipe_id = ? ORDER BY t.tag_name",
        (recipe_id,),
    ).fetchall()

    conn.close()

    # Build StepData list, separating intro ingredients (step_number=0)
    steps = []
    intro_ingredients = []
    for sr in step_rows:
        step_ingredients = []
        for si in si_by_step.get(sr["id"], []):
            ing = ingredients_by_id.get(si["ingredient_id"])
            if ing:
                step_ingredients.append(IngredientData(
                    ingredient_id=ing["id"],
                    item_name=ing["item_name"],
                    quantity=ing["total_quantity"],
                    unit=ing["unit"],
                    amount_override=si["amount_override"],
                ))
        if sr["step_number"] == 0:
            # Virtual intro step — extract ingredients only
            intro_ingredients = step_ingredients
        else:
            steps.append(StepData(
                step_id=sr["id"],
                step_number=sr["step_number"],
                instruction=sr["instruction"] or "",
                image_path=sr["image_path"],
                is_timer_required=bool(sr["is_timer_required"]),
                timer_duration_sec=sr["timer_duration_sec"] or 0,
                is_critical=bool(sr["is_critical"]),
                video_path=sr["video_path"] or None,
                ingredients=step_ingredients,
            ))

    return RecipeData(
        recipe_id=recipe_row["id"],
        title=recipe_row["title"] or "",
        description=recipe_row["description"] or "",
        prep_time_min=recipe_row["prep_time_min"],
        cook_time_min=recipe_row["cook_time_min"],
        cuisine_type=recipe_row["cuisine_type"],
        difficulty=recipe_row["difficulty"],
        main_image_path=recipe_row["main_image_path"],
        intro_video_path=recipe_row["intro_video_path"],
        producer=recipe_row["producer"] or "",
        community_origin_id=recipe_row["community_origin_id"],
        community_origin_uploader=recipe_row["community_origin_uploader"],
        steps=steps,
        intro_ingredients=intro_ingredients,
        tags=[row["tag_name"] for row in tag_rows],
        content_type=recipe_row["content_type"] or "recipe",
        is_moiety=bool(recipe_row["is_moiety"]),
    )


def insert_recipe_data(recipe_data, db_path=None):
    """Insert a new RecipeData instance into the database.

    Creates a new recipe row and all related child rows (steps, ingredients,
    step-ingredient links, tags). Returns the new recipe_id.

    Args:
        recipe_data: RecipeData with recipe_id=None (new recipe)
        db_path: Optional database path override

    Returns:
        int: The newly assigned recipe_id
    """
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()

        # Insert recipe header
        cur.execute(
            "INSERT INTO recipes (title, description, prep_time_min, cook_time_min, "
            "cuisine_type, difficulty, main_image_path, intro_video_path, producer, "
            "community_origin_id, community_origin_uploader, content_type, is_moiety, "
            "last_viewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (
                recipe_data.title,
                recipe_data.description,
                recipe_data.prep_time_min,
                recipe_data.cook_time_min,
                recipe_data.cuisine_type,
                recipe_data.difficulty,
                recipe_data.main_image_path,
                recipe_data.intro_video_path,
                recipe_data.producer,
                recipe_data.community_origin_id,
                recipe_data.community_origin_uploader,
                recipe_data.content_type,
                int(recipe_data.is_moiety),
            ),
        )
        rid = cur.lastrowid

        # Insert intro ingredients (virtual step 0)
        if recipe_data.intro_ingredients:
            cur.execute(
                "INSERT INTO recipe_steps "
                "(recipe_id, step_number, instruction, image_path, "
                "is_timer_required, timer_duration_sec, is_critical, video_path) "
                "VALUES (?, 0, '', NULL, 0, 0, 0, NULL)",
                (rid,),
            )
            intro_step_id = cur.lastrowid
            for ing in recipe_data.intro_ingredients:
                cur.execute(
                    "INSERT INTO ingredients "
                    "(recipe_id, item_name, total_quantity, unit) "
                    "VALUES (?, ?, ?, ?)",
                    (rid, ing.item_name, ing.quantity, ing.unit),
                )
                new_ing_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO step_ingredients "
                    "(step_id, ingredient_id, amount_override) "
                    "VALUES (?, ?, ?)",
                    (intro_step_id, new_ing_id, ing.amount_override),
                )

        # Insert steps and ingredients
        for step in recipe_data.steps:
            cur.execute(
                "INSERT INTO recipe_steps "
                "(recipe_id, step_number, instruction, image_path, "
                "is_timer_required, timer_duration_sec, is_critical, video_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    step.step_number,
                    step.instruction,
                    step.image_path,
                    int(step.is_timer_required),
                    step.timer_duration_sec,
                    int(step.is_critical),
                    step.video_path,
                ),
            )
            new_step_id = cur.lastrowid

            for ing in step.ingredients:
                cur.execute(
                    "INSERT INTO ingredients "
                    "(recipe_id, item_name, total_quantity, unit) "
                    "VALUES (?, ?, ?, ?)",
                    (rid, ing.item_name, ing.quantity, ing.unit),
                )
                new_ing_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO step_ingredients "
                    "(step_id, ingredient_id, amount_override) "
                    "VALUES (?, ?, ?)",
                    (new_step_id, new_ing_id, ing.amount_override),
                )

        # Insert tags
        for tag_name in recipe_data.tags:
            cur.execute(
                "INSERT OR IGNORE INTO tags (tag_name) VALUES (?)",
                (tag_name,),
            )
            cur.execute(
                "SELECT id FROM tags WHERE tag_name = ?", (tag_name,)
            )
            tag_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                (rid, tag_id),
            )

        conn.commit()
        return rid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_recipe_data(recipe_data, db_path=None):
    """Persist a RecipeData instance to the database.

    Uses a full-replace strategy: updates the recipe header, then deletes
    and re-inserts all steps, ingredients, step-ingredient links, and tags.
    Runs inside a single transaction so the save is atomic.
    """
    conn = _get_connection(db_path)
    rid = recipe_data.recipe_id
    try:
        cur = conn.cursor()

        # -- Recipe header --
        cur.execute(
            "UPDATE recipes SET title = ?, description = ?, prep_time_min = ?, "
            "cook_time_min = ?, cuisine_type = ?, difficulty = ?, main_image_path = ?, "
            "intro_video_path = ?, producer = ?, "
            "community_origin_id = ?, community_origin_uploader = ?, "
            "content_type = ?, is_moiety = ? WHERE id = ?",
            (
                recipe_data.title,
                recipe_data.description,
                recipe_data.prep_time_min,
                recipe_data.cook_time_min,
                recipe_data.cuisine_type,
                recipe_data.difficulty,
                recipe_data.main_image_path,
                recipe_data.intro_video_path,
                recipe_data.producer,
                recipe_data.community_origin_id,
                recipe_data.community_origin_uploader,
                recipe_data.content_type,
                int(recipe_data.is_moiety),
                rid,
            ),
        )

        # -- Delete existing child rows --
        # step_ingredients references recipe_steps, so delete links first
        cur.execute(
            "DELETE FROM step_ingredients WHERE step_id IN "
            "(SELECT id FROM recipe_steps WHERE recipe_id = ?)",
            (rid,),
        )
        cur.execute("DELETE FROM recipe_steps WHERE recipe_id = ?", (rid,))
        cur.execute("DELETE FROM ingredients WHERE recipe_id = ?", (rid,))
        cur.execute(
            "DELETE FROM recipe_tags WHERE recipe_id = ?", (rid,)
        )

        # -- Re-insert intro ingredients (virtual step 0) --
        if recipe_data.intro_ingredients:
            cur.execute(
                "INSERT INTO recipe_steps "
                "(recipe_id, step_number, instruction, image_path, "
                "is_timer_required, timer_duration_sec, is_critical, video_path) "
                "VALUES (?, 0, '', NULL, 0, 0, 0, NULL)",
                (rid,),
            )
            intro_step_id = cur.lastrowid
            for ing in recipe_data.intro_ingredients:
                cur.execute(
                    "INSERT INTO ingredients "
                    "(recipe_id, item_name, total_quantity, unit) "
                    "VALUES (?, ?, ?, ?)",
                    (rid, ing.item_name, ing.quantity, ing.unit),
                )
                new_ing_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO step_ingredients "
                    "(step_id, ingredient_id, amount_override) "
                    "VALUES (?, ?, ?)",
                    (intro_step_id, new_ing_id, ing.amount_override),
                )

        # -- Re-insert steps and ingredients --
        for step in recipe_data.steps:
            cur.execute(
                "INSERT INTO recipe_steps "
                "(recipe_id, step_number, instruction, image_path, "
                "is_timer_required, timer_duration_sec, is_critical, video_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    step.step_number,
                    step.instruction,
                    step.image_path,
                    int(step.is_timer_required),
                    step.timer_duration_sec,
                    int(step.is_critical),
                    step.video_path,
                ),
            )
            new_step_id = cur.lastrowid

            for ing in step.ingredients:
                cur.execute(
                    "INSERT INTO ingredients "
                    "(recipe_id, item_name, total_quantity, unit) "
                    "VALUES (?, ?, ?, ?)",
                    (rid, ing.item_name, ing.quantity, ing.unit),
                )
                new_ing_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO step_ingredients "
                    "(step_id, ingredient_id, amount_override) "
                    "VALUES (?, ?, ?)",
                    (new_step_id, new_ing_id, ing.amount_override),
                )

        # -- Re-insert tags --
        for tag_name in recipe_data.tags:
            # Ensure tag exists in the tags table
            cur.execute(
                "INSERT OR IGNORE INTO tags (tag_name) VALUES (?)",
                (tag_name,),
            )
            cur.execute(
                "SELECT id FROM tags WHERE tag_name = ?", (tag_name,)
            )
            tag_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                (rid, tag_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hide_temp_recipe(recipe_id, db_path=None):
    """Mark a recipe as a temp review import so it's excluded from recipe list.

    Sets book_id = -1 which is filtered out by 'WHERE book_id IS NULL'.
    """
    conn = _get_connection(db_path)
    conn.execute("UPDATE recipes SET book_id = -1 WHERE id = ?", (recipe_id,))
    conn.commit()
    conn.close()


def keep_as_bom_candidate(recipe_id, db_path=None):
    """Convert a temp review import into a saved BOM candidate.

    Clears book_id = -1 (unhides from temp) and sets
    book_of_moiety_candidate = 1 (hidden from recipe list, visible in BOM add-recipes).
    """
    conn = _get_connection(db_path)
    conn.execute(
        "UPDATE recipes SET book_id = NULL, book_of_moiety_candidate = 1 "
        "WHERE id = ?", (recipe_id,)
    )
    conn.commit()
    conn.close()


def hide_temp_book(book_id, db_path=None):
    """Mark a book as a temp review import so it's excluded from book list.

    Sets temp_review = 1. get_all_books filters WHERE temp_review IS NULL OR temp_review = 0.
    """
    conn = _get_connection(db_path)
    # Ensure column exists (idempotent)
    try:
        conn.execute("ALTER TABLE books ADD COLUMN temp_review INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    conn.execute("UPDATE books SET temp_review = 1 WHERE id = ?", (book_id,))
    conn.commit()
    conn.close()


def delete_recipe(recipe_id, db_path=None):
    """Delete a recipe and all related data from the database.

    Deletes the recipe, its steps, ingredients, step-ingredient links,
    and recipe-tag associations. Runs inside a single transaction.

    Args:
        recipe_id: ID of the recipe to delete
        db_path: Optional database path override
    """
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()

        # Delete step_ingredients (references recipe_steps)
        cur.execute(
            "DELETE FROM step_ingredients WHERE step_id IN "
            "(SELECT id FROM recipe_steps WHERE recipe_id = ?)",
            (recipe_id,),
        )

        # Delete recipe_steps
        cur.execute("DELETE FROM recipe_steps WHERE recipe_id = ?", (recipe_id,))

        # Delete ingredients
        cur.execute("DELETE FROM ingredients WHERE recipe_id = ?", (recipe_id,))

        # Delete recipe_tags
        cur.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))

        # Delete the recipe itself
        cur.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def find_recipe_by_title_producer(title, producer="", db_path=None):
    """Find a recipe matching the given title and producer.

    Returns a dict with 'id' and 'title', or None if no match.
    Uses exact match on both fields (case-sensitive).
    """
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT id, title FROM recipes "
        "WHERE title = ? AND COALESCE(producer, '') = ?",
        (title, producer or ""),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def find_book_by_title_producer(title, producer="", db_path=None):
    """Find a book matching the given title and producer.

    Returns a dict with 'id' and 'title', or None if no match.
    Uses exact match on both fields (case-sensitive).
    """
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT id, title FROM books "
        "WHERE title = ? AND COALESCE(producer, '') = ?",
        (title, producer or ""),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Video Speed Ranges ---


def load_speed_ranges(video_path, db_path=None):
    """Load all speed ranges for a video, sorted by start time.

    Returns a list of (start_ms, end_ms, playback_rate) tuples.
    """
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT start_ms, end_ms, playback_rate FROM video_speed_ranges "
        "WHERE video_path = ? ORDER BY start_ms",
        (video_path,),
    ).fetchall()
    conn.close()
    return [(r["start_ms"], r["end_ms"], r["playback_rate"]) for r in rows]


def save_speed_range(video_path, start_ms, end_ms, playback_rate=4.0, db_path=None):
    """Insert or replace a speed range for a video."""
    conn = _get_connection(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO video_speed_ranges "
        "(video_path, start_ms, end_ms, playback_rate) VALUES (?, ?, ?, ?)",
        (video_path, start_ms, end_ms, playback_rate),
    )
    conn.commit()
    conn.close()


def delete_speed_range(video_path, start_ms, db_path=None):
    """Delete a single speed range by its unique (video_path, start_ms) key."""
    conn = _get_connection(db_path)
    conn.execute(
        "DELETE FROM video_speed_ranges WHERE video_path = ? AND start_ms = ?",
        (video_path, start_ms),
    )
    conn.commit()
    conn.close()


def delete_all_speed_ranges(video_path, db_path=None):
    """Delete all speed ranges for a video."""
    conn = _get_connection(db_path)
    conn.execute(
        "DELETE FROM video_speed_ranges WHERE video_path = ?",
        (video_path,),
    )
    conn.commit()
    conn.close()


# --- Book CRUD ---


def insert_book_data(book_data, db_path=None):
    """Insert a new BookData instance into the database.

    Creates a new book row and all related child rows (categories,
    recipe-to-book links, tags). Returns the new book_id.
    """
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()

        # Insert book header
        cur.execute(
            "INSERT INTO books (title, description, producer, cover_image_path, "
            "intro_video_path, community_origin_id, community_origin_uploader, "
            "community_price_type, is_book_of_moiety) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book_data.title,
                book_data.description,
                book_data.producer,
                book_data.cover_image_path,
                book_data.intro_video_path,
                book_data.community_origin_id,
                book_data.community_origin_uploader,
                book_data.community_price_type,
                1 if book_data.is_book_of_moiety else 0,
            ),
        )
        bid = cur.lastrowid

        # Insert categories and set recipe ownership columns
        for cat in book_data.categories:
            cur.execute(
                "INSERT INTO book_categories (book_id, name, display_order) "
                "VALUES (?, ?, ?)",
                (bid, cat.name, cat.display_order),
            )
            cat_id = cur.lastrowid
            for order, recipe in enumerate(cat.recipes):
                cur.execute(
                    "UPDATE recipes SET book_id=?, book_category_id=?, "
                    "book_order=?, book_description=? WHERE id=?",
                    (
                        bid,
                        cat_id,
                        order,
                        recipe.get("book_description"),
                        recipe["recipe_id"],
                    ),
                )

        # Insert tags
        for tag_name in book_data.tags:
            cur.execute(
                "INSERT OR IGNORE INTO tags (tag_name) VALUES (?)",
                (tag_name,),
            )
            cur.execute(
                "SELECT id FROM tags WHERE tag_name = ?", (tag_name,)
            )
            tag_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO book_tags (book_id, tag_id) VALUES (?, ?)",
                (bid, tag_id),
            )

        conn.commit()
        return bid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_book_data(book_data, db_path=None):
    """Persist an existing BookData instance to the database.

    Uses a full-replace strategy: updates the book header, then deletes
    orphaned recipe copies, re-sets categories/ownership, and tags.
    """
    import os
    import shutil
    _project_root = str(DATA_DIR)

    conn = _get_connection(db_path)
    bid = book_data.book_id
    try:
        cur = conn.cursor()

        # Update book header
        cur.execute(
            "UPDATE books SET title = ?, description = ?, producer = ?, "
            "cover_image_path = ?, intro_video_path = ?, "
            "community_origin_id = ?, community_origin_uploader = ?, "
            "community_price_type = ?, is_book_of_moiety = ? WHERE id = ?",
            (
                book_data.title,
                book_data.description,
                book_data.producer,
                book_data.cover_image_path,
                book_data.intro_video_path,
                book_data.community_origin_id,
                book_data.community_origin_uploader,
                book_data.community_price_type,
                1 if book_data.is_book_of_moiety else 0,
                bid,
            ),
        )

        # Find recipe IDs still in the TOC
        keep_ids = {
            r["recipe_id"]
            for cat in book_data.categories for r in cat.recipes
        }
        # Find all currently owned recipes
        owned = cur.execute(
            "SELECT id FROM recipes WHERE book_id = ?", (bid,)
        ).fetchall()
        orphan_ids = [r["id"] for r in owned if r["id"] not in keep_ids]

        # Delete orphaned copies (removed from TOC during edit)
        for rid in orphan_ids:
            cur.execute(
                "DELETE FROM step_ingredients WHERE step_id IN "
                "(SELECT id FROM recipe_steps WHERE recipe_id = ?)",
                (rid,),
            )
            cur.execute("DELETE FROM recipe_steps WHERE recipe_id = ?", (rid,))
            cur.execute("DELETE FROM ingredients WHERE recipe_id = ?", (rid,))
            cur.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (rid,))
            cur.execute("DELETE FROM recipes WHERE id = ?", (rid,))

        # Delete old categories
        cur.execute("DELETE FROM book_categories WHERE book_id = ?", (bid,))
        # Delete old tags
        cur.execute("DELETE FROM book_tags WHERE book_id = ?", (bid,))

        # Re-insert categories and set recipe ownership columns
        for cat in book_data.categories:
            cur.execute(
                "INSERT INTO book_categories (book_id, name, display_order) "
                "VALUES (?, ?, ?)",
                (bid, cat.name, cat.display_order),
            )
            cat_id = cur.lastrowid
            for order, recipe in enumerate(cat.recipes):
                cur.execute(
                    "UPDATE recipes SET book_id=?, book_category_id=?, "
                    "book_order=?, book_description=? WHERE id=?",
                    (
                        bid,
                        cat_id,
                        order,
                        recipe.get("book_description"),
                        recipe["recipe_id"],
                    ),
                )

        # Re-insert tags
        for tag_name in book_data.tags:
            cur.execute(
                "INSERT OR IGNORE INTO tags (tag_name) VALUES (?)",
                (tag_name,),
            )
            cur.execute(
                "SELECT id FROM tags WHERE tag_name = ?", (tag_name,)
            )
            tag_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO book_tags (book_id, tag_id) VALUES (?, ?)",
                (bid, tag_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Clean up media folders for orphaned recipes (outside transaction)
    for rid in orphan_ids:
        media_dir = os.path.join(_project_root, "media", "recipes", str(rid))
        if os.path.isdir(media_dir):
            shutil.rmtree(media_dir, ignore_errors=True)


def load_book_data(book_id, db_path=None):
    """Load a full BookData from the database.

    Returns None if the book_id doesn't exist.
    """
    from models.recipe_data import BookCategoryData, BookData

    conn = _get_connection(db_path)

    # Book header
    book_row = conn.execute(
        "SELECT id, title, description, producer, cover_image_path, "
        "intro_video_path, community_origin_id, community_origin_uploader, "
        "community_price_type, is_book_of_moiety "
        "FROM books WHERE id = ?",
        (book_id,),
    ).fetchone()
    if not book_row:
        conn.close()
        return None

    # Categories ordered by display_order
    cat_rows = conn.execute(
        "SELECT id, name, display_order FROM book_categories "
        "WHERE book_id = ? ORDER BY display_order",
        (book_id,),
    ).fetchall()

    categories = []
    for cr in cat_rows:
        # Recipes in this category ordered by book_order
        recipe_rows = conn.execute(
            "SELECT id, title, book_description, main_image_path FROM recipes "
            "WHERE book_category_id = ? ORDER BY book_order",
            (cr["id"],),
        ).fetchall()
        recipes = [
            {
                "recipe_id": rr["id"],
                "title": rr["title"],
                "book_description": rr["book_description"],
                "main_image_path": rr["main_image_path"],
            }
            for rr in recipe_rows
        ]
        categories.append(BookCategoryData(
            category_id=cr["id"],
            name=cr["name"],
            display_order=cr["display_order"],
            recipes=recipes,
        ))

    # Tags
    tag_rows = conn.execute(
        "SELECT t.tag_name FROM tags t "
        "JOIN book_tags bt ON t.id = bt.tag_id "
        "WHERE bt.book_id = ? ORDER BY t.tag_name",
        (book_id,),
    ).fetchall()

    conn.close()

    return BookData(
        book_id=book_row["id"],
        title=book_row["title"] or "",
        description=book_row["description"] or "",
        producer=book_row["producer"] or "",
        community_origin_id=book_row["community_origin_id"],
        community_origin_uploader=book_row["community_origin_uploader"],
        community_price_type=book_row["community_price_type"],
        cover_image_path=book_row["cover_image_path"],
        intro_video_path=book_row["intro_video_path"],
        is_book_of_moiety=bool(book_row["is_book_of_moiety"]),
        categories=categories,
        tags=[row["tag_name"] for row in tag_rows],
    )


def delete_book(book_id, db_path=None):
    """Delete a book, all its owned recipes, and related data.

    In the owned-copies model, every recipe with book_id=X is the book's
    property and gets cascade-deleted along with its media folder.

    Returns a list of deleted recipe_ids so the caller can clean up media.
    """
    import os
    import shutil
    _project_root = str(DATA_DIR)

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()

        # Find all owned recipes
        owned = cur.execute(
            "SELECT id FROM recipes WHERE book_id = ?", (book_id,)
        ).fetchall()
        owned_ids = [r["id"] for r in owned]

        # Delete each owned recipe's data
        for rid in owned_ids:
            cur.execute(
                "DELETE FROM step_ingredients WHERE step_id IN "
                "(SELECT id FROM recipe_steps WHERE recipe_id = ?)",
                (rid,),
            )
            cur.execute("DELETE FROM recipe_steps WHERE recipe_id = ?", (rid,))
            cur.execute("DELETE FROM ingredients WHERE recipe_id = ?", (rid,))
            cur.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (rid,))
            cur.execute("DELETE FROM recipes WHERE id = ?", (rid,))

        # Delete book metadata
        cur.execute("DELETE FROM book_categories WHERE book_id = ?", (book_id,))
        cur.execute("DELETE FROM book_tags WHERE book_id = ?", (book_id,))
        cur.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Clean up media folders (outside transaction)
    for rid in owned_ids:
        media_dir = os.path.join(_project_root, "media", "recipes", str(rid))
        if os.path.isdir(media_dir):
            shutil.rmtree(media_dir, ignore_errors=True)
    book_media = os.path.join(_project_root, "media", "books", str(book_id))
    if os.path.isdir(book_media):
        shutil.rmtree(book_media, ignore_errors=True)

    return owned_ids


def copy_recipe_to_book(recipe_id, book_id, category_id, order,
                        book_description=None, db_path=None):
    """Deep-copy a recipe into a book as an owned copy.

    Creates a full clone of the recipe (data + media files) and sets the
    book ownership columns on the new recipe row.

    Args:
        recipe_id: Source recipe to copy.
        book_id: Book that will own the copy.
        category_id: Book category to place the copy in.
        order: Display order within the category.
        book_description: Optional description override for the book TOC.
        db_path: Optional database path override.

    Returns:
        The new recipe_id of the copy.
    """
    import os
    import shutil
    import uuid as _uuid
    _project_root = str(DATA_DIR)

    rd = load_recipe_data(recipe_id, db_path)
    if rd is None:
        raise ValueError(f"Recipe with id {recipe_id} not found")

    # Prepare for insert as new recipe
    rd.recipe_id = None
    for step in rd.steps:
        step.step_id = None
        for ing in step.ingredients:
            ing.ingredient_id = None
    for ing in rd.intro_ingredients:
        ing.ingredient_id = None

    # Copy media files to a temp staging folder
    dest_dir = os.path.join(_project_root, "media", "recipes", "new")
    os.makedirs(dest_dir, exist_ok=True)

    def _copy_media(rel_path):
        if not rel_path or rel_path == "media/default.jpg":
            return rel_path
        abs_path = os.path.join(_project_root, rel_path)
        if not os.path.isfile(abs_path):
            return rel_path
        ext = os.path.splitext(rel_path)[1].lower()
        new_name = f"{_uuid.uuid4().hex}{ext}"
        dst = os.path.join(dest_dir, new_name)
        shutil.copy2(abs_path, dst)
        return f"media/recipes/new/{new_name}"

    rd.main_image_path = _copy_media(rd.main_image_path)
    rd.intro_video_path = _copy_media(rd.intro_video_path)
    for step in rd.steps:
        step.image_path = _copy_media(step.image_path)
        step.video_path = _copy_media(step.video_path)

    # Insert into database
    new_id = insert_recipe_data(rd, db_path)

    # Rename media folder from "new" to actual recipe ID
    final_dir = os.path.join(_project_root, "media", "recipes", str(new_id))
    if os.path.isdir(dest_dir) and os.listdir(dest_dir):
        if os.path.exists(final_dir):
            for fname in os.listdir(dest_dir):
                shutil.move(os.path.join(dest_dir, fname), final_dir)
        else:
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
        save_recipe_data(rd, db_path)

    # Set book ownership columns
    conn = _get_connection(db_path)
    conn.execute(
        "UPDATE recipes SET book_id=?, book_category_id=?, book_order=?, "
        "book_description=? WHERE id=?",
        (book_id, category_id, order, book_description, new_id),
    )
    conn.commit()
    conn.close()

    return new_id


# ── Grocery list ──────────────────────────────────────────────────────

def get_grocery_items(db_path=None):
    """Return all grocery items ordered by creation time."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT id, text FROM grocery_items ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "text": r["text"]} for r in rows]


def add_grocery_item(text, db_path=None):
    """Insert a grocery item. Returns the new row id."""
    conn = _get_connection(db_path)
    cur = conn.execute("INSERT INTO grocery_items (text) VALUES (?)", (text,))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def update_grocery_item(item_id, text, db_path=None):
    """Update the text of a grocery item."""
    conn = _get_connection(db_path)
    conn.execute("UPDATE grocery_items SET text = ? WHERE id = ?", (text, item_id))
    conn.commit()
    conn.close()


def delete_grocery_item(item_id, db_path=None):
    """Delete a grocery item by id."""
    conn = _get_connection(db_path)
    conn.execute("DELETE FROM grocery_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()


def clear_grocery_items(db_path=None):
    """Delete all grocery items."""
    conn = _get_connection(db_path)
    conn.execute("DELETE FROM grocery_items")
    conn.commit()
    conn.close()

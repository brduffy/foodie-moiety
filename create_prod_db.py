"""Generate a clean production database with schema + default tags only.

The base tables (recipes, steps, ingredients, tags, etc.) are not created
by ensure_schema_migrations() — they come from the original bundled DB.
This script creates them from scratch, then applies all migrations and
seeds default tags, producing a clean DB ready for the installer.

Usage:
    python create_prod_db.py
"""

import os
import sqlite3
import sys

# Base tables — these predate the migration system and must be created first.
_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    prep_time_min INTEGER,
    cook_time_min INTEGER,
    cuisine_type TEXT,
    difficulty TEXT,
    main_image_path TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER,
    video_path TEXT NOT NULL,
    video_label TEXT,
    is_primary BOOLEAN DEFAULT 0,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
);

CREATE TABLE IF NOT EXISTS video_markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER,
    label TEXT,
    start_time_sec INTEGER,
    end_time_sec INTEGER,
    FOREIGN KEY (video_id) REFERENCES videos(id)
);

CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER,
    item_name TEXT NOT NULL,
    total_quantity REAL,
    unit TEXT,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
);

CREATE TABLE IF NOT EXISTS recipe_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER,
    step_number INTEGER NOT NULL,
    instruction TEXT NOT NULL,
    image_path TEXT,
    video_marker_id INTEGER,
    is_timer_required BOOLEAN DEFAULT 0,
    timer_duration_sec INTEGER DEFAULT 0,
    is_critical BOOLEAN DEFAULT 0,
    video_path TEXT,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id),
    FOREIGN KEY (video_marker_id) REFERENCES video_markers(id)
);

CREATE TABLE IF NOT EXISTS step_ingredients (
    step_id INTEGER,
    ingredient_id INTEGER,
    amount_override TEXT,
    PRIMARY KEY (step_id, ingredient_id),
    FOREIGN KEY (step_id) REFERENCES recipe_steps(id),
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id INTEGER,
    tag_id INTEGER,
    PRIMARY KEY (recipe_id, tag_id),
    FOREIGN KEY (recipe_id) REFERENCES recipes(id),
    FOREIGN KEY (tag_id) REFERENCES tags(id)
);
"""


def main():
    out = os.path.join("dist", "foodie_moiety_prod.db")
    os.makedirs("dist", exist_ok=True)

    # Start from scratch
    if os.path.exists(out):
        os.remove(out)

    # Create base tables
    conn = sqlite3.connect(out)
    conn.executescript(_BASE_SCHEMA)
    conn.commit()
    conn.close()

    # Apply all migrations (adds columns, creates books/grocery/etc. tables)
    from utils.database import ensure_schema_migrations, seed_default_tags
    ensure_schema_migrations(db_path=out)
    seed_default_tags(db_path=out)

    # Verify
    conn = sqlite3.connect(out)
    tables = sorted(
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        ).fetchall()
    )
    tag_count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    recipe_count = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    conn.close()

    print(f"Production DB: {out}")
    print(f"  Tables ({len(tables)}): {', '.join(tables)}")
    print(f"  Tags: {tag_count}")
    print(f"  Recipes: {recipe_count}")

    if recipe_count != 0:
        print("ERROR: Production DB should have no recipes!", file=sys.stderr)
        sys.exit(1)

    print("OK — clean production database created.")


if __name__ == "__main__":
    main()

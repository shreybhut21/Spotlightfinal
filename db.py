import sqlite3
import os
import click
from flask import g

# ======================================================
# DATABASE PATH
# ======================================================
def _get_db_path():
    return os.path.join(os.path.dirname(__file__), "database.db")

# ======================================================
# CONNECTION (Flask-safe)
# ======================================================
def get_db_connection():
    db = getattr(g, "_database", None)
    if db is None:
        db = sqlite3.connect(_get_db_path(), check_same_thread=False)
        db.row_factory = sqlite3.Row
        g._database = db
    return db

def close_db(e=None):
    db = g.pop("_database", None)
    if db is not None:
        db.close()

# ======================================================
# INIT DATABASE (SAFE + AUTO-MIGRATION)
# ======================================================
def init_db():
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # --------------------------------------------------
    # USERS
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,

            gender TEXT,
            dob TEXT,              -- YYYY-MM-DD
            bio TEXT,
            vibe_tags TEXT,        -- comma-separated

            trust_score INTEGER DEFAULT 100,
            avatar_level INTEGER DEFAULT 1,
            avatar_url TEXT,
            is_active INTEGER DEFAULT 1,

            is_matched INTEGER DEFAULT 0,
            matched_with INTEGER,

            created_at REAL
        )
    """)

    user_cols = [r["name"] for r in c.execute("PRAGMA table_info(users)")]

    def add_col(name, sql):
        if name not in user_cols:
            c.execute(sql)

    add_col("gender", "ALTER TABLE users ADD COLUMN gender TEXT")
    add_col("dob", "ALTER TABLE users ADD COLUMN dob TEXT")
    add_col("bio", "ALTER TABLE users ADD COLUMN bio TEXT")
    add_col("vibe_tags", "ALTER TABLE users ADD COLUMN vibe_tags TEXT")
    add_col("trust_score", "ALTER TABLE users ADD COLUMN trust_score INTEGER DEFAULT 100")
    add_col("avatar_level", "ALTER TABLE users ADD COLUMN avatar_level INTEGER DEFAULT 1")
    add_col("avatar_url", "ALTER TABLE users ADD COLUMN avatar_url TEXT")
    add_col("is_active", "ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    add_col("is_matched", "ALTER TABLE users ADD COLUMN is_matched INTEGER DEFAULT 0")
    add_col("matched_with", "ALTER TABLE users ADD COLUMN matched_with INTEGER")

    # --------------------------------------------------
    # SPOTLIGHTS
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS spotlights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            place TEXT,
            intent TEXT,
            meet_time TEXT,
            clue TEXT,
            timestamp REAL,
            expiry REAL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # --------------------------------------------------
    # REQUESTS
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            status TEXT CHECK(status IN ('pending','accepted','declined'))
                   DEFAULT 'pending',
            created_at REAL,
            FOREIGN KEY(sender_id) REFERENCES users(id),
            FOREIGN KEY(receiver_id) REFERENCES users(id)
        )
    """)

    # --------------------------------------------------
    # MATCHES
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            created_at REAL,
            ended_at REAL,
            status TEXT CHECK(status IN ('active','ended'))
                   DEFAULT 'active',
            FOREIGN KEY(user1_id) REFERENCES users(id),
            FOREIGN KEY(user2_id) REFERENCES users(id)
        )
    """)

    # --------------------------------------------------
    # REVIEWS (🔥 FIXED)
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reviewer_id INTEGER NOT NULL,
            reviewed_id INTEGER NOT NULL,
            rating INTEGER CHECK(rating BETWEEN 1 AND 10),
            comment TEXT,
            created_at REAL,
            FOREIGN KEY(reviewer_id) REFERENCES users(id),
            FOREIGN KEY(reviewed_id) REFERENCES users(id)
        )
    """)

    # Indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_reviews_reviewed ON reviews(reviewed_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_requests_receiver ON requests(receiver_id)")

    conn.commit()
    conn.close()

# ======================================================
# CLI
# ======================================================
@click.command("init-db")
def init_db_command():
    init_db()
    click.echo(f"Initialized database at {_get_db_path()}")

def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)

# ======================================================
# MANUAL RUN
# ======================================================
if __name__ == "__main__":
    init_db()
    print("Database initialized at:", _get_db_path())

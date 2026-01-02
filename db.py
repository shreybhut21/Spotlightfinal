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
            trust_score INTEGER DEFAULT 100,
            avatar_level INTEGER DEFAULT 1,
            vibe_tags TEXT,
            avatar_url TEXT,
            is_active INTEGER DEFAULT 1,
            created_at REAL
        )
    """)

    user_cols = [r["name"] for r in c.execute("PRAGMA table_info(users)")]

    if "is_matched" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN is_matched INTEGER DEFAULT 0")

    if "matched_with" not in user_cols:
        c.execute("ALTER TABLE users ADD COLUMN matched_with INTEGER")

    # --------------------------------------------------
    # SPOTLIGHTS (LIVE USERS)
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
    # REQUESTS (AUTO-FIX LEGACY SCHEMA)
    # --------------------------------------------------
    try:
        existing_req_cols = [r["name"] for r in c.execute("PRAGMA table_info(requests)")]
    except sqlite3.OperationalError:
        existing_req_cols = []

    if "spotlight_id" in existing_req_cols:
        # 🔥 Legacy broken table → rebuild clean
        c.execute("ALTER TABLE requests RENAME TO _requests_old")

        c.execute("""
            CREATE TABLE requests (
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

        c.execute("""
            INSERT INTO requests (id, sender_id, receiver_id, status, created_at)
            SELECT id, sender_id, receiver_id, status, created_at
            FROM _requests_old
        """)

        c.execute("DROP TABLE _requests_old")

    else:
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
    # MATCHES (ACTIVE / ENDED)
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
    # REVIEWS / FEEDBACK (⭐ OUT OF 10)
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            reviewed_id INTEGER NOT NULL,
            rating INTEGER CHECK(rating BETWEEN 1 AND 10),
            comment TEXT,
            created_at REAL,
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(reviewer_id) REFERENCES users(id),
            FOREIGN KEY(reviewed_id) REFERENCES users(id),
            UNIQUE(match_id, reviewer_id)
        )
    """)

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

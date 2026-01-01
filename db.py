import sqlite3
import time
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
# INIT DATABASE (NON-DESTRUCTIVE)
# ======================================================
def init_db():
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
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

            -- MATCH MODE
            is_matched INTEGER DEFAULT 0,
            matched_with INTEGER,

            created_at REAL
        )
    """)

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
    # REQUESTS (JOIN REQUESTS)
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
    # MATCHES (HISTORY + FUTURE CHAT LINK)
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
    # MESSAGES (FOR FUTURE CHAT)
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            match_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,

            message TEXT NOT NULL,
            created_at REAL,

            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(sender_id) REFERENCES users(id)
        )
    """)

    # --------------------------------------------------
    # REVIEWS / TRUST EVENTS (FUTURE)
    # --------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            reviewer_id INTEGER NOT NULL,
            reviewed_id INTEGER NOT NULL,

            score INTEGER,
            comment TEXT,

            created_at REAL,

            FOREIGN KEY(reviewer_id) REFERENCES users(id),
            FOREIGN KEY(reviewed_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()

# ======================================================
# CLI COMMAND
# ======================================================
@click.command("init-db")
def init_db_command():
    init_db()
    click.echo(f"Initialized the database at {_get_db_path()}")

# ======================================================
# APP HOOK
# ======================================================
def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)

# ======================================================
# MANUAL RUN
# ======================================================
if __name__ == "__main__":
    init_db()
    print("Database initialized at:", _get_db_path())

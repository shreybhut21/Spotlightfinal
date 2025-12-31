import sqlite3
import time
import os
import click
from flask import g

# ----------------------------
# DB PATH
# ----------------------------
def _get_db_path():
    return os.path.join(os.path.dirname(__file__), 'database.db')

# ----------------------------
# CONNECTION
# ----------------------------
def get_db_connection():
    db = getattr(g, '_database', None)
    if db is None:
        db = sqlite3.connect(_get_db_path(), check_same_thread=False)
        db.row_factory = sqlite3.Row
        g._database = db
    return db

def close_db(e=None):
    db = g.pop('_database', None)
    if db is not None:
        db.close()


# ----------------------------
# INIT DB (SAFE)
# ----------------------------
def init_db():
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # USERS
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

    # SPOTLIGHTS (NO DROP ❌)
    c.execute("""
        CREATE TABLE IF NOT EXISTS spotlights (
            user_id INTEGER,
            lat REAL,
            lon REAL,
            place TEXT,
            intent TEXT,
            meet_time TEXT,
            clue TEXT,
            timestamp REAL,
            expiry REAL
        )
    """)

    # REQUESTS (NO DROP ❌)
    c.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            status TEXT CHECK(status IN ('pending','accepted','declined')),
            created_at REAL
        )
    """)

    conn.commit()
    conn.close()

# ----------------------------
# CLI COMMAND
# ----------------------------
@click.command('init-db')
def init_db_command():
    init_db()
    click.echo(f'Initialized the database at {_get_db_path()}')

# ----------------------------
# APP HOOK
# ----------------------------
def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)

# ----------------------------
# MANUAL RUN
# ----------------------------
if __name__ == '__main__':
    init_db()
    print('Initialized the database at', _get_db_path())

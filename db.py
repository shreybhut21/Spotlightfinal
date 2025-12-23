import sqlite3
import time
import os
import click
from flask import current_app, g


def _get_db_path():
    """Return the path to the SQLite database file next to this module."""
    return os.path.join(os.path.dirname(__file__), 'database.db')


def get_db_connection():
    """Return a sqlite3 connection bound to Flask's `g` so it can be reused per-request.

    Usage within Flask request context:
        conn = get_db_connection()
        cur = conn.cursor()
        ...
    """
    db = getattr(g, '_database', None)
    if db is None:
        db = sqlite3.connect(_get_db_path())
        db.row_factory = sqlite3.Row
        g._database = db
    return db


def close_db(e=None):
    """Close the database connection if it exists in `g`.

    Intended to be registered with `app.teardown_appcontext`.
    """
    db = g.pop('_database', None)
    if db is not None:
        db.close()


def init_db():
    """Create the schema if it doesn't exist."""
    db_path = _get_db_path()
    # Ensure parent directory exists (usually the module folder)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Users table: Stores profile, avatar, trust score
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL,
                  gender TEXT CHECK(gender IN ('Male','Female','Other')) NOT NULL,
                  trust_score INTEGER DEFAULT 100,
                  avatar_level INTEGER DEFAULT 1,
                  vibe_tags TEXT,
                  is_active INTEGER DEFAULT 1,
                  created_at REAL)''')

    # Active Spotlights: Stores location and expiration time
    c.execute('DROP TABLE IF EXISTS spotlights')
    c.execute('''CREATE TABLE IF NOT EXISTS spotlights (
                  user_id INTEGER,
                  lat REAL,
                  lon REAL,
                  place TEXT,
                  intent TEXT,
                  meet_time TEXT,
                  clue TEXT,
                  timestamp REAL,
                  expiry REAL)''')

    # Requests: Handles the "handshake" logic
    c.execute('DROP TABLE IF EXISTS requests')
    c.execute('''CREATE TABLE requests
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  sender_id INTEGER NOT NULL,
                  receiver_id INTEGER NOT NULL,
                  spotlight_id INTEGER NOT NULL,
                  status TEXT CHECK(status IN ('pending','accepted','declined')),
                  arrival_deadline REAL,
                  created_at REAL,
                  FOREIGN KEY(sender_id) REFERENCES users(id),
                  FOREIGN KEY(receiver_id) REFERENCES users(id),
                  FOREIGN KEY(spotlight_id) REFERENCES spotlights(id))''')

    conn.commit()
    conn.close()

    # Add missing columns if an older DB exists without them
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("PRAGMA table_info(users)")
    cols = {r[1] for r in c.fetchall()}  # column names
    if 'password_hash' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if 'gender' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN gender TEXT")
    if 'trust_score' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN trust_score INTEGER DEFAULT 100")
    if 'avatar_level' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN avatar_level INTEGER DEFAULT 1")
    if 'is_active' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    if 'created_at' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN created_at REAL")
    conn.commit()
    conn.close()


@click.command('init-db')
def init_db_command():
    """CLI command to initialize the database file and schema."""
    init_db()
    click.echo(f'Initialized the database at {_get_db_path()}')


def init_app(app):
    """Register database helpers on the given Flask app.

    Call this from your application factory or at app setup time:
        from db import init_app
        init_app(app)
    """
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)


if __name__ == '__main__':
    # Allow quick initialization by running `python db.py` inside the venv
    init_db()
    print('Initialized the database at', _get_db_path())
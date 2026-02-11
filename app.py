"""
Vercel Flask entrypoint.

It re-exports the main `app` instance from `spotlight_app/app.py` so Vercel's
auto-detection (which looks for app.py/index.py in the repo root) can find it.
"""

from spotlight_app.app import app

# Optional alias some platforms expect
application = app

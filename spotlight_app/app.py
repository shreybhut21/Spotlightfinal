import time
import os
import logging
import re
import json
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io
from dotenv import load_dotenv
try:
    from authlib.integrations.flask_client import OAuth
except ImportError:
    OAuth = None  # type: ignore

try:
    from . import db
except ImportError:  # allow running as standalone script
    import db  # type: ignore

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None  # type: ignore
    WebPushException = Exception  # type: ignore

# Load environment variables from the project-root .env file.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT_DOTENV = os.path.join(PROJECT_ROOT, ".env")
if os.path.exists(ROOT_DOTENV):
    load_dotenv(ROOT_DOTENV)
else:
    load_dotenv()

# ======================================================
# APP SETUP
# ======================================================
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static"
)

app.secret_key = os.environ.get("SPOTLIGHT_SECRET_KEY", "spotlight_secret_key")
app.logger.setLevel(logging.DEBUG)
if app.secret_key == "spotlight_secret_key":
    app.logger.warning("Using default secret key; set SPOTLIGHT_SECRET_KEY in env for security.")

# Optional Google OAuth setup
oauth = OAuth(app) if OAuth else None
google_oauth = None
if oauth:
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if google_client_id and google_client_secret:
        google_oauth = oauth.register(
            name="google",
            client_id=google_client_id,
            client_secret=google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    else:
        app.logger.warning("Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
else:
    app.logger.warning("Authlib not installed. Google OAuth is disabled.")

# ======================================================
# DB INIT
# ======================================================
db.init_app(app)
try:
    db.init_db()
except Exception:
    pass


def _build_unique_username(conn, preferred: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "", preferred or "")[:20]
    if not base:
        base = "user"

    candidate = base
    suffix = 1
    while conn.execute("SELECT 1 FROM users WHERE username=?", (candidate,)).fetchone():
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def _is_profile_complete(user) -> bool:
    if not user:
        return False

    required_fields = ("gender", "dob", "phone", "bio", "vibe_tags")
    for field in required_fields:
        value = user[field] if field in user.keys() else None
        if not str(value or "").strip():
            return False
    return True


REQUEST_PENDING_TTL_SECONDS = 60 * 60  # 1 hour
MAX_PROFILE_VIBES = 5
PROFILE_VIBE_OPTIONS = [
    ("Chill", "Chill"),
    ("DeepTalks", "Deep Talks"),
    ("Exploring", "Exploring"),
    ("Drinks", "Drinks"),
    ("Coffee", "Coffee"),
    ("Foodie", "Foodie"),
    ("Fitness", "Fitness"),
    ("Movies", "Movies"),
    ("Music", "Music"),
    ("Gaming", "Gaming"),
    ("Books", "Books"),
    ("Networking", "Networking"),
]
PROFILE_VIBE_ALLOWED = {value for value, _ in PROFILE_VIBE_OPTIONS}


def _expire_stale_pending_requests(conn) -> None:
    cutoff = time.time() - REQUEST_PENDING_TTL_SECONDS
    conn.execute(
        """
        DELETE FROM requests
        WHERE status='pending' AND created_at < ?
        """,
        (cutoff,),
    )


def _push_config():
    return {
        "public_key": (os.environ.get("VAPID_PUBLIC_KEY") or "").strip(),
        "private_key": (os.environ.get("VAPID_PRIVATE_KEY") or "").strip(),
        "subject": (os.environ.get("VAPID_SUBJECT") or "mailto:admin@example.com").strip(),
    }


def _push_ready() -> bool:
    cfg = _push_config()
    return bool(webpush and cfg["public_key"] and cfg["private_key"])

# ======================================================
# AUTH / PAGES
# ======================================================
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/policy")
def policy():
    return render_template("policy.html")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")

@app.route("/auth")
def auth():
    return render_template("auth.html")


@app.route("/auth/google")
def auth_google():
    if not google_oauth:
        return render_template("auth.html", error="Google login is not configured yet.")
    redirect_uri = url_for("auth_google_callback", _external=True)
    return google_oauth.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    if not google_oauth:
        return render_template("auth.html", error="Google login is not configured yet.")

    try:
        token = google_oauth.authorize_access_token()
        userinfo = token.get("userinfo")
        if not userinfo:
            userinfo = google_oauth.parse_id_token(token)
    except Exception:
        app.logger.exception("Google OAuth callback failed")
        return render_template("auth.html", error="Google login failed. Try again.")

    email = (userinfo or {}).get("email")
    if not email:
        return render_template("auth.html", error="Google account email is unavailable.")
    email = email.strip().lower()

    preferred_name = (userinfo or {}).get("name") or email.split("@")[0]
    avatar_url = (userinfo or {}).get("picture")

    conn = db.get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

    if not user:
        username = _build_unique_username(conn, preferred_name)
        pwd_hash = generate_password_hash(os.urandom(16).hex())
        conn.execute(
            """
            INSERT INTO users
            (username, email, password_hash, gender, dob, bio, vibe_tags, phone,
             trust_score, is_matched, matched_with, is_active, avatar_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 100, 0, NULL, 1, ?, ?)
            """,
            (
                username,
                email,
                pwd_hash,
                "",
                "",
                "",
                "",
                "",
                avatar_url,
                time.time(),
            ),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    elif avatar_url and avatar_url != user["avatar_url"]:
        conn.execute("UPDATE users SET avatar_url=? WHERE id=?", (avatar_url, user["id"]))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()

    session["user_id"] = user["id"]
    if not _is_profile_complete(user):
        session["needs_profile_completion"] = True
        return redirect(url_for("complete_profile"))

    session.pop("needs_profile_completion", None)
    return redirect(url_for("index_html"))


@app.route("/auth/complete-profile", methods=["GET", "POST"])
def complete_profile():
    if "user_id" not in session:
        return redirect("/auth")

    conn = db.get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user:
        session.clear()
        return redirect("/auth")

    def render_profile(error=None, selected_vibes=None, form_data=None):
        selected = selected_vibes if selected_vibes is not None else [v for v in (user["vibe_tags"] or "").split(",") if v]
        data = form_data or {
            "username": user["username"] or "",
            "gender": user["gender"] or "",
            "dob": user["dob"] or "",
            "phone": user["phone"] or "",
            "bio": user["bio"] or "",
        }
        return render_template(
            "complete_profile.html",
            user=user,
            selected_vibes=selected,
            form_data=data,
            error=error,
            max_vibes=MAX_PROFILE_VIBES,
            vibe_options=PROFILE_VIBE_OPTIONS,
        )

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        gender = (request.form.get("gender") or "").strip()
        dob = (request.form.get("dob") or "").strip()
        bio = (request.form.get("bio") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        vibes = [v for v in request.form.getlist("vibes") if v in PROFILE_VIBE_ALLOWED]
        vibes = list(dict.fromkeys(vibes))
        vibe_tags = ",".join(vibes)
        form_data = {
            "username": username,
            "gender": gender,
            "dob": dob,
            "phone": phone,
            "bio": bio,
        }

        if not username:
            return render_profile("Username is required.", selected_vibes=vibes, form_data=form_data)
        if not gender:
            return render_profile("Gender is required.", selected_vibes=vibes, form_data=form_data)
        if not dob:
            return render_profile("Birth date is required.", selected_vibes=vibes, form_data=form_data)
        if not re.match(r"^\+?[0-9\-\s]{7,20}$", phone):
            return render_profile("Enter a valid phone number.", selected_vibes=vibes, form_data=form_data)
        if len(bio) > 280:
            return render_profile("Bio must be 280 characters or less.", selected_vibes=vibes, form_data=form_data)
        if len(vibes) > MAX_PROFILE_VIBES:
            return render_profile(f"Choose up to {MAX_PROFILE_VIBES} vibes.", selected_vibes=vibes, form_data=form_data)
        if not vibe_tags:
            return render_profile("Select at least one vibe.", selected_vibes=vibes, form_data=form_data)

        try:
            y, m, d = map(int, dob.split("-"))
            today = date.today()
            age = today.year - y - ((today.month, today.day) < (m, d))
        except Exception:
            return render_profile("Enter a valid birth date.", selected_vibes=vibes, form_data=form_data)

        if age < 18:
            return render_profile("You must be 18+ to join Spotlight.", selected_vibes=vibes, form_data=form_data)

        exists = conn.execute(
            "SELECT id FROM users WHERE username=? AND id<>?",
            (username, user["id"]),
        ).fetchone()
        if exists:
            return render_profile("Username already exists.", selected_vibes=vibes, form_data=form_data)

        conn.execute(
            """
            UPDATE users
            SET username=?, gender=?, dob=?, bio=?, vibe_tags=?, phone=?
            WHERE id=?
            """,
            (username, gender, dob, bio, vibe_tags, phone, user["id"]),
        )
        conn.commit()
        session.pop("needs_profile_completion", None)
        return redirect(url_for("index_html"))

    return render_profile()


@app.route("/api/username_available")
def username_available():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"available": False, "reason": "missing_username"}), 400

    conn = db.get_db_connection()
    exists = conn.execute(
        "SELECT id FROM users WHERE username=? AND id<>?",
        (username, session["user_id"]),
    ).fetchone()
    return jsonify({"available": not bool(exists)})

@app.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password")

    if not username:
        return render_template("auth.html", error="Username is required.")
    if not password:
        return render_template("auth.html", error="Password is required.")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

    # guard against missing password hashes or empty input
    if not user:
        return render_template("auth.html", error="Username not found.")

    # allow legacy accounts with null/empty hash to continue (no-password fallback)
    if not user["password_hash"]:
        session["user_id"] = user["id"]
        return redirect(url_for("index_html"))

    if not check_password_hash(user["password_hash"], password):
        return render_template("auth.html", error="Incorrect password.")

    session["user_id"] = user["id"]
    return redirect(url_for("index_html"))

@app.route("/signup", methods=["POST"])
def signup():
    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password")
    phone = (request.form.get("phone") or "").strip()

    conn = db.get_db_connection()

    # basic validation
    if not username or not password:
        return render_template("auth.html", error="Username and password are required.", show_signup=True)

    if not email:
        return render_template("auth.html", error="Email is required.", show_signup=True)

    if not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email):
        return render_template("auth.html", error="Enter a valid email address.", show_signup=True)

    if len(password) < 6:
        return render_template("auth.html", error="Password must be at least 6 characters.", show_signup=True)

    if not re.match(r"^\+?[0-9\-\s]{7,20}$", phone):
        return render_template("auth.html", error="Enter a valid phone number.", show_signup=True)

    exists = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()

    if exists:
        return render_template("auth.html", error="Username already exists.", show_signup=True)

    email_exists = conn.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
    if email_exists:
        return render_template("auth.html", error="Email is already registered.", show_signup=True)

    pwd_hash = generate_password_hash(password)

    conn.execute("""
        INSERT INTO users
        (username, email, password_hash, gender, dob, bio, vibe_tags, phone,
         trust_score, is_matched, matched_with, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 100, 0, NULL, 1, ?)
    """, (
        username,
        email,
        pwd_hash,
        "",
        "",
        "",
        "",
        phone,
        time.time()
    ))

    conn.commit()

    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    session["user_id"] = user["id"]
    session["needs_profile_completion"] = True
    return redirect(url_for("complete_profile"))



@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

from datetime import date

@app.route("/settings")
def settings():
    if "user_id" not in session:
        return redirect("/auth")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()

    # calculate age from dob
    age = None
    if user["dob"]:
        y, m, d = map(int, user["dob"].split("-"))
        today = date.today()
        age = today.year - y - ((today.month, today.day) < (m, d))

    vibes = [v for v in (user["vibe_tags"] or "").split(",") if v]

    return render_template(
        "settings.html",
        user=user,
        age=age,
        vibes=vibes,
        max_vibes=MAX_PROFILE_VIBES,
        vibe_options=PROFILE_VIBE_OPTIONS,
    )


@app.route("/profile/<int:user_id>")
def public_profile(user_id):
    if "user_id" not in session:
        return redirect("/auth")

    conn = db.get_db_connection()
    user = conn.execute(
        """
        SELECT id, username, gender, dob, phone, bio, vibe_tags, trust_score
        FROM users
        WHERE id=?
        """,
        (user_id,),
    ).fetchone()

    if not user:
        return redirect("/index.html")

    incoming_request_id = None
    request_id = request.args.get("request_id", type=int)
    if request_id:
        req = conn.execute(
            """
            SELECT id
            FROM requests
            WHERE id=?
              AND sender_id=?
              AND receiver_id=?
              AND status='pending'
            """,
            (request_id, user_id, session["user_id"]),
        ).fetchone()
        if req:
            incoming_request_id = req["id"]

    age = None
    if user["dob"]:
        try:
            y, m, d = map(int, user["dob"].split("-"))
            today = date.today()
            age = today.year - y - ((today.month, today.day) < (m, d))
        except Exception:
            age = None

    vibes = [v for v in (user["vibe_tags"] or "").split(",") if v]
    return render_template(
        "public_profile.html",
        user=user,
        age=age,
        vibes=vibes,
        incoming_request_id=incoming_request_id,
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Minimal admin login with fixed credentials (admin/admin)."""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == "admin" and password == "admin":
            session["is_admin"] = True
            return redirect("/admin")
        return render_template("admin_login.html", error="Invalid admin credentials")

    return render_template("admin_login.html")


@app.route("/admin")
def admin():
    """
    Lightweight admin dashboard showing high-level counts.
    Requires admin session.
    """
    if not session.get("is_admin"):
        return redirect("/admin/login")

    conn = db.get_db_connection()
    now = time.time()

    total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    active_spotlights = conn.execute(
        "SELECT COUNT(*) AS c FROM spotlights WHERE expiry > ?", (now,)
    ).fetchone()["c"]
    active_matches = conn.execute(
        "SELECT COUNT(*) AS c FROM matches WHERE status='active'"
    ).fetchone()["c"]
    total_reviews = conn.execute(
        "SELECT COUNT(*) AS c FROM reviews"
    ).fetchone()["c"]
    avg_trust = conn.execute(
        "SELECT AVG(trust_score) AS a FROM users"
    ).fetchone()["a"]
    push_subscribers = conn.execute(
        "SELECT COUNT(DISTINCT user_id) AS c FROM push_subscriptions"
    ).fetchone()["c"]

    users = conn.execute(
        "SELECT id, username, trust_score, is_active, created_at FROM users ORDER BY id ASC"
    ).fetchall()

    return render_template(
        "admin.html",
        stats={
            "total_users": total_users,
            "active_spotlights": active_spotlights,
            "active_matches": active_matches,
            "total_reviews": total_reviews,
            "avg_trust": round(avg_trust, 1) if avg_trust is not None else None,
            "push_subscribers": push_subscribers,
        },
        users=users,
        push_ready=_push_ready(),
    )


@app.route("/admin/reports")
def admin_reports():
    """Reports inbox for user reports and app feedback."""
    if not session.get("is_admin"):
        return redirect("/admin/login")

    conn = db.get_db_connection()
    rows = conn.execute("""
        SELECT r.*, ru.username AS reporter_name, tu.username AS target_name
        FROM reports r
        LEFT JOIN users ru ON ru.id = r.reporter_id
        LEFT JOIN users tu ON tu.id = r.target_user_id
        ORDER BY r.created_at DESC
    """).fetchall()

    reports = [
        {
            "id": r["id"],
            "type": r["type"],
            "message": r["message"],
            "status": r["status"],
            "created_at": r["created_at"],
            "reporter": r["reporter_name"],
            "target": r["target_name"],
        }
        for r in rows
    ]

    return render_template("admin_reports.html", reports=reports)


@app.route("/admin/reports/export")
def admin_reports_export():
    """Export all reports as CSV."""
    if not session.get("is_admin"):
        return redirect("/admin/login")

    conn = db.get_db_connection()
    rows = conn.execute("""
        SELECT r.id, r.type, r.status, r.message, r.created_at,
               ru.username AS reporter, tu.username AS target
        FROM reports r
        LEFT JOIN users ru ON ru.id = r.reporter_id
        LEFT JOIN users tu ON tu.id = r.target_user_id
        ORDER BY r.created_at DESC
    """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "type", "status", "reporter", "target", "message", "created_at"])
    for r in rows:
        writer.writerow([r["id"], r["type"], r["status"], r["reporter"], r["target"], r["message"], int(r["created_at"])])

    resp = app.response_class(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=reports.csv"}
    )
    return resp


@app.route("/admin/reports/delete", methods=["POST"])
def admin_reports_delete():
    """Delete a report by ID."""
    if not session.get("is_admin"):
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    try:
        rid = int(data.get("report_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_id"}), 400

    conn = db.get_db_connection()
    cur = conn.execute("DELETE FROM reports WHERE id=?", (rid,))
    conn.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "deleted", "id": rid})


@app.route("/admin/toggle_user", methods=["POST"])
def admin_toggle_user():
    """Block or unblock a user (sets is_active flag)."""
    if not session.get("is_admin"):
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    target_id = data.get("target_id")
    action = data.get("action")

    if action not in ("block", "unblock"):
        return jsonify({"error": "invalid_action"}), 400
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_user"}), 400

    conn = db.get_db_connection()
    is_active = 0 if action == "block" else 1
    conn.execute("UPDATE users SET is_active=? WHERE id=?", (is_active, target_id))
    if action == "block":
        # remove from map visibility immediately
        conn.execute("DELETE FROM spotlights WHERE user_id=?", (target_id,))
    conn.commit()

    return jsonify({"status": "ok", "is_active": is_active})


@app.route("/admin/delete_user", methods=["POST"])
def admin_delete_user():
    """Delete a user and their related records."""
    if not session.get("is_admin"):
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    try:
        target_id = int(data.get("target_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_user"}), 400

    conn = db.get_db_connection()
    # clean dependent data (no foreign key cascades)
    conn.execute("DELETE FROM spotlights WHERE user_id=?", (target_id,))
    conn.execute("DELETE FROM requests WHERE sender_id=? OR receiver_id=?", (target_id, target_id))
    conn.execute("DELETE FROM matches WHERE user1_id=? OR user2_id=?", (target_id, target_id))
    conn.execute("DELETE FROM reviews WHERE reviewer_id=? OR reviewed_id=?", (target_id, target_id))
    conn.execute("DELETE FROM push_subscriptions WHERE user_id=?", (target_id,))
    conn.execute("DELETE FROM users WHERE id=?", (target_id,))
    conn.commit()

    return jsonify({"status": "deleted"})


@app.route("/api/push/public_key")
def push_public_key():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    cfg = _push_config()
    if not cfg["public_key"]:
        return jsonify({"error": "push_not_configured"}), 503

    return jsonify({"public_key": cfg["public_key"]})


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.json or {}
    endpoint = (payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "invalid_subscription"}), 400

    ua = (request.headers.get("User-Agent") or "")[:255]
    now = time.time()
    conn = db.get_db_connection()
    conn.execute(
        """
        INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            user_id=excluded.user_id,
            p256dh=excluded.p256dh,
            auth=excluded.auth,
            user_agent=excluded.user_agent,
            updated_at=excluded.updated_at
        """,
        (session["user_id"], endpoint, p256dh, auth, ua, now, now),
    )
    conn.commit()
    return jsonify({"status": "saved"})


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.json or {}
    endpoint = (payload.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "invalid_subscription"}), 400

    conn = db.get_db_connection()
    conn.execute(
        "DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?",
        (session["user_id"], endpoint),
    )
    conn.commit()
    return jsonify({"status": "removed"})


@app.route("/admin/push/send", methods=["POST"])
def admin_push_send():
    if not session.get("is_admin"):
        return jsonify({"error": "unauthorized"}), 401
    if not _push_ready():
        return jsonify({"error": "push_not_configured"}), 503

    payload = request.json or {}
    title = (payload.get("title") or "").strip()
    message = (payload.get("message") or "").strip()
    target_type = (payload.get("target_type") or "all").strip()

    if not title or not message:
        return jsonify({"error": "title_and_message_required"}), 400
    if len(title) > 80 or len(message) > 240:
        return jsonify({"error": "content_too_long"}), 400

    conn = db.get_db_connection()
    target_user_ids = []
    if target_type == "single":
        try:
            target_user_id = int(payload.get("target_user_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_target_user"}), 400
        target_row = conn.execute(
            "SELECT id FROM users WHERE id=? AND is_active=1",
            (target_user_id,),
        ).fetchone()
        if target_row:
            target_user_ids = [target_row["id"]]
    elif target_type != "all":
        return jsonify({"error": "invalid_target_type"}), 400
    else:
        target_user_ids = [
            r["id"]
            for r in conn.execute("SELECT id FROM users WHERE is_active=1").fetchall()
        ]

    if not target_user_ids:
        return jsonify(
            {
                "status": "sent",
                "targeted_users": 0,
                "targeted_subscriptions": 0,
                "targeted": 0,
                "sent_count": 0,
                "failed_count": 0,
                "removed_subscriptions": 0,
            }
        )

    placeholders = ",".join(["?"] * len(target_user_ids))
    rows = conn.execute(
        f"""
        SELECT id, endpoint, p256dh, auth, user_id
        FROM push_subscriptions
        WHERE user_id IN ({placeholders})
        """,
        tuple(target_user_ids),
    ).fetchall()

    now = time.time()
    conn.executemany(
        """
        INSERT INTO app_notifications (user_id, title, message, kind, created_at, seen_at)
        VALUES (?, ?, ?, 'admin_push', ?, NULL)
        """,
        [(uid, title, message, now) for uid in target_user_ids],
    )

    cfg = _push_config()
    vapid_claims = {"sub": cfg["subject"]}
    push_payload = json.dumps(
        {"title": title, "message": message, "kind": "admin_push", "sent_at": int(now)}
    )

    sent = 0
    failed = 0
    removed = 0

    for row in rows:
        subscription = {
            "endpoint": row["endpoint"],
            "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
        }

        try:
            webpush(  # type: ignore[misc]
                subscription_info=subscription,
                data=push_payload,
                vapid_private_key=cfg["private_key"],
                vapid_claims=vapid_claims,
            )
            sent += 1
            conn.execute(
                "UPDATE push_subscriptions SET last_sent_at=?, updated_at=? WHERE id=?",
                (time.time(), time.time(), row["id"]),
            )
        except WebPushException as exc:  # type: ignore[misc]
            failed += 1
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            # Subscription is expired or invalid; prune it.
            if status_code in (404, 410):
                conn.execute("DELETE FROM push_subscriptions WHERE id=?", (row["id"],))
                removed += 1
        except Exception:
            failed += 1

    conn.commit()

    return jsonify(
        {
            "status": "sent",
            "targeted_users": len(target_user_ids),
            "targeted_subscriptions": len(rows),
            "targeted": len(rows),
            "sent_count": sent,
            "failed_count": failed,
            "removed_subscriptions": removed,
        }
    )


@app.route("/api/notifications")
def api_notifications():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    uid = session["user_id"]
    conn = db.get_db_connection()
    rows = conn.execute(
        """
        SELECT id, title, message, kind, created_at
        FROM app_notifications
        WHERE user_id=? AND seen_at IS NULL
        ORDER BY created_at DESC
        LIMIT 25
        """,
        (uid,),
    ).fetchall()

    if rows:
        ids = [r["id"] for r in rows]
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"UPDATE app_notifications SET seen_at=? WHERE id IN ({placeholders})",
            (time.time(), *ids),
        )
        conn.commit()

    return jsonify(
        {
            "notifications": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "message": r["message"],
                    "kind": r["kind"] or "admin_push",
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        }
    )


# ======================================================
# API – REPORTS
# ======================================================
@app.route("/api/report_user", methods=["POST"])
def report_user():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    target_id = data.get("target_id")
    message = data.get("message", "").strip()[:500]

    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_target"}), 400

    conn = db.get_db_connection()
    conn.execute("""
        INSERT INTO reports (reporter_id, target_user_id, type, message, status, created_at)
        VALUES (?, ?, 'user', ?, 'open', ?)
    """, (session["user_id"], target_id, message, time.time()))
    conn.commit()
    return jsonify({"status": "reported"})


@app.route("/api/report_app", methods=["POST"])
def report_app():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    message = data.get("message", "").strip()[:500]
    if not message:
        return jsonify({"error": "empty_message"}), 400

    conn = db.get_db_connection()
    conn.execute("""
        INSERT INTO reports (reporter_id, type, message, status, created_at)
        VALUES (?, 'app', ?, 'open', ?)
    """, (session["user_id"], message, time.time()))
    conn.commit()
    return jsonify({"status": "reported"})


@app.route("/index.html")
def index_html():
    user = None
    if "user_id" in session:
        conn = db.get_db_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if user and not _is_profile_complete(user):
            session["needs_profile_completion"] = True
            return redirect(url_for("complete_profile"))

    # fallback guest user so template has fields
    if not user:
        user = {
            "id": None,
            "username": "Guest",
            "avatar_url": "https://ui-avatars.com/api/?name=G",
            "trust_score": None,
        }

    return render_template("index.html", user=user)

# ======================================================
# API – USER INFO (🔥 KEY FOR MATCH MODE)
# ======================================================
@app.route("/api/user_info")
def user_info():
    if "user_id" not in session:
        return jsonify({}), 401

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT trust_score, is_matched, matched_with FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    return jsonify(dict(user))

# ======================================================
# API – SEND REQUEST
# ======================================================
@app.route("/api/send_request", methods=["POST"])
def send_request():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "invalid_json"}), 400

    sender_id = session["user_id"]
    receiver_id = data.get("receiver_id")

    if not receiver_id:
        return jsonify({"error": "missing_receiver_id"}), 400

    if sender_id == receiver_id:
        return jsonify({"error": "invalid"}), 400

    conn = db.get_db_connection()

    try:
        _expire_stale_pending_requests(conn)

        # block if either already matched
        rows = conn.execute(
            "SELECT is_matched FROM users WHERE id IN (?, ?)",
            (sender_id, receiver_id)
        ).fetchall()

        if any(r["is_matched"] for r in rows):
            return jsonify({"error": "already_matched"}), 409

        existing = conn.execute(
            """
            SELECT id FROM requests
            WHERE sender_id=? AND receiver_id=? AND status='pending'
            """,
            (sender_id, receiver_id)
        ).fetchone()

        if existing:
            return jsonify({"status": "already_sent"}), 409

        conn.execute(
            """
            INSERT INTO requests (sender_id, receiver_id, status, created_at)
            VALUES (?, ?, 'pending', ?)
            """,
            (sender_id, receiver_id, time.time())
        )
        conn.commit()
    except Exception as e:
        app.logger.error(f"Error in send_request: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "sent"})

# ======================================================
# API – CHECK REQUESTS
# ======================================================
@app.route("/api/check_requests")
def check_requests():
    if "user_id" not in session:
        return jsonify({"type": "none"})

    uid = session["user_id"]
    conn = db.get_db_connection()
    _expire_stale_pending_requests(conn)
    conn.commit()
    now = time.time()

    req = conn.execute(
        """
        SELECT
            r.id,
            r.sender_id,
            u.username,
            u.trust_score,
            u.bio,
            u.vibe_tags,
            s.place,
            s.intent,
            s.meet_time,
            s.clue
        FROM requests r
        JOIN users u ON u.id = r.sender_id
        LEFT JOIN spotlights s
          ON s.user_id = r.sender_id
         AND s.expiry > ?
        WHERE r.receiver_id = ?
          AND r.status = 'pending'
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        (now, uid)
    ).fetchone()

    if req:
        return jsonify({
            "type": "incoming",
            "data": {
                "id": req["id"],
                "sender_id": req["sender_id"],
                "username": req["username"],
                "trust_score": req["trust_score"],
                "bio": req["bio"],
                "vibe_tags": req["vibe_tags"],
                "place": req["place"],
                "intent": req["intent"],
                "meet_time": req["meet_time"],
                "clue": req["clue"],
            }
        })

    return jsonify({"type": "none"})

# ======================================================
# API – RESPOND REQUEST (🔥 SYNC BOTH USERS)
# ======================================================
@app.route("/api/respond_request", methods=["POST"])
def respond_request():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    user_id = session["user_id"]
    data = request.json
    request_id = data.get("request_id")
    action = data.get("action")

    if action not in ("accept", "decline"):
        return jsonify({"error": "invalid"}), 400

    conn = db.get_db_connection()
    _expire_stale_pending_requests(conn)
    conn.commit()

    req = conn.execute(
        """
        SELECT * FROM requests
        WHERE id=? AND receiver_id=? AND status='pending'
        """,
        (request_id, user_id)
    ).fetchone()

    if not req:
        return jsonify({"error": "not_found"}), 404

    sender_id = req["sender_id"]

    # -------- DECLINE --------
    if action == "decline":
        conn.execute(
            "UPDATE requests SET status='declined' WHERE id=?",
            (request_id,)
        )
        conn.commit()
        return jsonify({"status": "declined"})

    # -------- ACCEPT --------
    conn.execute(
        "UPDATE requests SET status='accepted' WHERE id=?",
        (request_id,)
    )

    # 🔥 CREATE MATCH (THIS WAS MISSING)
    conn.execute(
        """
        INSERT INTO matches (user1_id, user2_id, created_at, status)
        VALUES (?, ?, ?, 'active')
        """,
        (sender_id, user_id, time.time())
    )

    # update users state
    conn.execute(
        "UPDATE users SET is_matched=1, matched_with=? WHERE id=?",
        (sender_id, user_id)
    )
    conn.execute(
        "UPDATE users SET is_matched=1, matched_with=? WHERE id=?",
        (user_id, sender_id)
    )

    # remove from live map
    conn.execute(
        "DELETE FROM spotlights WHERE user_id IN (?, ?)",
        (user_id, sender_id)
    )

    # cancel all other pending requests
    conn.execute(
        """
        UPDATE requests
        SET status='declined'
        WHERE status='pending'
        AND (sender_id IN (?, ?) OR receiver_id IN (?, ?))
        """,
        (user_id, sender_id, user_id, sender_id)
    )

    conn.commit()
    return jsonify({"status": "matched"})


# ======================================================
# API – MATCH STATUS
# ======================================================
@app.route("/api/match_status")
def match_status():
    if "user_id" not in session:
        return jsonify({"matched": False})

    uid = session["user_id"]
    conn = db.get_db_connection()
    u = conn.execute(
        "SELECT is_matched, matched_with FROM users WHERE id=?",
        (uid,)
    ).fetchone()

    matched = bool(u["is_matched"])
    if matched:
        other = u["matched_with"]
        m = conn.execute(
            """
            SELECT id, user1_id, user2_id, user1_reached, user2_reached
            FROM matches
            WHERE status='active'
              AND ((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?))
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (uid, other, other, uid)
        ).fetchone()

        if not m:
            return jsonify({"matched": True, "i_reached": False, "other_reached": False})

        if m["user1_id"] == uid:
            i_reached = bool(m["user1_reached"])
            other_reached = bool(m["user2_reached"])
        else:
            i_reached = bool(m["user2_reached"])
            other_reached = bool(m["user1_reached"])

        return jsonify({
            "matched": True,
            "match_id": m["id"],
            "i_reached": i_reached,
            "other_reached": other_reached
        })

    ended = conn.execute(
        """
        SELECT m.id, m.end_reason, m.end_reason_by, u.username AS ended_by_name
        FROM matches m
        LEFT JOIN users u ON u.id = m.end_reason_by
        WHERE (m.user1_id=? OR m.user2_id=?)
          AND m.status='ended'
        ORDER BY m.ended_at DESC
        LIMIT 1
        """,
        (uid, uid)
    ).fetchone()

    if ended and ended["end_reason"] and ended["end_reason_by"] and int(ended["end_reason_by"]) != uid:
        return jsonify({
            "matched": False,
            "match_id": ended["id"],
            "ended_by_other": True,
            "ended_by": ended["ended_by_name"] or "Your match",
            "end_reason": ended["end_reason"]
        })

    return jsonify({"matched": False, "ended_by_other": False})


@app.route("/api/mark_reached", methods=["POST"])
def mark_reached():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    uid = session["user_id"]
    conn = db.get_db_connection()

    user = conn.execute(
        "SELECT is_matched, matched_with FROM users WHERE id=?",
        (uid,)
    ).fetchone()
    if not user or not user["is_matched"] or not user["matched_with"]:
        return jsonify({"error": "no_active_match"}), 400

    other = user["matched_with"]
    m = conn.execute(
        """
        SELECT id, user1_id, user2_id
        FROM matches
        WHERE status='active'
          AND ((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?))
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (uid, other, other, uid)
    ).fetchone()

    if not m:
        return jsonify({"error": "match_not_found"}), 404

    reached_col = "user1_reached" if m["user1_id"] == uid else "user2_reached"
    conn.execute(f"UPDATE matches SET {reached_col}=1 WHERE id=?", (m["id"],))
    conn.commit()
    return jsonify({"status": "ok", "match_id": m["id"]})

# ======================================================
# API – END MATCH
# ======================================================
@app.route("/api/end_match", methods=["POST"])
def end_match():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    uid = session["user_id"]
    payload = request.json or {}
    end_reason = (payload.get("reason") or "").strip()
    conn = db.get_db_connection()

    other = conn.execute(
        "SELECT matched_with FROM users WHERE id=?",
        (uid,)
    ).fetchone()["matched_with"]

    # If the local cache is cleared (e.g., other user already ended),
    # treat this as an idempotent call and return success.
    if other is None:
        existing = conn.execute(
            """
            SELECT id FROM matches
            WHERE (user1_id=? OR user2_id=?)
              AND status='ended'
            ORDER BY ended_at DESC
            LIMIT 1
            """,
            (uid, uid)
        ).fetchone()
        if existing:
            return jsonify({"status": "ended", "note": "already_ended"}), 200
        return jsonify({"error": "no_active_match"}), 400

    active_match = conn.execute(
        """
        SELECT id, user1_id, user2_id, user1_reached, user2_reached
        FROM matches
        WHERE status='active'
          AND ((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?))
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (uid, other, other, uid)
    ).fetchone()

    if active_match:
        my_reached = bool(active_match["user1_reached"]) if active_match["user1_id"] == uid else bool(active_match["user2_reached"])
        if not my_reached:
            if not end_reason:
                return jsonify({"error": "reason_required"}), 400
            if len(end_reason.split()) > 50:
                return jsonify({"error": "reason_too_long"}), 400

    conn.execute(
        "UPDATE users SET is_matched=0, matched_with=NULL WHERE id IN (?, ?)",
        (uid, other)
    )

    cur = conn.execute("""
        UPDATE matches
        SET status='ended', ended_at=?,
            end_reason=CASE WHEN ? <> '' THEN ? ELSE end_reason END,
            end_reason_by=CASE WHEN ? <> '' THEN ? ELSE end_reason_by END
        WHERE status='active'
          AND ((user1_id=? AND user2_id=?)
               OR (user1_id=? AND user2_id=?))
    """, (time.time(), end_reason, end_reason, end_reason, uid, uid, other, other, uid))

    if cur.rowcount == 0:
        # If nothing to update, it may already be ended; respond idempotently.
        existing = conn.execute(
            """
            SELECT id FROM matches
            WHERE (user1_id=? AND user2_id=?)
               OR (user1_id=? AND user2_id=?)
            ORDER BY ended_at DESC
            LIMIT 1
            """,
            (uid, other, other, uid)
        ).fetchone()
        conn.commit()
        if existing:
            return jsonify({"status": "ended", "note": "already_ended"}), 200
        return jsonify({"error": "match_not_found"}), 404

    conn.commit()
    return jsonify({"status": "ended"})

# ======================================================
# API – GET FEEDBACK TARGET (after match)
# ======================================================
@app.route("/api/feedback_target")
def feedback_target():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    uid = session["user_id"]
    conn = db.get_db_connection()

    row = conn.execute("""
        SELECT m.user1_id, m.user2_id
        FROM matches m
        WHERE (m.user1_id=? OR m.user2_id=?)
        AND m.status='ended'
        ORDER BY m.ended_at DESC
        LIMIT 1
    """, (uid, uid)).fetchone()

    if not row:
        return jsonify({"error": "no_match"}), 404

    other_id = row["user2_id"] if row["user1_id"] == uid else row["user1_id"]

    other = conn.execute(
        "SELECT id, username, trust_score FROM users WHERE id=?",
        (other_id,)
    ).fetchone()

    return jsonify(dict(other))


# ======================================================
# TRUST SCORE UTILS
# ======================================================
def rating_to_trust_delta(rating: int) -> int:
    """Map 1-10 rating to trust delta: 1..5 => minus/zero, 6..10 => plus."""
    value = max(1, min(10, int(rating)))
    return value - 5  # 5 => 0, 1 => -4, 10 => +5


def apply_trust_delta(conn, user_id: int, delta: int) -> None:
    """Apply trust delta with guardrails to avoid unbounded growth."""
    if delta == 0:
        return

    row = conn.execute("SELECT trust_score FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return

    current = int(row["trust_score"] or 100)
    updated = current + int(delta)
    # keep trust score in a sane range
    updated = max(50, min(150, updated))
    conn.execute("UPDATE users SET trust_score=? WHERE id=?", (updated, user_id))


# ======================================================
# API – SUBMIT FEEDBACK
# ======================================================
@app.route("/api/submit_feedback", methods=["POST"])
def submit_feedback():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    reviewer_id = session["user_id"]
    reviewed_id = data.get("reviewed_id")
    rating = data.get("rating")
    comment = data.get("comment", "")

    # 🔥 HARD VALIDATION
    try:
        reviewed_id = int(reviewed_id)
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_data"}), 400

    if rating < 1 or rating > 10:
        return jsonify({"error": "invalid_rating"}), 400

    if not reviewed_id:
        return jsonify({"error": "missing_data"}), 400

    conn = db.get_db_connection()

    # If a recent review exists for this pair, update it instead of failing.
    recent = conn.execute(
        """
        SELECT id, rating FROM reviews
        WHERE reviewer_id=? AND reviewed_id=? AND created_at > ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (reviewer_id, reviewed_id, time.time() - 3600)
    ).fetchone()
    if recent:
        conn.execute(
            """
            UPDATE reviews
            SET rating=?, comment=?, created_at=?
            WHERE id=?
            """,
            (rating, comment, time.time(), recent["id"])
        )
        old_delta = rating_to_trust_delta(int(recent["rating"]))
        new_delta = rating_to_trust_delta(rating)
        apply_trust_delta(conn, reviewed_id, new_delta - old_delta)
        conn.commit()
        return jsonify({"status": "submitted", "note": "updated_recent"})

    conn.execute(
        """
        INSERT INTO reviews (reviewer_id, reviewed_id, rating, comment, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (reviewer_id, reviewed_id, rating, comment, time.time())
    )

    apply_trust_delta(conn, reviewed_id, rating_to_trust_delta(rating))
    conn.commit()
    return jsonify({"status": "submitted"})

# ======================================================
# API – VIEW MY FEEDBACK
# ======================================================
@app.route("/api/my_feedback")
def my_feedback():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    uid = session["user_id"]
    conn = db.get_db_connection()
    page = request.args.get("page", type=int)
    per_page = request.args.get("per_page", type=int)
    use_pagination = page is not None or per_page is not None

    if use_pagination:
        page = max(1, page or 1)
        per_page = max(1, min(20, per_page or 5))

    aggregate = conn.execute(
        """
        SELECT COUNT(*) AS c, AVG(rating) AS avg_rating
        FROM reviews
        WHERE reviewed_id=?
        """,
        (uid,),
    ).fetchone()

    total_count = int((aggregate["c"] or 0) if aggregate else 0)
    avg_rating = aggregate["avg_rating"] if aggregate else None

    if total_count == 0:
        payload = {"average": None, "count": 0, "reviews": []}
        if use_pagination:
            payload.update({
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "has_prev": False,
                "has_next": False,
            })
        return jsonify(payload)

    if use_pagination:
        offset = (page - 1) * per_page
        rows = conn.execute(
            """
            SELECT r.rating, r.comment, r.created_at, u.username
            FROM reviews r
            JOIN users u ON u.id = r.reviewer_id
            WHERE r.reviewed_id=?
            ORDER BY r.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (uid, per_page, offset),
        ).fetchall()
        total_pages = (total_count + per_page - 1) // per_page
    else:
        rows = conn.execute(
            """
            SELECT r.rating, r.comment, r.created_at, u.username
            FROM reviews r
            JOIN users u ON u.id = r.reviewer_id
            WHERE r.reviewed_id=?
            ORDER BY r.created_at DESC
            """,
            (uid,),
        ).fetchall()

    payload = {
        "average": round(avg_rating, 1) if avg_rating is not None else None,
        "count": total_count,
        "reviews": [
            {
                "rating": r["rating"],
                "comment": r["comment"],
                "by": r["username"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }

    if use_pagination:
        payload.update({
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        })

    return jsonify(payload)


@app.route("/api/user_feedback/<int:user_id>")
def user_feedback(user_id):
    """Public feedback history for a selected user profile."""
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    conn = db.get_db_connection()

    exists = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if not exists:
        return jsonify({"error": "not_found"}), 404

    rows = conn.execute(
        """
        SELECT r.rating, r.comment, r.created_at, u.username
        FROM reviews r
        JOIN users u ON u.id = r.reviewer_id
        WHERE r.reviewed_id=?
        ORDER BY r.created_at DESC
        """,
        (user_id,)
    ).fetchall()

    return jsonify({
        "count": len(rows),
        "reviews": [
            {
                "rating": r["rating"],
                "comment": r["comment"],
                "by": r["username"],
                "created_at": r["created_at"]
            }
            for r in rows
        ]
    })




# ======================================================
# API – CHECKIN / CHECKOUT
# ======================================================
@app.route("/api/checkin", methods=["POST"])
def checkin():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json
    uid = session["user_id"]
    expiry = time.time() + 90 * 60

    conn = db.get_db_connection()
    conn.execute("DELETE FROM spotlights WHERE user_id=?", (uid,))
    conn.execute(
        """
        INSERT INTO spotlights
        (user_id, lat, lon, place, intent, meet_time, clue, timestamp, expiry)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            data["lat"],
            data["lon"],
            data["place"],
            data["intent"],
            data.get("meet_time"),
            data["clue"],
            time.time(),
            expiry,
        )
    )
    conn.commit()
    return jsonify({"status": "live"})

@app.route("/api/checkout", methods=["POST"])
def checkout():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    conn = db.get_db_connection()
    conn.execute("DELETE FROM spotlights WHERE user_id=?", (session["user_id"],))
    conn.commit()
    return jsonify({"status": "off"})


@app.route("/api/my_live_status")
def my_live_status():
    if "user_id" not in session:
        return jsonify({"live": False}), 401

    uid = session["user_id"]
    conn = db.get_db_connection()
    row = conn.execute(
        """
        SELECT 1
        FROM spotlights
        WHERE user_id=? AND expiry > ?
        LIMIT 1
        """,
        (uid, time.time())
    ).fetchone()

    return jsonify({"live": bool(row)})

# ======================================================
# API – NEARBY USERS
# ======================================================
@app.route("/api/nearby")
def nearby():
    if "user_id" not in session:
        return jsonify([])

    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))
    me = session["user_id"]

    conn = db.get_db_connection()
    rows = conn.execute(
        """
        SELECT s.*, u.username, u.trust_score, u.bio, u.vibe_tags
        FROM spotlights s
        JOIN users u ON u.id = s.user_id
        WHERE s.expiry > ?
          AND s.user_id != ?
          AND u.is_matched = 0
        """,
        (time.time(), me)
    ).fetchall()

    result = []
    for r in rows:
        result.append({
            "id": r["user_id"],
            "lat": r["lat"],
            "lon": r["lon"],
            "username": r["username"],
            "trust_score": r["trust_score"],
            "bio": (r["bio"] or "")[:280],
            "vibe_tags": r["vibe_tags"],
            "place": r["place"],
            "intent": r["intent"],
            "meet_time": r["meet_time"],
            "clue": r["clue"],
        })

    return jsonify(result)


# ======================================================
# API – UPDATE PROFILE FIELDS
# ======================================================
@app.route("/api/update_profile", methods=["POST"])
def update_profile():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    raw_vibes = data.get("vibes") or []
    if not isinstance(raw_vibes, list):
        return jsonify({"error": "invalid_vibes"}), 400

    vibes = [str(v).strip() for v in raw_vibes]
    vibes = [v for v in vibes if v in PROFILE_VIBE_ALLOWED]
    vibes = list(dict.fromkeys(vibes))

    if not vibes:
        return jsonify({"error": "vibes_required"}), 400
    if len(vibes) > MAX_PROFILE_VIBES:
        return jsonify({"error": "too_many_vibes", "max_vibes": MAX_PROFILE_VIBES}), 400

    vibe_tags = ",".join(vibes)
    conn = db.get_db_connection()
    conn.execute(
        "UPDATE users SET vibe_tags=? WHERE id=?",
        (vibe_tags, session["user_id"]),
    )
    conn.commit()

    return jsonify({
        "status": "saved",
        "vibes": vibes,
    })


# ======================================================
# API – UPDATE BIO
# ======================================================
@app.route("/api/update_bio", methods=["POST"])
def update_bio():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    bio = (data.get("bio") or "").strip()

    if len(bio) > 280:
        return jsonify({"error": "too_long"}), 400

    conn = db.get_db_connection()
    conn.execute("UPDATE users SET bio=? WHERE id=?", (bio, session["user_id"]))
    conn.commit()

    return jsonify({"status": "saved", "bio": bio})

# ======================================================
# RUN
# ======================================================
if __name__ == "__main__":
    app.run(debug=True)

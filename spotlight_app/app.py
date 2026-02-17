import time
import os
import logging
import re
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
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

# Load environment variables from .env for local development.
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


def _expire_stale_pending_requests(conn) -> None:
    cutoff = time.time() - REQUEST_PENDING_TTL_SECONDS
    conn.execute(
        """
        DELETE FROM requests
        WHERE status='pending' AND created_at < ?
        """,
        (cutoff,),
    )

# ======================================================
# AUTH / PAGES
# ======================================================
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/policy")
def policy():
    return render_template("policy.html")

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

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        gender = (request.form.get("gender") or "").strip()
        dob = (request.form.get("dob") or "").strip()
        bio = (request.form.get("bio") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        vibes = request.form.getlist("vibes")
        vibe_tags = ",".join(vibes)

        if not username:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Username is required.")
        if not gender:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Gender is required.")
        if not dob:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Birth date is required.")
        if not re.match(r"^\+?[0-9\-\s]{7,20}$", phone):
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Enter a valid phone number.")
        if len(bio) > 280:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Bio must be 280 characters or less.")
        if not vibe_tags:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Select at least one vibe.")

        try:
            y, m, d = map(int, dob.split("-"))
            today = date.today()
            age = today.year - y - ((today.month, today.day) < (m, d))
        except Exception:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Enter a valid birth date.")

        if age < 18:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="You must be 18+ to join Spotlight.")

        exists = conn.execute(
            "SELECT id FROM users WHERE username=? AND id<>?",
            (username, user["id"]),
        ).fetchone()
        if exists:
            return render_template("complete_profile.html", user=user, selected_vibes=vibes, error="Username already exists.")

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

    selected_vibes = [v for v in (user["vibe_tags"] or "").split(",") if v]
    return render_template("complete_profile.html", user=user, selected_vibes=selected_vibes)

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

    vibes = []
    if user["vibe_tags"]:
        vibes = user["vibe_tags"].split(",")

    return render_template(
        "settings.html",
        user=user,
        age=age,
        vibes=vibes
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
        },
        users=users
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
    conn.execute("DELETE FROM users WHERE id=?", (target_id,))
    conn.commit()

    return jsonify({"status": "deleted"})


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

    req = conn.execute(
        """
        SELECT r.id, u.username
        FROM requests r
        JOIN users u ON u.id = r.sender_id
        WHERE r.receiver_id = ?
          AND r.status = 'pending'
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        (uid,)
    ).fetchone()

    if req:
        return jsonify({
            "type": "incoming",
            "data": {"id": req["id"], "username": req["username"]}
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

    rows = conn.execute("""
        SELECT r.rating, r.comment, r.created_at, u.username
        FROM reviews r 
        JOIN users u ON u.id = r.reviewer_id
        WHERE r.reviewed_id=?
        ORDER BY r.created_at DESC
    """, (uid,)).fetchall()

    if not rows:
        return jsonify({
            "average": None,
            "count": 0,
            "reviews": []
        })

    avg = round(sum(r["rating"] for r in rows) / len(rows), 1)

    return jsonify({
        "average": avg,
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

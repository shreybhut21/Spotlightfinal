import time
import os
import logging
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from geopy.distance import geodesic
import csv
import io

try:
    from . import db
except ImportError:  # allow running as standalone script
    import db  # type: ignore

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

# ======================================================
# DB INIT
# ======================================================
db.init_app(app)
try:
    db.init_db()
except Exception:
    pass

# ======================================================
# AUTH / PAGES
# ======================================================
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/auth")
def auth():
    return render_template("auth.html")

@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username")
    password = request.form.get("password")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

    # guard against missing password hashes or empty input
    if not user:
        return render_template("auth.html", error="Invalid credentials")

    # allow legacy accounts with null/empty hash to continue (no-password fallback)
    if not user["password_hash"]:
        session["user_id"] = user["id"]
        return redirect(url_for("index_html"))

    if not password or not check_password_hash(user["password_hash"], password):
        return render_template("auth.html", error="Invalid credentials")

    session["user_id"] = user["id"]
    return redirect(url_for("index_html"))

@app.route("/signup", methods=["POST"])
def signup():
    username = request.form.get("username")
    password = request.form.get("password")
    gender = request.form.get("gender")
    dob = request.form.get("dob")
    bio = request.form.get("bio", "")
    phone = (request.form.get("phone") or "").strip()
    vibes = request.form.getlist("vibes")  # multiple checkboxes

    vibe_tags = ",".join(vibes)

    conn = db.get_db_connection()

    # basic validation
    if not username or not password:
        return render_template("auth.html", error="Username and password required", show_signup=True)

    exists = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()

    if exists:
        return render_template("auth.html", error="Username already exists", show_signup=True)

    pwd_hash = generate_password_hash(password)

    conn.execute("""
        INSERT INTO users
        (username, password_hash, gender, dob, bio, vibe_tags, phone,
         trust_score, is_matched, matched_with, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 100, 0, NULL, 1, ?)
    """, (
        username,
        pwd_hash,
        gender,
        dob,
        bio,
        vibe_tags,
        phone,
        time.time()
    ))

    conn.commit()

    session["user_id"] = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()["id"]

    return redirect(url_for("index_html"))



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
    Allows signed-in users or the special admin session.
    """
    if "user_id" not in session and not session.get("is_admin"):
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
    if "user_id" not in session and not session.get("is_admin"):
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
    if "user_id" not in session and not session.get("is_admin"):
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
    if "user_id" not in session and not session.get("is_admin"):
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
    if "user_id" not in session and not session.get("is_admin"):
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
    if "user_id" not in session and not session.get("is_admin"):
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

    conn = db.get_db_connection()
    u = conn.execute(
        "SELECT is_matched FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    return jsonify({"matched": bool(u["is_matched"])})

# ======================================================
# API – END MATCH
# ======================================================
@app.route("/api/end_match", methods=["POST"])
def end_match():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    uid = session["user_id"]
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

    conn.execute(
        "UPDATE users SET is_matched=0, matched_with=NULL WHERE id IN (?, ?)",
        (uid, other)
    )

    cur = conn.execute("""
        UPDATE matches
        SET status='ended', ended_at=?
        WHERE (user1_id=? AND user2_id=?)
           OR (user1_id=? AND user2_id=?)
    """, (time.time(), uid, other, other, uid))

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
def recalc_trust(conn, user_id):
    """Set trust_score from the latest rating using a simple 90 + rating rule (1→91, 10→110)."""
    row = conn.execute(
        "SELECT rating FROM reviews WHERE reviewed_id=? ORDER BY created_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()

    if not row:
        return

    trust_score = 90 + int(row["rating"])
    conn.execute("UPDATE users SET trust_score=? WHERE id=?", (trust_score, user_id))


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

    # simple rate limit: 1 review per hour per reviewer→target
    recent = conn.execute(
        """
        SELECT 1 FROM reviews
        WHERE reviewer_id=? AND reviewed_id=? AND created_at > ?
        """,
        (reviewer_id, reviewed_id, time.time() - 3600)
    ).fetchone()
    if recent:
        return jsonify({"error": "too_frequent"}), 429

    conn.execute(
        """
        INSERT INTO reviews (reviewer_id, reviewed_id, rating, comment, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (reviewer_id, reviewed_id, rating, comment, time.time())
    )

    recalc_trust(conn, reviewed_id)
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
        if geodesic((lat, lon), (r["lat"], r["lon"])).km <= 5:
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

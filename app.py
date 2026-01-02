import time
import os
import logging
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from geopy.distance import geodesic
import db

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

    if not user or not check_password_hash(user["password_hash"], password):
        return render_template("auth.html", error="Invalid credentials")

    session["user_id"] = user["id"]
    return redirect(url_for("index_html"))

@app.route("/signup", methods=["POST"])
def signup():
    username = request.form.get("username")
    password = request.form.get("password")

    conn = db.get_db_connection()
    exists = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()

    if exists:
        return render_template("auth.html", error="Username already exists")

    pwd_hash = generate_password_hash(password)

    conn.execute(
        """
        INSERT INTO users
        (username, password_hash, trust_score, is_matched, matched_with, is_active, created_at)
        VALUES (?, ?, 100, 0, NULL, 1, ?)
        """,
        (username, pwd_hash, time.time())
    )
    conn.commit()

    session["user_id"] = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()["id"]

    return redirect(url_for("index_html"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/settings")
def settings():
    if "user_id" not in session:
        return redirect("/auth")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()

    return render_template("settings.html", user=user)

@app.route("/index.html")
def index_html():
    if "user_id" not in session:
        return redirect("/auth")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()

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

    sender_id = session["user_id"]
    receiver_id = request.json.get("receiver_id")

    if sender_id == receiver_id:
        return jsonify({"error": "invalid"}), 400

    conn = db.get_db_connection()

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

    if action == "decline":
        conn.execute(
            "UPDATE requests SET status='declined' WHERE id=?",
            (request_id,)
        )
        conn.commit()
        return jsonify({"status": "declined"})

    # ================= ACCEPT =================
    conn.execute(
        "UPDATE requests SET status='accepted' WHERE id=?",
        (request_id,)
    )

    # 🔥 BOTH USERS ENTER MATCH MODE
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
        UPDATE requests SET status='declined'
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

    conn.execute(
        "UPDATE users SET is_matched=0, matched_with=NULL WHERE id IN (?, ?)",
        (uid, other)
    )

    conn.execute("""
        UPDATE matches
        SET status='ended', ended_at=?
        WHERE (user1_id=? AND user2_id=?)
           OR (user1_id=? AND user2_id=?)
    """, (time.time(), uid, other, other, uid))

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
        return jsonify({"error": "no_match"})

    other_id = row["user2_id"] if row["user1_id"] == uid else row["user1_id"]

    other = conn.execute(
        "SELECT id, username, trust_score FROM users WHERE id=?",
        (other_id,)
    ).fetchone()

    return jsonify(dict(other))


# ======================================================
# API – SUBMIT FEEDBACK
# ======================================================
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
                "time": r["created_at"]
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
        SELECT s.*, u.username, u.trust_score
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
            })

    return jsonify(result)

# ======================================================
# RUN
# ======================================================
if __name__ == "__main__":
    app.run(debug=True)

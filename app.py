import time
import os
import logging
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from geopy.distance import geodesic
import db

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static"
)
app.secret_key = os.environ.get("SPOTLIGHT_SECRET_KEY", "spotlight_secret_key")
app.logger.setLevel(logging.DEBUG)

# ----------------------------
# DB INIT
# ----------------------------
db.init_app(app)
try:
    db.init_db()
except Exception:
    pass

# ----------------------------
# AUTH / PAGES
# ----------------------------
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

    app.logger.info(f"Login attempt username={username}")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

    # defend against malformed DB rows where password_hash may be NULL/empty
    pwd_hash = user["password_hash"] if user else None

    if not user:
        app.logger.info(f"Login failed: user not found username={username}")
        return render_template("auth.html", error="Invalid credentials")

    if not pwd_hash:
        app.logger.warning(f"Login failed: missing password_hash for username={username}")
        return render_template("auth.html", error="Invalid credentials")

    try:
        if not check_password_hash(pwd_hash, password):
            app.logger.info(f"Login failed: bad password for username={username}")
            return render_template("auth.html", error="Invalid credentials")
    except Exception:
        app.logger.exception("Error while checking password_hash")
        return render_template("auth.html", error="Internal error")

    session["user_id"] = user["id"]
    app.logger.info(f"Login success user_id={user['id']} username={username}")
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
        (username, password_hash, avatar_level, trust_score, vibe_tags, avatar_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (username, pwd_hash, 1, 100, "", "static/default-avatar.png", time.time())
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

@app.route("/index.html")
def index_html():
    if "user_id" not in session:
        return redirect("/auth")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()

    return render_template("index.html", user=user)

@app.route("/settings")
def settings():
    if "user_id" not in session:
        return redirect("/auth")

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT username, trust_score FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    return render_template("settings.html", user=user)

# ----------------------------
# API – USER INFO
# ----------------------------
@app.route("/api/user_info")
def user_info():
    if "user_id" not in session:
        return jsonify({}), 401

    conn = db.get_db_connection()
    user = conn.execute(
        "SELECT trust_score FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    return jsonify({"trust_score": user["trust_score"]})

# ----------------------------
# API – SEND REQUEST (🔥 MISSING BEFORE)
# ----------------------------
@app.route("/api/send_request", methods=["POST"])
def send_request():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    try:
        data = request.get_json(silent=True)
        app.logger.debug(f"send_request payload: {data} session_user={session.get('user_id')}")
        sender_id = session["user_id"]
        if not data:
            return jsonify({"error": "missing json body"}), 400
        receiver_id = int(data.get("receiver_id"))
    except (ValueError, TypeError):
        return jsonify({"error": "invalid receiver id"}), 400
    except Exception:
        app.logger.exception("Unexpected error parsing send_request payload")
        return jsonify({"error": "bad request"}), 400

    if receiver_id == sender_id:
        return jsonify({"error": "cannot send request to yourself"}), 400

    try:
        conn = db.get_db_connection()
        # validate receiver exists and is active
        recv = conn.execute("SELECT id, is_active FROM users WHERE id = ?", (receiver_id,)).fetchone()
        if not recv or recv["is_active"] != 1:
            return jsonify({"error": "receiver not found"}), 404

        # prevent duplicate pending requests
        existing = conn.execute(
            "SELECT id FROM requests WHERE sender_id = ? AND receiver_id = ? AND status = 'pending'",
            (sender_id, receiver_id),
        ).fetchone()
        if existing:
            app.logger.debug(f"Duplicate request prevented: sender={sender_id} receiver={receiver_id} existing_id={existing['id']}")
            return jsonify({"status": "already_sent"}), 409

        app.logger.info(f"REQUEST INSERT: {sender_id} -> {receiver_id}")
        conn.execute(
            "INSERT INTO requests (sender_id, receiver_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (sender_id, receiver_id, time.time()),
        )
        conn.commit()

        app.logger.info("Request inserted and committed")
        return jsonify({"status": "sent"})
    except Exception:
        app.logger.exception("Error while handling send_request")
        return jsonify({"error": "internal error"}), 500

# ----------------------------
# API – CHECK REQUESTS
# ----------------------------
@app.route("/api/check_requests")
def check_requests():
    if "user_id" not in session:
        return jsonify({"type": "none"})

    uid = session["user_id"]
    conn = db.get_db_connection()

    req = conn.execute("""
        SELECT r.id, u.username
        FROM requests r
        JOIN users u ON u.id = r.sender_id
        WHERE r.receiver_id = ?
          AND r.status = 'pending'
        ORDER BY r.created_at DESC
        LIMIT 1
    """, (uid,)).fetchone()

    if req:
        app.logger.info(f"Incoming request for user_id={uid} from {req['id']}")
        return jsonify({
            "type": "incoming",
            "data": {
                "id": req["id"],
                "username": req["username"]
            }
        })

    app.logger.debug(f"No incoming requests for user_id={uid}")
    return jsonify({"type": "none"})
# ----------------------------


# ----------------------------
# API – RESPOND REQUEST
# ----------------------------
@app.route("/api/respond_request", methods=["POST"])
def respond_request():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    request_id = request.json.get("request_id")
    action = request.json.get("action")  # accept / decline

    status = "accepted" if action == "accept" else "declined"

    conn = db.get_db_connection()
    app.logger.info(f"Respond request id={request_id} action={action} by user_id={session.get('user_id')}")

    if not request_id:
        return jsonify({"error": "missing request_id"}), 400

    # validate request exists
    req = conn.execute("SELECT id, sender_id, receiver_id, status FROM requests WHERE id = ?", (request_id,)).fetchone()
    if not req:
        return jsonify({"error": "request not found"}), 404

    # only the receiver may respond
    if req["receiver_id"] != session["user_id"]:
        app.logger.warning(f"Unauthorized respond attempt user={session.get('user_id')} on request={request_id}")
        return jsonify({"error": "forbidden"}), 403

    if req["status"] != "pending":
        return jsonify({"error": "request already handled"}), 400

    conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, request_id))
    conn.commit()

    app.logger.info(f"Request {request_id} updated to {status}")
    return jsonify({"status": status})

# ----------------------------
# API – CHECK IN / OUT
# ----------------------------
@app.route("/api/checkin", methods=["POST"])
def checkin():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json
    user_id = session["user_id"]

    app.logger.info(f"Checkin attempt user_id={user_id} lat={data.get('lat')} lon={data.get('lon')}")

    expiry = time.time() + (2 * 60 * 60 if data.get("meet_time") else 90 * 60)

    conn = db.get_db_connection()
    conn.execute("DELETE FROM spotlights WHERE user_id = ?", (user_id,))
    conn.execute(
        """
        INSERT INTO spotlights
        (user_id, lat, lon, place, intent, meet_time, clue, timestamp, expiry)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            data["lat"],
            data["lon"],
            data["place"],
            data["intent"],
            data.get("meet_time"),
            data["clue"],
            time.time(),
            expiry,
        ),
    )
    conn.commit()

    app.logger.info(f"User {user_id} is now live")
    return jsonify({"status": "live"})

@app.route("/api/checkout", methods=["POST"])
def checkout():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    user_id = session["user_id"]
    app.logger.info(f"Checkout attempt user_id={user_id}")
    conn = db.get_db_connection()
    conn.execute("DELETE FROM spotlights WHERE user_id = ?", (user_id,))
    conn.commit()
    app.logger.info(f"User {user_id} checked out (spotlight removed)")

    return jsonify({"status": "off"})

# ----------------------------
# API – NEARBY USERS
# ----------------------------
@app.route("/api/nearby")
def nearby():
    if "user_id" not in session:
        return jsonify([])

    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))
    me = session["user_id"]

    app.logger.debug(f"Nearby request from user_id={me} lat={lat} lon={lon}")

    conn = db.get_db_connection()
    rows = conn.execute(
        """
        SELECT s.*, u.username, u.trust_score
        FROM spotlights s
        JOIN users u ON u.id = s.user_id
        WHERE s.expiry > ? AND s.user_id != ?
        """,
        (time.time(), me),
    ).fetchall()

    result = []
    for r in rows:
        dist = geodesic((lat, lon), (r["lat"], r["lon"])).km
        if dist <= 5:
            result.append({
                "id": r["user_id"],
                "lat": r["lat"],
                "lon": r["lon"],
                "username": r["username"],
                "trust_score": r["trust_score"],
                "distance": round(dist, 1),
            })

    return jsonify(result)

# ----------------------------
# RUN
# ----------------------------
if __name__ == "__main__":
    app.run(debug=True)

import time
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
app.secret_key = "spotlight_secret_key"

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
        (username, avatar_level, trust_score, vibe_tags, password_hash, avatar_url)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (username, 1, 100, "", pwd_hash, "static/default-avatar.png")
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

    data = request.json
    sender_id = session["user_id"]
    receiver_id = data.get("receiver_id")

    if not receiver_id:
        return jsonify({"error": "missing receiver"}), 400

    conn = db.get_db_connection()
    print("REQUEST INSERT:", sender_id, "→", receiver_id)

    conn.execute(
        """
        INSERT INTO requests (sender_id, receiver_id, status, created_at)
        VALUES (?, ?, 'pending', ?)
        """,
        (sender_id, receiver_id, time.time())
    )
    conn.commit()

    return jsonify({"status": "sent"})

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
        return jsonify({
            "type": "incoming",
            "data": {
                "id": req["id"],
                "username": req["username"]
            }
        })

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
    conn.execute(
        "UPDATE requests SET status = ? WHERE id = ?",
        (status, request_id)
    )
    conn.commit()

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

    return jsonify({"status": "live"})

@app.route("/api/checkout", methods=["POST"])
def checkout():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    conn = db.get_db_connection()
    conn.execute("DELETE FROM spotlights WHERE user_id = ?", (session["user_id"],))
    conn.commit()

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

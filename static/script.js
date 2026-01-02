// ==========================
// GLOBAL STATE
// ==========================
let map;
let myLat = null;
let myLon = null;
let userMarker = null;
let nearbyMarkers = [];
let selectedUserId = null;
let currentRequestId = null;
let locationReady = false;
let requestPoller = null;
let matchPoller = null;
let isMatched = false;
// 🔥 view feedback state
let myFeedbackList = [];

// 🔥 feedback state
let feedbackTargetId = null;
let selectedRating = 0;

// GPS smoothing
let lastPositions = [];
const MAX_POINTS = 5;
let hasFirstFix = false;

// ==========================
// INIT
// ==========================
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  fetchUserInfo();
  requestPoller = setInterval(pollRequests, 5000);
  startMatchPoller();
});

// ==========================
// MAP INIT
// ==========================
function initMap() {
  map = L.map("map", { zoomControl: false }).setView([20.5937, 78.9629], 5);

  L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { attribution: "&copy; OpenStreetMap contributors" }
  ).addTo(map);

  if (!navigator.geolocation) {
    showGPS("Geolocation not supported");
    return;
  }

  showGPS("📍 Getting your location…");

  navigator.geolocation.watchPosition(
    handleLocation,
    () => showGPS("📍 Unable to get location"),
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 3000 }
  );
}

// ==========================
// LOCATION HANDLER
// ==========================
function handleLocation(pos) {
  const { latitude, longitude } = pos.coords;

  if (!hasFirstFix) {
    myLat = latitude;
    myLon = longitude;
    locationReady = true;
    hasFirstFix = true;
    hideGPS();
  } else {
    lastPositions.push([latitude, longitude]);
    if (lastPositions.length > MAX_POINTS) lastPositions.shift();

    myLat = lastPositions.reduce((s, p) => s + p[0], 0) / lastPositions.length;
    myLon = lastPositions.reduce((s, p) => s + p[1], 0) / lastPositions.length;
  }

  if (!userMarker) {
    map.setView([myLat, myLon], 15);
    userMarker = L.circleMarker([myLat, myLon], {
      radius: 8,
      fillColor: "#3bb2d0",
      color: "#fff",
      weight: 2,
      fillOpacity: 1
    }).addTo(map);
  } else {
    userMarker.setLatLng([myLat, myLon]);
  }

  if (!isMatched) fetchNearbyUsers();
}

// ==========================
// GPS UI
// ==========================
function showGPS(text) {
  const el = document.getElementById("gps-status");
  if (!el) return;
  el.innerText = text;
  el.style.display = "block";
}
function hideGPS() {
  const el = document.getElementById("gps-status");
  if (!el) return;
  el.style.display = "none";
}

// ==========================
// USER INFO
// ==========================
async function fetchUserInfo() {
  const res = await fetch("/api/user_info");
  if (!res.ok) return;
  const data = await res.json();

  const el = document.getElementById("my-trust-score");
  if (el) el.innerText = data.trust_score ?? "--";

  if (data.is_matched === 1) enterMatchMode();
}

// ==========================
// MATCH STATUS POLLING
// ==========================
function startMatchPoller() {
  if (matchPoller) return;
  matchPoller = setInterval(checkMatchStatus, 3000);
}

async function checkMatchStatus() {
  if (isMatched) return;

  const res = await fetch("/api/match_status");
  if (!res.ok) return;

  const data = await res.json();
  if (data.matched) enterMatchMode();
}

// ==========================
// NEARBY USERS
// ==========================
async function fetchNearbyUsers() {
  if (!locationReady) return;

  const res = await fetch(`/api/nearby?lat=${myLat}&lon=${myLon}`);
  if (!res.ok) return;

  const users = await res.json();

  nearbyMarkers.forEach(m => map.removeLayer(m));
  nearbyMarkers = [];

  users.forEach(user => {
    const icon = L.divIcon({
      html: `<div style="width:60px;height:60px;border-radius:50%;
        background:rgba(255,215,0,0.25);
        border:2px solid rgba(255,215,0,0.8)"></div>`,
      iconSize: [60, 60],
      className: ""
    });

    const marker = L.marker([user.lat, user.lon], { icon }).addTo(map);
    marker.on("click", () => openProfile(user));
    nearbyMarkers.push(marker);
  });
}

// ==========================
// PROFILE
// ==========================
function openProfile(user) {
  selectedUserId = user.id;
  document.getElementById("p-username").innerText = user.username;
  document.getElementById("p-score").innerText = user.trust_score ?? "--";
  openSheet("profile-sheet");
}

// ==========================
// SEND REQUEST
// ==========================
async function sendRequest() {
  if (!selectedUserId) return;

  await fetch("/api/send_request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ receiver_id: selectedUserId })
  });

  startMatchPoller();
  closeAllSheets();
}

// ==========================
// REQUESTS
// ==========================
async function pollRequests() {
  if (isMatched) return;

  const res = await fetch("/api/check_requests");
  if (!res.ok) return;

  const data = await res.json();
  if (data.type !== "incoming") return;

  currentRequestId = data.data.id;
  document.getElementById("bell-dot").classList.remove("hidden");
  document.getElementById("bellContent").innerHTML = `
    <strong>${data.data.username}</strong>
    <p>Wants to meet you</p>
    <div class="action-row">
      <button onclick="respondRequest('accept')" class="primary-btn">Accept</button>
      <button onclick="respondRequest('decline')" class="secondary-btn">Decline</button>
    </div>
  `;
}

// ==========================
// RESPOND REQUEST
// ==========================
async function respondRequest(action) {
  await fetch("/api/respond_request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: currentRequestId, action })
  });

  currentRequestId = null;
  document.getElementById("bell-dot").classList.add("hidden");
  document.getElementById("bellBox").classList.add("hidden");

  if (action === "accept") enterMatchMode();
}

// ==========================
// MATCH MODE
// ==========================
function enterMatchMode() {
  if (isMatched) return;

  isMatched = true;
  clearInterval(requestPoller);
  clearInterval(matchPoller);

  nearbyMarkers.forEach(m => map.removeLayer(m));
  nearbyMarkers = [];

  document.body.innerHTML = `
    <div style="height:100vh;display:flex;align-items:center;
      justify-content:center;flex-direction:column;
      background:#000;color:gold;padding:20px;text-align:center;">
      <h2>✨ Match Mode ✨</h2>
      <p>You are matched 🎉</p>
      <button onclick="endMatch()" class="primary-btn">End Match</button>
    </div>
  `;
}

// ==========================
// END MATCH → FEEDBACK
// ==========================
async function endMatch() {
  await fetch("/api/end_match", { method: "POST" });

  const res = await fetch("/api/feedback_target");
  if (!res.ok) {
    window.location.reload();
    return;
  }

  const target = await res.json();
  feedbackTargetId = target.id;
  showFeedbackUI(target.username);
}

// ==========================
// FEEDBACK UI
// ==========================
function showFeedbackUI(username) {
  document.body.innerHTML = `
    <div style="min-height:100vh;background:#000;color:#fff;
      display:flex;flex-direction:column;justify-content:center;
      align-items:center;padding:20px;text-align:center;">

      <h2>⭐ Rate Your Match ⭐</h2>
      <p>@${username}</p>

      <div id="rating-box" style="margin:15px;">
        ${[1,2,3,4,5,6,7,8,9,10].map(n =>
          `<button class="rate-btn" onclick="selectRating(${n}, this)">${n}</button>`
        ).join("")}
      </div>

      <textarea id="feedback-text"
        placeholder="Write up to 50 words"
        style="width:90%;max-width:400px;height:80px;"></textarea>

      <button onclick="submitFeedback()" class="primary-btn"
        style="margin-top:15px;">Submit</button>
    </div>
  `;
}

function selectRating(n, el) {
  selectedRating = n;
  document.querySelectorAll(".rate-btn").forEach(b => {
    b.style.background = "#222";
  });
  el.style.background = "gold";
}

// ==========================
// FEEDBACK RESULT (VIEW)
// ==========================
function showFeedbackResult(rating, comment) {
  document.body.innerHTML = `
    <div style="min-height:100vh;background:#000;color:#fff;
      display:flex;flex-direction:column;justify-content:center;
      align-items:center;padding:20px;text-align:center;">

      <h2>✅ Feedback Submitted</h2>
      <p>Your rating: <strong>${rating}/10</strong></p>
      ${comment ? `<p style="max-width:400px;margin-top:10px;">"${comment}"</p>` : ""}
      <button onclick="window.location.reload()" class="primary-btn" style="margin-top:20px;">
        Continue
      </button>
    </div>
  `;
}

// ==========================
// SUBMIT FEEDBACK
// ==========================
async function submitFeedback() {
  const comment = document.getElementById("feedback-text").value.trim();

  if (!selectedRating) {
    alert("Select a rating");
    return;
  }

  if (comment.split(/\s+/).length > 50) {
    alert("Max 50 words allowed");
    return;
  }

  const res = await fetch("/api/submit_feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reviewed_id: feedbackTargetId,
      rating: selectedRating,
      comment
    })
  });

  if (!res.ok) {
    const err = await res.json();
    alert(err.error || "Error");
    return;
  }

  showFeedbackResult(selectedRating, comment);
}

// ==========================
// UI HELPERS
// ==========================
function openSheet(id) {
  document.getElementById("overlay").classList.remove("hidden");
  setTimeout(() => document.getElementById(id).classList.add("active"), 10);
}

function closeAllSheets() {
  document.querySelectorAll(".bottom-sheet").forEach(s =>
    s.classList.remove("active")
  );
  setTimeout(() => document.getElementById("overlay").classList.add("hidden"), 300);
}
async function fetchMyFeedback() {
  const res = await fetch("/api/my_feedback");
  if (!res.ok) {
    alert("Unable to load feedback");
    return;
  }
  myFeedbackList = await res.json();
  showMyFeedbackUI();
}
function showMyFeedbackUI() {
  if (!myFeedbackList.length) {
    document.body.innerHTML = `
      <div style="min-height:100vh;background:#000;color:#fff;
        display:flex;flex-direction:column;justify-content:center;
        align-items:center;text-align:center;padding:20px;">
        <h2>📝 Feedback</h2>
        <p>No feedback yet</p>
        <button onclick="window.location.reload()" class="primary-btn">
          Back
        </button>
      </div>
    `;
    return;
  }

  document.body.innerHTML = `
    <div style="min-height:100vh;background:#000;color:#fff;padding:20px;">
      <h2 style="text-align:center;">📝 Feedback About You</h2>

      ${myFeedbackList.map(f => `
        <div style="background:#111;padding:15px;margin:15px 0;
          border-radius:10px;border:1px solid #333;">
          <p><strong>⭐ ${f.rating}/10</strong></p>
          ${f.comment ? `<p style="opacity:.9;">"${f.comment}"</p>` : ""}
          <small style="opacity:.6;">${new Date(f.created_at * 1000).toLocaleString()}</small>
        </div>
      `).join("")}

      <div style="text-align:center;margin-top:30px;">
        <button onclick="window.location.reload()" class="primary-btn">
          Back to Map
        </button>
      </div>
    </div>
  `;
}
<button onclick="fetchMyFeedback()" class="primary-btn" style="margin-top:10px;">
  View My Feedback
</button>

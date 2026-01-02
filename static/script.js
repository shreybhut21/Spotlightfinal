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
let isMatched = false;

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
    {
      enableHighAccuracy: true,
      timeout: 20000,
      maximumAge: 3000
    }
  );
}

// ==========================
// LOCATION HANDLER
// ==========================
function handleLocation(pos) {
  const { latitude, longitude } = pos.coords;

  // Accept first fix immediately (important for localhost)
  if (!hasFirstFix) {
    myLat = latitude;
    myLon = longitude;
    locationReady = true;
    hasFirstFix = true;
    hideGPS();
  } else {
    lastPositions.push([latitude, longitude]);
    if (lastPositions.length > MAX_POINTS) lastPositions.shift();

    myLat =
      lastPositions.reduce((s, p) => s + p[0], 0) / lastPositions.length;
    myLon =
      lastPositions.reduce((s, p) => s + p[1], 0) / lastPositions.length;
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
// SOFT GPS UI
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
// GO LIVE
// ==========================
async function confirmCheckIn() {
  if (!locationReady) {
    showGPS("📍 Locating… try again in a moment");
    return;
  }

  const place = placeInput();
  const intent = intentInput();
  const clue = clueInput();
  const meet_time = document.getElementById("meet_time").value || null;

  if (!place || !intent || !clue) return;

  await fetch("/api/checkin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat: myLat, lon: myLon, place, intent, meet_time, clue })
  });

  document.getElementById("live-indicator").classList.remove("hidden");
  document.getElementById("main-fab").style.display = "none";
  closeAllSheets();
}

// ==========================
// TURN OFF LIVE ✅ FIXED
// ==========================
async function turnOffSpotlight() {
  await fetch("/api/checkout", { method: "POST" });
  document.getElementById("live-indicator").classList.add("hidden");
  document.getElementById("main-fab").style.display = "flex";
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
  isMatched = true;
  clearInterval(requestPoller);

  nearbyMarkers.forEach(m => map.removeLayer(m));
  nearbyMarkers = [];

  document.body.innerHTML = `
    <div style="height:100vh;display:flex;align-items:center;
      justify-content:center;flex-direction:column;
      background:#000;color:gold;">
      <h2>✨ Match Mode ✨</h2>
      <button onclick="endMatch()" class="primary-btn">End Match</button>
    </div>
  `;
}

// ==========================
// END MATCH
// ==========================
async function endMatch() {
  await fetch("/api/end_match", { method: "POST" });
  window.location.reload();
}

// ==========================
// UI HELPERS
// ==========================
function toggleBellBox() {
  document.getElementById("bellBox").classList.toggle("hidden");
}

document.addEventListener("click", e => {
  const bell = document.querySelector(".bell-wrapper");
  if (bell && !bell.contains(e.target)) {
    document.getElementById("bellBox").classList.add("hidden");
  }
});

const placeInput = () => document.getElementById("place")?.value.trim();
const intentInput = () => document.getElementById("intent")?.value.trim();
const clueInput = () => document.getElementById("visual-clue")?.value.trim();

function openSheet(id) {
  document.getElementById("overlay").classList.remove("hidden");
  setTimeout(() => document.getElementById(id).classList.add("active"), 10);
}

function closeAllSheets() {
  document.querySelectorAll(".bottom-sheet").forEach(s => s.classList.remove("active"));
  setTimeout(() => document.getElementById("overlay").classList.add("hidden"), 300);
}

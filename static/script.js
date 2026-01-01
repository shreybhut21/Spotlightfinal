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

// ==========================
// INIT
// ==========================
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  fetchUserInfo();
  window.requestPoller = setInterval(pollRequests, 5000);
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
    alert("Geolocation not supported");
    return;
  }

  navigator.geolocation.watchPosition(
    pos => {
      myLat = pos.coords.latitude;
      myLon = pos.coords.longitude;
      locationReady = true;

      if (!userMarker) {
        map.setView([myLat, myLon], 14);
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

      fetchNearbyUsers();
    },
    () => alert("Please enable location services"),
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
  );
}

// ==========================
// USER INFO
// ==========================
async function fetchUserInfo() {
  try {
    const res = await fetch("/api/user_info");
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById("my-trust-score").innerText =
      data.trust_score ?? "--";
  } catch (e) {
    console.error("user_info error", e);
  }
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
    const ring = document.createElement("div");
    ring.style.width = "60px";
    ring.style.height = "60px";
    ring.style.borderRadius = "50%";
    ring.style.background = "rgba(255,215,0,0.25)";
    ring.style.border = "2px solid rgba(255,215,0,0.8)";
    ring.style.cursor = "pointer";

    const icon = L.divIcon({
      html: ring.outerHTML,
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
// CHECK IN (GO LIVE) ✅
// ==========================
async function confirmCheckIn() {
  if (!locationReady) {
    alert("Waiting for GPS fix…");
    return;
  }

  const place = document.getElementById("place").value.trim();
  const intent = document.getElementById("intent").value.trim();
  const meet_time = document.getElementById("meet_time").value || null;
  const clue = document.getElementById("visual-clue").value.trim();

  if (!place || !intent || !clue) {
    alert("Please fill all required fields");
    return;
  }

  const res = await fetch("/api/checkin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      lat: myLat,
      lon: myLon,
      place,
      intent,
      meet_time,
      clue
    })
  });

  if (!res.ok) {
    alert("Failed to go live");
    return;
  }

  document.getElementById("live-indicator").classList.remove("hidden");
  document.getElementById("main-fab").style.display = "none";
  closeAllSheets();
}

// ==========================
// TURN OFF LIVE ✅
// ==========================
async function turnOffSpotlight() {
  const res = await fetch("/api/checkout", { method: "POST" });
  if (!res.ok) {
    alert("Failed to turn off");
    return;
  }

  document.getElementById("live-indicator").classList.add("hidden");
  document.getElementById("main-fab").style.display = "flex";
}

// ==========================
// SEND REQUEST
// ==========================
async function sendRequest() {
  if (!selectedUserId) return;

  const res = await fetch("/api/send_request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ receiver_id: selectedUserId })
  });

  if (!res.ok) {
    alert("Request failed");
    return;
  }

  closeAllSheets();
  alert("Request sent ✅");
}

// ==========================
// POLL REQUESTS
// ==========================
async function pollRequests() {
  const res = await fetch("/api/check_requests");
  if (!res.ok) return;

  const data = await res.json();

  if (data.type === "incoming") {
    currentRequestId = data.data.id;
    document.getElementById("bell-dot").classList.remove("hidden");

    document.getElementById("bellContent").innerHTML = `
      <strong>${data.data.username}</strong>
      <p>Wants to meet you</p>
      <div style="display:flex; gap:8px; margin-top:8px;">
        <button onclick="respondRequest('accept')" class="primary-btn">Accept</button>
        <button onclick="respondRequest('decline')" class="secondary-btn">Decline</button>
      </div>
    `;
  }

  // also check if user got matched (accept flow sets is_matched on both users)
  try {
    const infoRes = await fetch("/api/user_info");
    if (infoRes.ok) {
      const info = await infoRes.json();
      if (info.is_matched === 1) {
        // stop polling
        clearInterval(window.requestPoller);

        // optional: hide map markers
        nearbyMarkers.forEach(m => map.removeLayer(m));
        nearbyMarkers = [];

        // show match confirmation
        alert("🎉 You’re matched!");
      }
    }
  } catch (e) {
    console.error("user_info poll error", e);
  }
}

// ==========================
// RESPOND REQUEST
// ==========================
async function respondRequest(action) {
  if (!currentRequestId) return;

  await fetch("/api/respond_request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: currentRequestId, action })
  });

  currentRequestId = null;
  document.getElementById("bell-dot").classList.add("hidden");
  document.getElementById("bellBox").classList.add("hidden");
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

// ==========================
// BELL UI
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

// ==========================
// SETTINGS
// ==========================
function goToSettings() {
  window.location.href = "/settings";
}

// ==========================
// MATCH MODE UI
// ==========================
async function checkMatchMode() {
  const res = await fetch("/api/match_status");
  if (!res.ok) return;

  const data = await res.json();
  if (!data.matched) return;

  document.body.innerHTML = `
    <div style="
      height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      flex-direction:column;
      background:#000;
      color:gold;
      font-size:22px;
    ">
      <h2>✨ Match Mode ✨</h2>
      <p>You matched with <b>${data.partner}</b></p>
      <p>No other users can see you now</p>
    </div>
  `;
}

document.addEventListener("DOMContentLoaded", checkMatchMode);

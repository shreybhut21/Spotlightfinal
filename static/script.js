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

// ==========================
// INIT
// ==========================
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  fetchUserInfo();
  setInterval(pollRequests, 5000);
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
    {
      enableHighAccuracy: true,
      timeout: 20000,
      maximumAge: 0
    }
  );
}

// ==========================
// USER INFO
// ==========================
async function fetchUserInfo() {
  const res = await fetch("/api/user_info");
  if (!res.ok) return;
  const data = await res.json();
  document.getElementById("my-trust-score").innerText = data.trust_score ?? "--";
}

// ==========================
// NEARBY USERS
// ==========================
async function fetchNearbyUsers() {
  if (!locationReady) return;

  const res = await fetch(`/api/nearby?lat=${myLat}&lon=${myLon}`);
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
// SEND REQUEST
// ==========================
async function sendRequest() {
  if (!selectedUserId) return;

  await fetch("/api/send_request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ receiver_id: selectedUserId })
  });

  closeAllSheets();
  alert("Request sent");
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
      <button onclick="respondRequest('accept')">Accept</button>
      <button onclick="respondRequest('decline')">Decline</button>
    `;
  }
}

// ==========================
// RESPOND REQUEST
// ==========================
async function respondRequest(action) {
  await fetch("/api/respond_request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_id: currentRequestId,
      action
    })
  });

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
  document.querySelectorAll(".bottom-sheet").forEach(s => s.classList.remove("active"));
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

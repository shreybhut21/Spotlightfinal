// ==========================
// GLOBAL STATE
// ==========================
let map;
let myLat = null;
let myLon = null;
let userMarker = null;
let nearbyMarkers = [];
let selectedUserId = null;
let pollInterval = null;
let currentRequestId = null;
let isLive = false;
let locationReady = false;

// ==========================
// INIT
// ==========================
document.addEventListener('DOMContentLoaded', () => {
  initMap();
  fetchUserInfo();
  startPolling();
});

// ==========================
// MAP INIT (FINAL FIX)
// ==========================
function initMap() {
  map = L.map('map', { zoomControl: false }).setView([20.5937, 78.9629], 5);

  L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    { attribution: '&copy; OpenStreetMap contributors' }
  ).addTo(map);

  if (!navigator.geolocation) {
    alert("Geolocation not supported");
    return;
  }

  // 🔥 USE watchPosition (NOT getCurrentPosition)
  navigator.geolocation.watchPosition(
    pos => {
      myLat = pos.coords.latitude;
      myLon = pos.coords.longitude;
      locationReady = true;

      console.log(
        "LIVE LOCATION:",
        myLat,
        myLon,
        "Accuracy:",
        pos.coords.accuracy
      );

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
    err => {
      alert("Please enable GPS / Location services");
      console.error(err);
    },
    {
      enableHighAccuracy: true, // 🔥 MOST IMPORTANT
      maximumAge: 0,
      timeout: 20000
    }
  );
}

// ==========================
// USER INFO
// ==========================
async function fetchUserInfo() {
  const res = await fetch('/api/user_info');
  if (!res.ok) return;
  const data = await res.json();
  const el = document.getElementById('my-trust-score');
  if (el) el.innerText = data.trust_score ?? '--';
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
    const ring = document.createElement('div');
    ring.style.width = '60px';
    ring.style.height = '60px';
    ring.style.borderRadius = '50%';
    ring.style.background = 'rgba(255,215,0,0.25)';
    ring.style.border = '2px solid rgba(255,215,0,0.8)';
    ring.style.cursor = 'pointer';

    const icon = L.divIcon({
      html: ring.outerHTML,
      iconSize: [60, 60],
      className: ''
    });

    const marker = L.marker([user.lat, user.lon], { icon }).addTo(map);
    marker.on('click', () => openProfile(user));

    nearbyMarkers.push(marker);
  });
}

// ==========================
// GO LIVE
// ==========================
async function confirmCheckIn() {
  if (!locationReady) {
    alert("Waiting for GPS fix. Please wait a few seconds.");
    return;
  }

  const place = document.getElementById('place')?.value.trim();
  const intent = document.getElementById('intent')?.value.trim();
  const meetTime = document.getElementById('meet_time')?.value;
  const clue = document.getElementById('visual-clue')?.value.trim();

  if (!place || !intent || !clue) {
    alert("Please fill all required fields");
    return;
  }

  console.log("CHECKIN SENDING:", myLat, myLon);

  const res = await fetch('/api/checkin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      lat: myLat,
      lon: myLon,
      place,
      intent,
      meet_time: meetTime,
      clue
    })
  });

  if (!res.ok) {
    alert("Failed to go live");
    return;
  }

  isLive = true;
  closeAllSheets();
  document.getElementById('main-fab').style.display = 'none';
  document.getElementById('live-indicator').classList.remove('hidden');

  alert("You are LIVE 🔴");
}

// ==========================
// TURN OFF
// ==========================
async function turnOffSpotlight() {
  await fetch('/api/checkout', { method: 'POST' });

  isLive = false;
  document.getElementById('live-indicator').classList.add('hidden');
  document.getElementById('main-fab').style.display = 'flex';

  alert("Spotlight turned off");
}

// ==========================
// PROFILE
// ==========================
function openProfile(user) {
  selectedUserId = user.id;
  document.getElementById('p-username').innerText = user.username;
  document.getElementById('p-score').innerText = user.trust_score ?? '--';
  openSheet('profile-sheet');
}

// ==========================
// SEND REQUEST
// ==========================
async function sendRequest() {
  if (!selectedUserId) return;

  await fetch('/api/send_request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ receiver_id: selectedUserId })
  });

  closeAllSheets();
  alert("Request sent");
}

// ==========================
// POLLING
// ==========================
function startPolling() {
  pollInterval = setInterval(async () => {
    const res = await fetch('/api/check_requests');
    const data = await res.json();

    if (data.type === 'incoming') {
      currentRequestId = data.data.id;
      document.getElementById('inc-username').innerText = data.data.username;
      openSheet('request-sheet');
    }

    if (data.type === 'accepted') {
      document.getElementById('match-clue').innerText = data.data.clue;
      openSheet('timer-sheet');
      clearInterval(pollInterval);
      startTimer();
    }
  }, 5000);
}

// ==========================
// RESPOND REQUEST
// ==========================
async function respondRequest(action) {
  if (!currentRequestId) return;

  await fetch('/api/respond_request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: currentRequestId, action })
  });

  closeAllSheets();
}

// ==========================
// TIMER
// ==========================
function startTimer() {
  let timeLeft = 15 * 60;
  const display = document.getElementById('timer-countdown');

  const timer = setInterval(() => {
    const m = Math.floor(timeLeft / 60);
    const s = timeLeft % 60;
    display.innerText = `${m}:${s < 10 ? '0' : ''}${s}`;
    timeLeft--;
    if (timeLeft < 0) clearInterval(timer);
  }, 1000);
}

// ==========================
// UI HELPERS
// ==========================
function openSheet(id) {
  document.getElementById('overlay').classList.remove('hidden');
  setTimeout(() => {
    document.getElementById(id).classList.add('active');
  }, 10);
}

function closeAllSheets() {
  document.querySelectorAll('.bottom-sheet')
    .forEach(s => s.classList.remove('active'));

  setTimeout(() => {
    document.getElementById('overlay').classList.add('hidden');
  }, 300);
}

// ==========================
// SETTINGS
// ==========================
function goToSettings() {
  window.location.href = '/settings';
}

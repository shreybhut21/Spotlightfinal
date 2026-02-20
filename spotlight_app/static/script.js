// ==========================
// GLOBAL STATE
// ==========================
let map;
let myLat = null;
let myLon = null;
let userMarker = null;
let nearbyMarkers = [];
let selectedUserId = null;
let nearbyUsers = [];
let currentRequestId = null;
let incomingRequestData = null;
let appNotifications = [];
let locationReady = false;
let requestPoller = null;
let matchPoller = null;
let trustPoller = null;
let appNotifPoller = null;
let isMatched = false;
// 🔥 view feedback state
let myFeedbackList = [];

// 🔥 feedback state
let feedbackTargetId = null;
let selectedRating = 0;
let feedbackRequired = false;
let activeFeedbackProfileUserId = null;
let myReachedInCurrentMatch = false;
let otherReachedInCurrentMatch = false;
let currentMatchId = null;
let lastReachedNoticeMatchId = null;
let lastEndReasonNoticeMatchId = null;
let selectedEndReason = "";
let pendingFeedbackUsername = "";

// GPS smoothing
let lastPositions = [];
const MAX_POINTS = 5;
let hasFirstFix = false;

// simple view toggler with explicit display control (prevents stuck overlays)
function showSection(sectionId) {
  ["app-shell", "match-view", "post-match-view", "feedback-view"].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const isTarget = id === sectionId;
    el.classList.toggle("hidden", !isTarget);
    if (isTarget) {
      const flexViews = ["match-view", "post-match-view", "feedback-view"];
      el.style.display = flexViews.includes(id) ? "flex" : "block";
    } else {
      el.style.display = "none";
    }
  });

  if (sectionId !== "match-view") {
    closeEndMatchModal();
    const helpBox = document.getElementById("match-help-box");
    if (helpBox) helpBox.classList.add("hidden");
  }
}

// ==========================
// INIT
// ==========================
document.addEventListener("DOMContentLoaded", () => {
  // force-hide overlays on load in case of cached state
  const mv = document.getElementById("match-view");
  const pmv = document.getElementById("post-match-view");
  const fv = document.getElementById("feedback-view");
  const app = document.getElementById("app-shell");
  if (mv) { mv.classList.add("hidden"); mv.style.display = "none"; }
  if (pmv) { pmv.classList.add("hidden"); pmv.style.display = "none"; }
  if (fv) { fv.classList.add("hidden"); fv.style.display = "none"; }
  if (app) { app.classList.remove("hidden"); app.style.display = "block"; }

  initMap();
  initEndMatchReasonUI();
  fetchUserInfo();
  startTrustPoller();
  syncLiveIndicator();
  startRequestPoller();
  startAppNotificationPoller();
  startMatchPoller();
  initPushNotifications();
});

function startRequestPoller() {
  if (requestPoller) return;
  requestPoller = setInterval(pollRequests, 5000);
}

function startTrustPoller() {
  if (trustPoller) return;
  trustPoller = setInterval(fetchUserInfo, 8000);
}

function startAppNotificationPoller() {
  if (appNotifPoller) return;
  pollAppNotifications();
  appNotifPoller = setInterval(pollAppNotifications, 7000);
}

function pushBellNotification(kind, title, message, dedupeKey = null) {
  if (dedupeKey && appNotifications.some(n => n.dedupeKey === dedupeKey)) return;
  appNotifications.unshift({
    id: `n_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    kind,
    title,
    message,
    dedupeKey
  });
  if (appNotifications.length > 10) appNotifications = appNotifications.slice(0, 10);
  renderBellBox();
}

function dismissBellNotification(id) {
  appNotifications = appNotifications.filter(n => n.id !== id);
  renderBellBox();
}

function renderBellBox() {
  const box = document.getElementById("bellBox");
  const dot = document.getElementById("bell-dot");
  if (!box || !dot) return;

  const hasIncoming = !!incomingRequestData;
  const hasFeed = appNotifications.length > 0;
  dot.classList.toggle("hidden", !hasIncoming && !hasFeed);

  const incomingHtml = incomingRequestData ? `
    <div class="notif-item">
      <div class="notif-avatar clickable" title="View profile" onclick="openIncomingRequestProfile()">${(incomingRequestData.username || "?")[0]}</div>
      <div class="notif-text">
        <div class="name">${escapeHtml(incomingRequestData.username || "User")}</div>
        <div class="msg">wants to meet you</div>
      </div>
      <div class="notif-actions">
        <button class="accept-btn" onclick="respondRequest('accept')"><i class="fas fa-check"></i></button>
        <button class="decline-btn" onclick="respondRequest('decline')"><i class="fas fa-xmark"></i></button>
      </div>
    </div>
  ` : "";

  const feedHtml = appNotifications.map(n => `
    <div class="notif-item">
      <div class="notif-avatar">${(n.kind === "reach") ? "R" : ((n.kind === "admin_push" || n.kind === "admin") ? "A" : "i")}</div>
      <div class="notif-text">
        <div class="name">${escapeHtml(n.title)}</div>
        <div class="msg">${escapeHtml(n.message)}</div>
      </div>
      <div class="notif-actions">
        <button class="decline-btn" onclick="dismissBellNotification('${n.id}')"><i class="fas fa-xmark"></i></button>
      </div>
    </div>
  `).join("");

  box.innerHTML = incomingHtml + feedHtml + (!incomingHtml && !feedHtml ? `<div class="muted">No notifications</div>` : "");
}

function openIncomingRequestProfile() {
  if (!incomingRequestData || !incomingRequestData.sender_id) return;
  const requestId = incomingRequestData.id
    ? `?request_id=${encodeURIComponent(incomingRequestData.id)}`
    : "";
  window.location.href = `/profile/${incomingRequestData.sender_id}${requestId}`;
}

async function pollAppNotifications() {
  const res = await fetch("/api/notifications");
  if (!res.ok) return;

  const payload = await res.json().catch(() => ({}));
  const notifications = payload.notifications || [];
  notifications.forEach(n => {
    pushBellNotification(
      n.kind || "admin_push",
      n.title || "Admin Update",
      n.message || "",
      `admin_${n.id}`
    );
  });
}

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

async function syncLiveIndicator() {
  const liveEl = document.getElementById("live-indicator");
  const fabEl = document.getElementById("main-fab");
  if (!liveEl) return;

  const res = await fetch("/api/my_live_status");
  if (!res.ok) return;
  const data = await res.json().catch(() => ({}));
  const isLive = !!data.live;
  liveEl.classList.toggle("hidden", !isLive);
  if (fabEl) fabEl.classList.toggle("hidden", isLive);
}

// ==========================
// MATCH STATUS POLLING
// ==========================
function startMatchPoller() {
  if (matchPoller) return;
  matchPoller = setInterval(checkMatchStatus, 3000);
}

async function checkMatchStatus() {
  const res = await fetch("/api/match_status");
  if (!res.ok) return;

  const data = await res.json();
  if (data.matched) {
    currentMatchId = data.match_id || null;
    myReachedInCurrentMatch = !!data.i_reached;
    otherReachedInCurrentMatch = !!data.other_reached;
    if (!isMatched) enterMatchMode();
    refreshMatchUI(data);
    return;
  }

  if (isMatched) {
    isMatched = false;
    setTimelineState({ matched: true, onWay: true, reached: true, ended: true });
    const wasCompleted = myReachedInCurrentMatch && otherReachedInCurrentMatch;
    let endedByOtherNotice = null;
    if (data.ended_by_other && !wasCompleted) {
      const endedBy = data.ended_by || "Your match";
      const reason = data.end_reason || "No reason provided.";
      endedByOtherNotice = { endedBy, reason };
    }
    if (endedByOtherNotice && data.match_id && lastEndReasonNoticeMatchId !== data.match_id) {
      lastEndReasonNoticeMatchId = data.match_id;
      pushBellNotification(
        "end_reason",
        `${endedByOtherNotice.endedBy} ended the match`,
        `Reason: ${endedByOtherNotice.reason}`,
        `end_${data.match_id}`
      );
    }
    const flowType = wasCompleted ? "completed" : (endedByOtherNotice ? "other_ended" : "ended");
    showFeedbackForLatestTarget(endedByOtherNotice, flowType);
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
  nearbyUsers = users.map(u => ({
    ...u,
    vibes: u.vibe_tags ? u.vibe_tags.split(",").filter(Boolean) : [],
    distance_km: (u.lat && u.lon) ? haversine(myLat, myLon, u.lat, u.lon) : null
  }));

  nearbyMarkers.forEach(m => map.removeLayer(m));
  nearbyMarkers = [];

  nearbyUsers.forEach(user => {
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

  renderNearbyCards();
}

function formatMeetingDateTime(rawMeetTime) {
  if (!rawMeetTime) return "Not selected";

  const value = String(rawMeetTime).trim();
  const [datePart, timePartRaw] = value.split("T");

  if (datePart && timePartRaw) {
    const dt = new Date(`${datePart}T${timePartRaw}`);
    if (!Number.isNaN(dt.getTime())) {
      const dateLabel = dt.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric"
      });
      const timeLabel = dt.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
        hour12: true
      });
      return `${dateLabel}, ${timeLabel}`;
    }
    return `${datePart}, ${timePartRaw.slice(0, 5)}`;
  }

  if (/^\d{1,2}:\d{2}$/.test(value)) {
    const [hStr, mStr] = value.split(":");
    let hour = parseInt(hStr, 10);
    const minute = parseInt(mStr, 10);
    const ampm = hour >= 12 ? "PM" : "AM";
    hour = hour % 12 || 12;
    return `${hour}:${String(minute).padStart(2, "0")} ${ampm}`;
  }

  return value;
}

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function loadProfileFeedback(userId) {
  activeFeedbackProfileUserId = userId;
  const feedbackGroup = document.getElementById("p-feedback-group");
  const feedbackWrap = document.getElementById("p-feedback-wrap");
  const feedbackSummary = document.getElementById("p-feedback-summary");
  const feedbackDropdown = document.getElementById("p-feedback-dropdown");
  if (!feedbackGroup || !feedbackWrap) return;

  feedbackGroup.classList.remove("hidden");
  if (feedbackSummary) feedbackSummary.innerText = "Past Feedback";
  if (feedbackDropdown) feedbackDropdown.open = false;
  feedbackWrap.innerHTML = `<div class="muted">Loading feedback...</div>`;

  try {
    const res = await fetch(`/api/user_feedback/${userId}`);
    const payload = await res.json().catch(() => ({}));

    if (activeFeedbackProfileUserId !== userId) return;

    if (!res.ok) {
      feedbackWrap.innerHTML = `<div class="muted">Unable to load feedback</div>`;
      return;
    }

    const reviews = payload.reviews || [];
    if (feedbackSummary) feedbackSummary.innerText = `Past Feedback (${reviews.length})`;
    if (!reviews.length) {
      feedbackWrap.innerHTML = `<div class="muted">No feedback yet.</div>`;
      return;
    }

    feedbackWrap.innerHTML = reviews.map(r => `
      <div class="profile-feedback-item">
        <div class="profile-feedback-head">
          <span><strong>${escapeHtml(r.by)}</strong></span>
          <span>⭐ ${r.rating}/10</span>
        </div>
        ${r.comment ? `<div class="profile-feedback-comment">${escapeHtml(r.comment)}</div>` : ""}
        <div class="muted">${new Date(r.created_at * 1000).toLocaleString()}</div>
      </div>
    `).join("");
  } catch (_) {
    if (activeFeedbackProfileUserId !== userId) return;
    feedbackWrap.innerHTML = `<div class="muted">Unable to load feedback</div>`;
  }
}

// ==========================
// PROFILE
// ==========================
function openProfile(user) {
  selectedUserId = user.id;
  document.getElementById("p-avatar").innerText = user.username?.[0] || "?";
  document.getElementById("p-username").innerText = user.username;
  document.getElementById("p-score").innerText = user.trust_score ?? "--";
  const meeting = formatMeetingDateTime(user.meet_time);
  document.getElementById("p-place").innerText = user.place || "—";
  document.getElementById("p-intent").innerText = user.intent || "—";
  document.getElementById("p-time").innerText = meeting;

  const clueWrap = document.getElementById("p-clue-wrap");
  if (user.clue) {
    clueWrap.classList.remove("hidden");
    document.getElementById("p-clue").innerText = `👀 ${user.clue}`;
  } else {
    clueWrap.classList.add("hidden");
  }

  const bioWrap = document.getElementById("p-bio-wrap");
  if (user.bio) {
    bioWrap.classList.remove("hidden");
    document.getElementById("p-bio").innerText = user.bio;
  } else {
    bioWrap.classList.add("hidden");
  }

  const vibesGroup = document.getElementById("p-vibes-group");
  const vibesWrap = document.getElementById("p-vibes-wrap");
  if (user.vibes && user.vibes.length) {
    vibesGroup.classList.remove("hidden");
    vibesWrap.innerHTML = user.vibes.map(v => `<span class="profile-chip">${v}</span>`).join("");
  } else {
    vibesGroup.classList.add("hidden");
  }

  loadProfileFeedback(user.id);
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
// REPORT USER
// ==========================
async function reportUser() {
  if (!selectedUserId) {
    alert("No user selected");
    return;
  }
  const message = prompt("Why are you reporting this user? (optional)") || "";
  const res = await fetch("/api/report_user", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_id: selectedUserId, message })
  });
  if (!res.ok) {
    if (res.status === 401) { alert("Please sign in to report."); return; }
    alert("Unable to submit report");
    return;
  }
  alert("Report submitted");
}

// ==========================
// REQUESTS
// ==========================
async function pollRequests() {
  if (isMatched) return;

  const res = await fetch("/api/check_requests");
  if (!res.ok) return;

  const data = await res.json();
  if (data.type === "incoming" && data.data) {
    currentRequestId = data.data.id;
    incomingRequestData = data.data;
  } else {
    currentRequestId = null;
    incomingRequestData = null;
  }
  renderBellBox();
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
  incomingRequestData = null;
  renderBellBox();
  document.getElementById("bellBox").classList.add("hidden");

  if (action === "accept") enterMatchMode();
}

// ==========================
// MATCH MODE
// ==========================
function enterMatchMode() {
  if (isMatched) return;

  isMatched = true;
  clearInterval(requestPoller); requestPoller = null;

  // ensure overlays/sheets are closed so clicks aren't blocked
  closeAllSheets();
  const ov = document.getElementById("overlay");
  if (ov) {
    ov.classList.remove("active");
    ov.classList.add("hidden");
  }

  nearbyMarkers.forEach(m => map.removeLayer(m));
  nearbyMarkers = [];

  showSection("match-view");
  setTimelineState({ matched: true, onWay: true, reached: false, ended: false });
  refreshMatchUI({ i_reached: myReachedInCurrentMatch, other_reached: false });
}

// ==========================
// END MATCH → FEEDBACK
// ==========================
async function endMatch(reason = "") {
  const resEnd = await fetch("/api/end_match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason })
  });
  const endPayload = await resEnd.json().catch(() => ({}));
  if (!resEnd.ok) {
    if (endPayload.error === "reason_required") alert("Reason is required before ending this match.");
    else if (endPayload.error === "reason_too_long") alert("Reason must be 50 words or fewer.");
    else alert(endPayload.error || "Unable to end match");
    return;
  }

  setTimelineState({ matched: true, onWay: true, reached: true, ended: true });
  isMatched = false;
  closeEndMatchModal();
  const flowType = (myReachedInCurrentMatch && otherReachedInCurrentMatch) ? "completed" : "self_ended";
  showFeedbackForLatestTarget(null, flowType);
}

function initEndMatchReasonUI() {
  const chipWrap = document.getElementById("end-reason-chips");
  const textEl = document.getElementById("end-reason-text");
  if (!chipWrap || !textEl) return;

  chipWrap.querySelectorAll(".reason-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      chipWrap.querySelectorAll(".reason-chip").forEach(other => other.classList.remove("selected"));
      chip.classList.add("selected");
      selectedEndReason = chip.getAttribute("data-reason") || "";
      textEl.value = selectedEndReason;
      updateEndReasonCounter();
      clearEndReasonError();
    });
  });

  textEl.addEventListener("input", () => {
    selectedEndReason = "";
    chipWrap.querySelectorAll(".reason-chip").forEach(chip => chip.classList.remove("selected"));
    updateEndReasonCounter();
    clearEndReasonError();
  });

  updateEndReasonCounter();
}

function updateEndReasonCounter() {
  const textEl = document.getElementById("end-reason-text");
  const counterEl = document.getElementById("end-reason-counter");
  if (!textEl || !counterEl) return;
  const count = countWords(textEl.value);
  counterEl.innerText = `${count} / 50 words`;
  counterEl.style.color = count > 50 ? "#ff8080" : "";
}

function countWords(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

function showEndReasonError(message) {
  const errorEl = document.getElementById("end-reason-error");
  if (!errorEl) return;
  errorEl.innerText = message;
  errorEl.classList.remove("hidden");
}

function clearEndReasonError() {
  const errorEl = document.getElementById("end-reason-error");
  if (!errorEl) return;
  errorEl.classList.add("hidden");
  errorEl.innerText = "";
}

function openEndMatchModal() {
  const modal = document.getElementById("end-match-modal");
  const textEl = document.getElementById("end-reason-text");
  const chipWrap = document.getElementById("end-reason-chips");
  if (!modal) return;
  selectedEndReason = "";
  if (textEl) textEl.value = "";
  if (chipWrap) chipWrap.querySelectorAll(".reason-chip").forEach(chip => chip.classList.remove("selected"));
  clearEndReasonError();
  updateEndReasonCounter();
  modal.classList.remove("hidden");
}

function toggleMatchHelp() {
  const helpBox = document.getElementById("match-help-box");
  if (!helpBox) return;
  helpBox.classList.toggle("hidden");
}

function closeEndMatchModal() {
  const modal = document.getElementById("end-match-modal");
  if (!modal) return;
  modal.classList.add("hidden");
}

function handleMatchEndAction() {
  if (myReachedInCurrentMatch) {
    endMatch("");
    return;
  }
  openEndMatchModal();
}

async function confirmEndMatch() {
  const textEl = document.getElementById("end-reason-text");
  if (!textEl) return;

  const reason = textEl.value.trim();
  const words = countWords(reason);
  if (!myReachedInCurrentMatch && !reason) {
    showEndReasonError("Reason is required before ending if you have not reached.");
    return;
  }
  if (words > 50) {
    showEndReasonError("Reason must be 50 words or fewer.");
    return;
  }

  await endMatch(reason);
}

// ==========================
// FEEDBACK UI
// ==========================
function renderFeedbackForm() {
  const card = document.querySelector("#feedback-view .feedback-card");
  if (!card) return null;

  card.innerHTML = `
    <h2>⭐ Rate Your Match ⭐</h2>
    <p id="feedback-username"></p>
    <div id="rating-box"></div>
    <textarea id="feedback-text"
      placeholder="Write up to 50 words"
      style="width:90%;max-width:400px;height:80px;"></textarea>
    <div class="action-row">
      <button onclick="submitFeedback()" class="primary-btn">Submit</button>
    </div>
  `;
  return card;
}

function showFeedbackUI(username, endedByOtherNotice = null) {
  renderFeedbackForm();
  showSection("feedback-view");
  feedbackRequired = true;
  selectedRating = 0;
  const nameEl = document.getElementById("feedback-username");
  if (nameEl) nameEl.innerText = `@${username}`;
  renderEndedByOtherNotice(endedByOtherNotice);

  const ratingBox = document.getElementById("rating-box");
  if (ratingBox) {
    ratingBox.innerHTML = [1,2,3,4,5,6,7,8,9,10].map(n =>
      `<button class="rate-btn" onclick="selectRating(${n}, this)">${n}</button>`
    ).join("");
  }
}

function renderEndedByOtherNotice(endedByOtherNotice) {
  const card = document.querySelector("#feedback-view .feedback-card");
  if (!card) return;

  const existing = card.querySelector(".end-by-other-note");
  if (existing) existing.remove();

  if (!endedByOtherNotice) return;
  const endedBy = escapeHtml(endedByOtherNotice.endedBy || "Your match");
  const reason = escapeHtml(endedByOtherNotice.reason || "No reason shared.");

  const note = document.createElement("div");
  note.className = "end-by-other-note";
  note.innerHTML = `<strong>${endedBy} canceled the match.</strong><br>Reason: ${reason}`;
  const usernameEl = document.getElementById("feedback-username");
  if (usernameEl) usernameEl.insertAdjacentElement("afterend", note);
}

function showPostMatchOutcome(flowType, username, endedByOtherNotice = null) {
  const titleEl = document.getElementById("post-match-title");
  const subEl = document.getElementById("post-match-subtitle");
  const badgeEl = document.getElementById("post-match-badge");
  const reasonEl = document.getElementById("post-match-reason");
  const ctaEl = document.getElementById("post-match-cta");
  if (!titleEl || !subEl || !badgeEl || !reasonEl || !ctaEl) {
    showFeedbackUI(username, endedByOtherNotice);
    return;
  }

  reasonEl.classList.add("hidden");
  reasonEl.innerHTML = "";

  if (flowType === "other_ended") {
    const endedBy = escapeHtml((endedByOtherNotice && endedByOtherNotice.endedBy) || "Your match");
    const reason = escapeHtml((endedByOtherNotice && endedByOtherNotice.reason) || "No reason provided.");
    titleEl.innerText = "Match Canceled";
    subEl.innerText = `${endedBy} ended this match early.`;
    badgeEl.innerText = "Before meetup";
    reasonEl.innerHTML = `<strong>${endedBy} canceled the match.</strong><br>Reason: ${reason}`;
    reasonEl.classList.remove("hidden");
    ctaEl.innerText = "Continue to Rating";
  } else if (flowType === "completed") {
    titleEl.innerText = "Match Completed";
    subEl.innerText = "Both of you reached. Nice job closing the meetup properly.";
    badgeEl.innerText = "After meetup";
    ctaEl.innerText = "Rate Your Experience";
  } else if (flowType === "ended") {
    titleEl.innerText = "Match Ended";
    subEl.innerText = "This match ended. Share your quick rating to close this session.";
    badgeEl.innerText = "Session closed";
    ctaEl.innerText = "Continue to Rating";
  } else {
    titleEl.innerText = "Match Ended";
    subEl.innerText = "You ended this match. Share feedback to close this session.";
    badgeEl.innerText = "Before meetup";
    ctaEl.innerText = "Continue to Rating";
  }

  showSection("post-match-view");
}

function proceedToFeedback() {
  if (!pendingFeedbackUsername) {
    backToMap();
    return;
  }
  showFeedbackUI(pendingFeedbackUsername);
}

async function showFeedbackForLatestTarget(endedByOtherNotice = null, flowType = "self_ended") {
  const res = await fetch("/api/feedback_target");
  const target = await res.json().catch(() => ({}));
  if (!res.ok || target.error || !target.id) {
    backToMap();
    return;
  }
  feedbackTargetId = target.id;
  pendingFeedbackUsername = target.username;
  showPostMatchOutcome(flowType, target.username, endedByOtherNotice);
}

function refreshMatchUI(data) {
  const reachedBtn = document.getElementById("reached-btn");
  const endBtn = document.getElementById("end-match-btn");
  const statusEl = document.getElementById("reached-status");
  if (!reachedBtn || !statusEl || !endBtn) return;

  const iReached = !!data.i_reached;
  const otherReached = !!data.other_reached;
  otherReachedInCurrentMatch = otherReached;
  updateReachedPills(iReached, otherReached);

  reachedBtn.disabled = iReached;
  reachedBtn.innerText = iReached ? "Reached Confirmed" : "I Reached";
  endBtn.innerText = iReached ? "End Match" : "Emergency End Match";
  setTimelineState({
    matched: true,
    onWay: true,
    reached: iReached || otherReached,
    ended: false
  });

  if (iReached && otherReached) {
    statusEl.innerText = "Both sides reached. You can now end the match anytime.";
    return;
  }
  statusEl.innerText = otherReached
    ? "Your match has reached. Mark yourself reached when you arrive."
    : "You are matched. Mark reached when you get there.";
}

function updateReachedPills(iReached, otherReached) {
  const myPill = document.getElementById("reach-pill-you");
  const otherPill = document.getElementById("reach-pill-match");
  if (!myPill || !otherPill) return;

  myPill.innerText = `You: ${iReached ? "Reached" : "On the way"}`;
  otherPill.innerText = `Match: ${otherReached ? "Reached" : "On the way"}`;
  myPill.classList.toggle("is-on", !!iReached);
  otherPill.classList.toggle("is-on", !!otherReached);
}

async function markReached() {
  const res = await fetch("/api/mark_reached", { method: "POST" });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    alert(payload.error || "Unable to mark reached");
    return;
  }
  myReachedInCurrentMatch = true;
  refreshMatchUI({ i_reached: true, other_reached: otherReachedInCurrentMatch });
}

function setTimelineState({ matched, onWay, reached, ended }) {
  const steps = [
    ["tl-matched", matched],
    ["tl-way", onWay],
    ["tl-reached", reached],
    ["tl-ended", ended]
  ];

  steps.forEach(([id, active]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle("is-active", !!active);
  });
}

function selectRating(n, el) {
  selectedRating = n;
  document.querySelectorAll(".rate-btn").forEach(b => {
    b.classList.remove("selected");
  });
  el.classList.add("selected");
}

// ==========================
// FEEDBACK RESULT (VIEW)
// ==========================
function showFeedbackResult(rating, comment) {
  const card = document.querySelector("#feedback-view .feedback-card");
  if (!card) return window.location.reload();

  card.innerHTML = `
    <h2>✅ Feedback Submitted</h2>
    <p>Your rating: <strong>${rating}/10</strong></p>
    ${comment ? `<p style="max-width:400px;margin-top:10px;">"${comment}"</p>` : ""}
    <button onclick="finishFeedback()" class="primary-btn" style="margin-top:20px;">
      Continue
    </button>
  `;
}

// ==========================
// SUBMIT FEEDBACK
// ==========================
function submitFeedback() {
  const comment = document.getElementById("feedback-text").value.trim();
  const rating = selectedRating;

  if (!rating) {
    alert("Select a rating");
    return;
  }

  if (!feedbackTargetId) {
    alert("No feedback target found");
    return;
  }

  fetch("/api/submit_feedback", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      reviewed_id: feedbackTargetId, // ✅ REQUIRED
      rating: rating,
      comment: comment
    })
  })
  .then(async res => ({ ok: res.ok, data: await res.json().catch(() => ({})) }))
  .then(data => {
    if (data.ok && data.data.status === "submitted") {
      alert("Feedback submitted");
      feedbackRequired = false;
      feedbackTargetId = null;
      showFeedbackResult(rating, comment);
    } else {
      const err = data.data?.error;
      if (err === "invalid_rating") alert("Please select a rating between 1 and 10.");
      else if (err === "missing_data" || err === "invalid_data") alert("Feedback target is missing. Please try again.");
      else if (err === "unauthorized") alert("Please sign in again.");
      else alert("Failed to submit feedback. Please try again.");
    }
  })
  .catch(err => {
    console.error(err);
    alert("Server error");
  });
}

function finishFeedback() {
  feedbackRequired = false;
  selectedRating = 0;
  myReachedInCurrentMatch = false;
  otherReachedInCurrentMatch = false;
  currentMatchId = null;
  pendingFeedbackUsername = "";
  selectedEndReason = "";
  const reasonText = document.getElementById("end-reason-text");
  if (reasonText) reasonText.value = "";
  updateEndReasonCounter();
  startRequestPoller();
  startMatchPoller();
  fetchNearbyUsers();
  fetchUserInfo();
  syncLiveIndicator();
  showSection("app-shell");
}

// ==========================
// UI HELPERS
// ==========================
function openSheet(id) {
  const ov = document.getElementById("overlay");
  ov.classList.remove("hidden");
  requestAnimationFrame(() => ov.classList.add("active"));
  setTimeout(() => document.getElementById(id).classList.add("active"), 10);
}

function closeAllSheets() {
  document.querySelectorAll(".bottom-sheet").forEach(s =>
    s.classList.remove("active")
  );
  const ov = document.getElementById("overlay");
  ov.classList.remove("active");
  setTimeout(() => ov.classList.add("hidden"), 300);
}
async function fetchMyFeedback() {
  const res = await fetch("/api/my_feedback");
  if (!res.ok) {
    alert("Unable to load feedback");
    return;
  }
  const payload = await res.json();
  myFeedbackList = payload.reviews || [];
  showMyFeedbackUI();
}
function showMyFeedbackUI() {
  if (!myFeedbackList || myFeedbackList.length === 0) {
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

// ==========================
// MISSING FUNCTIONS
// ==========================
function goToSettings() {
  window.location.href = '/settings';
}

function toggleBellBox() {
  const box = document.getElementById('bellBox');
  if (!box) return;
  renderBellBox();
  box.classList.toggle('hidden');
}

function backToMap() {
  if (feedbackRequired || feedbackTargetId) {
    alert("Rating is mandatory. Submit feedback to continue.");
    return;
  }
  showSection("app-shell");
  if (!isMatched) {
    startRequestPoller();
    startMatchPoller();
    fetchNearbyUsers();
    syncLiveIndicator();
  }
}

// ==========================
// REPORT APP (feedback to admin)
// ==========================
async function reportApp() {
  const message = prompt("Describe the issue or feedback:") || "";
  if (!message.trim()) return;
  const res = await fetch("/api/report_app", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message })
  });
  if (!res.ok) {
    if (res.status === 401) { alert("Please sign in to send a report."); return; }
    alert("Unable to send report");
    return;
  }
  alert("Thanks! Report sent.");
}

// ==========================
// CHECKIN FUNCTIONS
// ==========================
async function confirmCheckIn() {
  if (!locationReady) {
    alert("Location not ready yet");
    return;
  }

  const place = document.getElementById("place").value.trim();
  const intent = document.getElementById("intent").value.trim();
  const meet_time = document.getElementById("meet_time").value;
  const bill = document.getElementById("bill").value;
  const clue = document.getElementById("visual-clue").value.trim();

  if (!place || !intent) {
    alert("Please fill in place and intent");
    return;
  }

  const data = {
    lat: myLat,
    lon: myLon,
    place,
    intent,
    meet_time,
    bill,
    clue
  };

  const res = await fetch("/api/checkin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });

  if (!res.ok) {
    alert("Failed to check in");
    return;
  }

  closeAllSheets();
  // Show live indicator
  document.getElementById("live-indicator").classList.remove("hidden");
  const fabEl = document.getElementById("main-fab");
  if (fabEl) fabEl.classList.add("hidden");
}

async function turnOffSpotlight() {
  const res = await fetch("/api/checkout", { method: "POST" });
  if (!res.ok) {
    alert("Failed to turn off");
    return;
  }

  document.getElementById("live-indicator").classList.add("hidden");
  const fabEl = document.getElementById("main-fab");
  if (fabEl) fabEl.classList.remove("hidden");
  fetchUserInfo();
}

// ==========================
// NEARBY CARDS
// ==========================
function renderNearbyCards() {
  const el = document.getElementById("nearby-carousel");
  if (!el) return;

  if (!nearbyUsers || nearbyUsers.length === 0) {
    el.innerHTML = "";
    return;
  }

  el.innerHTML = nearbyUsers.map(u => `
    <div class="nearby-card" onclick='openProfile(${JSON.stringify(u).replace(/'/g, "\\'")})'>
      <div class="card-top">
        <div class="card-avatar">${u.username?.[0] || "?"}</div>
        <div>
          <div class="card-name">${u.username}</div>
          <div class="card-score"><i class="fas fa-star" style="font-size:10px"></i> ${u.trust_score ?? "--"}</div>
        </div>
      </div>
      <div class="card-info"><i class="fas fa-map-pin"></i> ${u.place || "Somewhere nearby"}</div>
      <div class="card-info"><i class="fas fa-mug-hot"></i> ${u.intent || "Hanging out"}</div>
      ${u.bio ? `<div class="card-bio">${u.bio.slice(0, 80)}${u.bio.length > 80 ? "…" : ""}</div>` : ""}
      <div class="card-dist"><span>${u.distance_km ? `${u.distance_km.toFixed(1)} km` : "Nearby"}</span><i class="fas fa-chevron-right" style="font-size:10px;color:rgba(255,215,0,0.4)"></i></div>
    </div>
  `).join("");
}

// ==========================
// UTIL – distance
// ==========================
function haversine(lat1, lon1, lat2, lon2) {
  const toRad = deg => deg * Math.PI / 180;
  const R = 6371; // km
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLon/2)**2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  return R * c;
}

// ==========================
// PUSH NOTIFICATIONS
// ==========================
async function initPushNotifications() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return;
  const isLocalhost =
    location.hostname === "localhost" ||
    location.hostname === "127.0.0.1" ||
    location.hostname === "::1";
  if (location.protocol !== "https:" && !isLocalhost) return;

  try {
    const permission = Notification.permission === "default"
      ? await Notification.requestPermission()
      : Notification.permission;

    if (permission !== "granted") return;

    const keyRes = await fetch("/api/push/public_key");
    if (!keyRes.ok) return;
    const keyPayload = await keyRes.json().catch(() => ({}));
    const publicKey = keyPayload.public_key;
    if (!publicKey) return;

    const swReg = await navigator.serviceWorker.register("/sw.js");
    const existing = await swReg.pushManager.getSubscription();

    let subscription = existing;
    if (!subscription) {
      subscription = await swReg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey)
      });
    }

    await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(subscription.toJSON())
    });
  } catch (err) {
    console.warn("Push setup failed:", err);
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

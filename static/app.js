/* ============================================================
   Chronos — Frontend Logic (Production)
   ============================================================ */

const API = "";
let token = null;
let ws = null;
let conversationId = null;
let weekOffset = 0;
let currentOrgId = null;
let orgs = [];

// ── Bootstrap ──────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  token = localStorage.getItem("token");
  currentOrgId = localStorage.getItem("currentOrgId");
  document.getElementById("sidebar").style.display = "none";
  if (token) showApp();

  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.dataset.view) switchView(btn.dataset.view);
    });
  });
});

// ── Auth ───────────────────────────────────────────────────────

async function loginGoogle() {
  try {
    const res = await api("GET", "/api/v1/auth/google/login");
    window.location.href = res.authorization_url;
  } catch (e) { showToast("Google login failed: " + e.message, "error"); }
}

async function loginMicrosoft() {
  try {
    const res = await api("GET", "/api/v1/auth/microsoft/login");
    window.location.href = res.authorization_url;
  } catch (e) { showToast("Microsoft login failed: " + e.message, "error"); }
}

async function loginDev() {
  try {
    const res = await api("POST", "/api/v1/auth/dev-login");
    token = res.access_token;
    localStorage.setItem("token", token);
    showApp();
  } catch (e) { showToast("Dev login failed: " + e.message, "error"); }
}

function logout() {
  token = null; conversationId = null; currentOrgId = null;
  localStorage.removeItem("token");
  localStorage.removeItem("currentOrgId");
  if (ws) { ws.close(); ws = null; }
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.getElementById("login-view").classList.add("active");
  document.getElementById("sidebar").style.display = "none";
  document.getElementById("org-selector").style.display = "none";
  setStatus(false);
}

async function showApp() {
  document.getElementById("login-view").classList.remove("active");
  document.getElementById("sidebar").style.display = "flex";
  switchView("chat");
  connectWS();
  loadProfile();
  await loadOrganizations();
}

// ── Navigation ─────────────────────────────────────────────────

function switchView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.getElementById(name + "-view").classList.add("active");
  document.querySelectorAll(".nav-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  if (name === "calendar") loadEvents();
  if (name === "profile") loadProfile();
  if (name === "org-settings") loadOrgSettings();
  if (name === "settings") loadSettings();
  if (name === "email") loadEmailView();
  if (name === "scheduling") loadSchedulingView();
}

// ── Quick Chat (from quick-action buttons) ─────────────────────

function quickChat(msg) {
  const input = document.getElementById("chat-input");
  input.value = msg;
  input.focus();
  document.getElementById("chat-form").dispatchEvent(new Event("submit", { cancelable: true }));
}

// ── Organizations ──────────────────────────────────────────────

async function loadOrganizations() {
  try {
    orgs = await api("GET", "/api/v1/orgs/");
    const selector = document.getElementById("org-selector");
    const dropdown = document.getElementById("org-dropdown");

    if (orgs.length > 0) {
      selector.style.display = "flex";
      dropdown.innerHTML = orgs.map(o =>
        `<option value="${o.id}" ${o.id === currentOrgId ? "selected" : ""}>${esc(o.name)}</option>`
      ).join("");

      if (!currentOrgId || !orgs.find(o => o.id === currentOrgId)) {
        currentOrgId = orgs[0].id;
        localStorage.setItem("currentOrgId", currentOrgId);
      }
    } else {
      selector.style.display = "none";
      currentOrgId = null;
    }
  } catch (e) {
    orgs = [];
  }
}

function switchOrg() {
  const dropdown = document.getElementById("org-dropdown");
  currentOrgId = dropdown.value;
  localStorage.setItem("currentOrgId", currentOrgId);
  loadEvents();
}

async function loadOrgSettings() {
  const createPanel = document.getElementById("org-create-panel");
  const dashboard = document.getElementById("org-dashboard");

  if (!currentOrgId || orgs.length === 0) {
    createPanel.style.display = "block";
    dashboard.style.display = "none";
    return;
  }

  createPanel.style.display = "none";
  dashboard.style.display = "block";

  const org = orgs.find(o => o.id === currentOrgId);
  if (org) {
    document.getElementById("org-name-display").textContent = org.name;
    document.getElementById("org-member-count").textContent = `${org.member_count} members`;
    document.getElementById("org-domain-display").textContent = org.domain ? `@${org.domain}` : "";
  }

  await loadMembers();
  await loadProviders();
  await checkGoogleSetup();
}

async function createOrg() {
  const name = document.getElementById("new-org-name").value.trim();
  const domain = document.getElementById("new-org-domain").value.trim();
  if (!name) { showToast("Organization name is required", "error"); return; }

  try {
    const org = await api("POST", "/api/v1/orgs/", { name, domain: domain || null });
    currentOrgId = org.id;
    localStorage.setItem("currentOrgId", currentOrgId);
    await loadOrganizations();
    loadOrgSettings();
    showToast("Organization created!");
  } catch (e) { showToast("Failed: " + e.message, "error"); }
}

// ── Members ────────────────────────────────────────────────────

async function loadMembers() {
  if (!currentOrgId) return;
  const el = document.getElementById("members-list");
  try {
    const members = await api("GET", `/api/v1/orgs/${currentOrgId}/members`);
    if (!members.length) {
      el.innerHTML = '<div class="empty-state">No members yet</div>';
      return;
    }
    el.innerHTML = members.map(m => `
      <div class="member-row">
        <div class="member-info">
          <span class="member-avatar">${m.name ? m.name[0].toUpperCase() : "?"}</span>
          <div>
            <div class="member-name">${esc(m.name)}</div>
            <div class="member-email">${esc(m.email)}</div>
          </div>
        </div>
        <span class="badge badge-${m.role}">${m.role}</span>
      </div>
    `).join("");
  } catch (e) {
    el.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

function showInviteModal() { document.getElementById("invite-modal").style.display = "flex"; }
function hideInviteModal() { document.getElementById("invite-modal").style.display = "none"; }

async function inviteMember() {
  const email = document.getElementById("invite-email").value.trim();
  const role = document.getElementById("invite-role").value;
  if (!email) { showToast("Email is required", "error"); return; }
  if (!currentOrgId) { showToast("Select an organization first", "error"); return; }

  try {
    await api("POST", `/api/v1/orgs/${currentOrgId}/members`, { email, role });
    hideInviteModal();
    document.getElementById("invite-email").value = "";
    await loadMembers();
    await loadOrganizations();
    showToast("Invite sent!");
  } catch (e) { showToast("Invite failed: " + e.message, "error"); }
}

// ── Providers ──────────────────────────────────────────────────

async function loadProviders() {
  if (!currentOrgId) return;
  const el = document.getElementById("providers-list");
  try {
    const conns = await api("GET", `/api/v1/orgs/${currentOrgId}/providers`);
    if (!conns.length) {
      el.innerHTML = '<div class="empty-state" style="padding:1rem">No accounts connected yet. Connect below.</div>';
      return;
    }
    el.innerHTML = conns.map(c => `
      <div class="provider-row">
        <div class="provider-info">
          <span class="provider-icon">${c.provider === "google" ? "📧" : "📬"}</span>
          <div>
            <div class="provider-email">${esc(c.provider_email)}</div>
            <div class="provider-type">${c.provider === "google" ? "Gmail / Google Calendar" : "Outlook / Microsoft 365"}</div>
          </div>
        </div>
        <div class="provider-status">
          <span class="status-badge status-${c.status}">${c.status}</span>
          <span class="sync-icons">
            🔒 ${c.calendar_sync_enabled ? "📅" : ""} ${c.email_sync_enabled ? "📨" : ""}
          </span>
        </div>
      </div>
    `).join("") + '<div class="provider-security-note">🔒 Tokens encrypted at rest · OAuth2 — your password is never stored</div>';
  } catch (e) {
    el.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

async function connectProvider(provider) {
  if (!currentOrgId) {
    showToast("Create an organization first", "error");
    switchView("org-settings");
    return;
  }

  if (provider === "google") {
    try {
      const res = await api("GET", `/api/v1/orgs/${currentOrgId}/providers/google/auth`);
      if (res.authorization_url) {
        window.location.href = res.authorization_url;
        return;
      }
    } catch (e) {
      if (e.message && e.message.includes("not configured")) {
        showToast("Google OAuth not configured. Add credentials in Settings.", "error");
        switchView("settings");
        return;
      }
      showToast("Failed: " + e.message, "error");
      return;
    }
  }

  const email = prompt(`Enter your ${provider === "google" ? "Gmail" : "Outlook"} email address:`);
  if (!email) return;

  try {
    await api("POST", `/api/v1/orgs/${currentOrgId}/providers`, {
      provider,
      provider_email: email,
      scopes: provider === "google"
        ? "calendar.readonly,calendar.events,gmail.readonly"
        : "Calendars.ReadWrite,Mail.Read",
    });
    await loadProviders();
    showToast("Account connected!");
  } catch (e) { showToast("Failed: " + e.message, "error"); }
}

// ── Google Setup Check ─────────────────────────────────────────

let googleOAuthConfigured = false;

async function checkGoogleSetup() {
  const guide = document.getElementById("google-setup-guide");
  if (!guide) return;
  try {
    const res = await api("POST", "/api/v1/settings/test-connection", { service: "google_oauth" });
    googleOAuthConfigured = res.configured;
    guide.style.display = res.configured ? "none" : "block";
    if (!res.configured) {
      const uriEl = document.getElementById("google-redirect-uri");
      if (uriEl && res.redirect_uri) uriEl.textContent = res.redirect_uri;
    }
  } catch (e) {
    guide.style.display = "block";
    googleOAuthConfigured = false;
  }
}

// ── Chat ───────────────────────────────────────────────────────

const BOT_AVATAR_SVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>`;
const USER_AVATAR_SVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`;

async function sendMessage(e) {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const msg = input.value.trim();
  if (!msg) return;

  appendMessage("user", msg);
  input.value = "";
  document.getElementById("btn-send").disabled = true;

  if (ws && ws.readyState === WebSocket.OPEN) {
    showTyping();
    ws.send(JSON.stringify({ message: msg, conversation_id: conversationId, token }));
  } else {
    showTyping();
    try {
      const body = { message: msg };
      if (conversationId) body.conversation_id = conversationId;
      const res = await api("POST", "/api/v1/chat/", body);
      removeTyping();
      appendMessage("assistant", res.message);
      conversationId = res.conversation_id;
    } catch (err) {
      removeTyping();
      appendMessage("assistant", "⚠️ " + err.message);
    }
    document.getElementById("btn-send").disabled = false;
  }
}

function appendMessage(role, text) {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "message " + role;
  const avatar = role === "user" ? USER_AVATAR_SVG : BOT_AVATAR_SVG;
  let html = esc(text);
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  html = html.replace(/~~(.+?)~~/g, "<s>$1</s>");
  html = html.replace(/^• /gm, "· ");
  html = html.replace(/\n/g, "<br>");
  div.innerHTML = `<div class="message-avatar">${avatar}</div><div class="message-content">${html}</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function showTyping() {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "message assistant"; div.id = "typing";
  div.innerHTML = `<div class="message-avatar">${BOT_AVATAR_SVG}</div><div class="message-content typing-indicator"><span></span><span></span><span></span></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
function removeTyping() { const el = document.getElementById("typing"); if (el) el.remove(); }

// ── WebSocket ──────────────────────────────────────────────────

let wsRetryCount = 0;
const WS_MAX_RETRIES = 5;

function connectWS() {
  if (!token) return;
  if (ws) ws.close();
  wsRetryCount = 0;
  _doConnectWS();
}

function _doConnectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
  ws.onopen = () => { setStatus(true); wsRetryCount = 0; };
  ws.onclose = () => {
    setStatus(false);
    if (token && wsRetryCount < WS_MAX_RETRIES) {
      wsRetryCount++;
      setTimeout(_doConnectWS, 3000 * wsRetryCount);
    }
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "complete") {
      removeTyping();
      appendMessage("assistant", data.content);
      if (data.conversation_id) conversationId = data.conversation_id;
      document.getElementById("btn-send").disabled = false;
    } else if (data.type === "error") {
      removeTyping();
      appendMessage("assistant", "⚠️ " + data.content);
      document.getElementById("btn-send").disabled = false;
    }
  };
}

function setStatus(online) {
  const el = document.getElementById("connection-status");
  el.className = "conn-status " + (online ? "online" : "offline");
  const dot = el.querySelector(".conn-dot");
  const label = el.querySelector(".conn-label");
  if (label) label.textContent = online ? "Connected" : "Offline";
}

// ── Calendar ───────────────────────────────────────────────────

async function loadEvents() {
  const list = document.getElementById("events-list");
  const now = new Date();
  const start = new Date(now);
  start.setDate(start.getDate() + weekOffset * 7 - start.getDay());
  start.setHours(0, 0, 0, 0);
  const end = new Date(start); end.setDate(end.getDate() + 7);

  const opts = { month: "short", day: "numeric" };
  document.getElementById("week-label").textContent =
    start.toLocaleDateString(undefined, opts) + " — " + end.toLocaleDateString(undefined, opts);

  try {
    const events = await api("GET",
      `/api/v1/calendar/events?start=${start.toISOString()}&end=${end.toISOString()}`);

    if (!events.length) {
      list.innerHTML = '<div class="empty-state"><div class="empty-icon">📅</div>No events this week</div>';
      return;
    }

    const groups = {};
    events.forEach(ev => {
      const day = new Date(ev.start_time).toLocaleDateString(undefined, {
        weekday: "long", month: "short", day: "numeric"
      });
      (groups[day] = groups[day] || []).push(ev);
    });

    list.innerHTML = Object.entries(groups).map(([day, evts]) => `
      <div class="day-group">
        <div class="day-label">${day}</div>
        ${evts.map(ev => `
          <div class="event-card">
            <div>
              <div class="event-title">${esc(ev.title)}</div>
              <div class="event-time">${fmtTime(ev.start_time)} — ${fmtTime(ev.end_time)}</div>
            </div>
            <div class="event-status">${ev.status || "confirmed"}</div>
          </div>
        `).join("")}
      </div>
    `).join("");
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
  }
}

function shiftWeek(delta) { weekOffset += delta; loadEvents(); }

// ── Profile ────────────────────────────────────────────────────

async function loadProfile() {
  const el = document.getElementById("profile-data");
  try {
    const u = await api("GET", "/api/v1/auth/me");
    const pct = u.monthly_request_limit
      ? Math.round((u.monthly_requests_used / u.monthly_request_limit) * 100) : 0;
    el.innerHTML = `
      <div class="profile-row"><span class="profile-label">Email</span><span class="profile-value">${esc(u.email)}</span></div>
      <div class="profile-row"><span class="profile-label">Name</span><span class="profile-value">${esc(u.name)}</span></div>
      <div class="profile-row"><span class="profile-label">Timezone</span><span class="profile-value">${esc(u.timezone)}</span></div>
      <div class="profile-row"><span class="profile-label">Plan</span><span class="profile-value" style="text-transform:capitalize">${esc(u.plan)}</span></div>
      <div class="profile-row"><span class="profile-label">Organizations</span><span class="profile-value">${orgs.length}</span></div>
      <div class="profile-row">
        <span class="profile-label">Usage</span>
        <span class="profile-value">
          ${u.monthly_requests_used} / ${u.monthly_request_limit}
          <div class="usage-bar-wrapper"><div class="usage-bar" style="width:${pct}%"></div></div>
        </span>
      </div>
    `;
  } catch (err) {
    el.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
  }
}

// ── Settings ───────────────────────────────────────────────────

let settingsSchema = [];
let settingsValues = {};

async function loadSettings() {
  const container = document.getElementById("settings-sections");
  container.innerHTML = '<div class="empty-state">Loading settings...</div>';

  try {
    const res = await api("GET", "/api/v1/settings/");
    settingsSchema = res.schema_ || [];
    settingsValues = res.values || {};
    renderSettings();
  } catch (err) {
    container.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
  }
}

function renderSettings() {
  const container = document.getElementById("settings-sections");
  container.innerHTML = settingsSchema.map(section => `
    <div class="settings-section" data-section="${section.id}">
      <div class="settings-section-header" onclick="toggleSection('${section.id}')">
        <div class="settings-section-title">
          <span class="settings-section-icon">${section.icon}</span>
          <h3>${esc(section.title)}</h3>
        </div>
        <span class="settings-section-chevron" id="chevron-${section.id}">▸</span>
      </div>
      <div class="settings-section-body" id="section-body-${section.id}" style="display:none">
        ${section.fields.map(f => renderField(f)).join("")}
        <div class="settings-section-actions">
          <button class="btn btn-primary btn-sm" onclick="saveSection('${section.id}')">Save ${esc(section.title)}</button>
          <span class="save-indicator" id="indicator-${section.id}"></span>
        </div>
      </div>
    </div>
  `).join("");
}

function renderField(field) {
  const val = settingsValues[field.key] || "";
  const hint = field.hint ? `<span class="field-hint">${esc(field.hint)}</span>` : "";

  if (field.type === "select") {
    const options = (field.options || []).map(opt =>
      `<option value="${esc(opt)}" ${val === opt ? "selected" : ""}>${esc(opt)}</option>`
    ).join("");
    return `
      <div class="settings-field">
        <label class="settings-label">${esc(field.label)} ${hint}</label>
        <select class="input settings-input" data-key="${field.key}">
          <option value="">— select —</option>
          ${options}
        </select>
      </div>`;
  }

  if (field.type === "secret") {
    return `
      <div class="settings-field">
        <label class="settings-label">${esc(field.label)} ${hint}</label>
        <div class="secret-input-wrapper">
          <input type="password" class="input settings-input" data-key="${field.key}"
                 value="${esc(val)}" placeholder="Enter value..." autocomplete="off" />
          <button type="button" class="btn btn-icon secret-toggle"
                  onclick="toggleSecret(this)">👁</button>
        </div>
      </div>`;
  }

  if (field.type === "number") {
    return `
      <div class="settings-field">
        <label class="settings-label">${esc(field.label)} ${hint}</label>
        <input type="number" class="input settings-input" data-key="${field.key}"
               value="${esc(val)}" />
      </div>`;
  }

  return `
    <div class="settings-field">
      <label class="settings-label">${esc(field.label)} ${hint}</label>
      <input type="text" class="input settings-input" data-key="${field.key}"
             value="${esc(val)}" placeholder="Enter value..." />
    </div>`;
}

function toggleSection(sectionId) {
  const body = document.getElementById("section-body-" + sectionId);
  const chevron = document.getElementById("chevron-" + sectionId);
  if (body.style.display === "none") {
    body.style.display = "block";
    chevron.textContent = "▾";
  } else {
    body.style.display = "none";
    chevron.textContent = "▸";
  }
}

function toggleSecret(btn) {
  const input = btn.parentElement.querySelector("input");
  if (input.type === "password") {
    input.type = "text";
    btn.textContent = "🙈";
  } else {
    input.type = "password";
    btn.textContent = "👁";
  }
}

async function saveSection(sectionId) {
  const section = settingsSchema.find(s => s.id === sectionId);
  if (!section) return;

  const values = {};
  section.fields.forEach(f => {
    const input = document.querySelector(`.settings-input[data-key="${f.key}"]`);
    if (input) values[f.key] = input.value;
  });

  const indicator = document.getElementById("indicator-" + sectionId);
  indicator.textContent = "Saving...";
  indicator.className = "save-indicator saving";

  try {
    const res = await api("PUT", "/api/v1/settings/", { values });
    indicator.textContent = `✓ ${res.message}`;
    indicator.className = "save-indicator success";
    Object.assign(settingsValues, values);
    setTimeout(() => { indicator.textContent = ""; indicator.className = "save-indicator"; }, 3000);
  } catch (err) {
    indicator.textContent = "✕ " + err.message;
    indicator.className = "save-indicator error";
    setTimeout(() => { indicator.textContent = ""; indicator.className = "save-indicator"; }, 4000);
  }
}

// ── Email Intelligence ─────────────────────────────────────────

let emailSuggestions = [];
let scannedEmails = [];
let emailCurrentTab = "emails";

async function loadEmailView() {
  await Promise.all([
    loadEmailProviders(),
    loadScannedEmails(),
    loadEmailSuggestions(),
    loadDrafts(),
    loadUserPreferences(),
  ]);
}

async function loadEmailProviders() {
  const bar = document.getElementById("email-providers-bar");
  try {
    const providers = await api("GET", "/api/v1/email/providers");
    if (!providers.length) {
      bar.innerHTML = `
        <div class="email-provider-notice">
          <span>⚠️</span>
          <span>No email accounts connected. <a href="#" onclick="switchView('org-settings'); return false;">Connect one</a> to get started.</span>
        </div>`;
      return;
    }
    bar.innerHTML = providers.map(p => `
      <div class="email-provider-chip ${p.email_sync_enabled ? 'active' : 'inactive'}">
        <span>${p.provider === 'google' ? '📧' : '📬'}</span>
        <span class="email-provider-email">${esc(p.email)}</span>
        <span class="email-provider-badge">${p.email_sync_enabled ? '✓ Syncing' : 'Disabled'}</span>
        ${p.last_sync_at ? `<span class="email-provider-sync">Last: ${fmtRelativeTime(p.last_sync_at)}</span>` : ''}
      </div>
    `).join("");
  } catch (e) {
    bar.innerHTML = `<div class="email-provider-notice"><span>⚠️ ${esc(e.message)}</span></div>`;
  }
}

// ── Scanned Emails ─────────────────────────────────────────────

async function loadScannedEmails() {
  try {
    scannedEmails = await api("GET", "/api/v1/email/scanned-emails?limit=50");
    document.getElementById("tab-count-emails").textContent = scannedEmails.length;
    if (emailCurrentTab === "emails") {
      renderScannedEmails();
    }
  } catch (e) {
    scannedEmails = [];
    document.getElementById("tab-count-emails").textContent = "0";
  }
}

function renderScannedEmails() {
  const container = document.getElementById("email-all-list");
  if (!scannedEmails.length) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon">📬</div>No scanned emails yet. Click Scan to fetch from your inbox.</div>';
    return;
  }
  container.innerHTML = scannedEmails.map((email, idx) => renderScannedEmailCard(email, idx)).join("");
}

function renderScannedEmailCard(email, idx) {
  const categoryIcons = {
    meeting_request: "🤝", meeting_reschedule: "📝", meeting_cancellation: "🚫",
    task_assignment: "📋", deadline_reminder: "⏰", appointment: "📅",
    event_invitation: "🎟️", follow_up: "🔄", non_actionable: "📧",
  };
  const icon = categoryIcons[email.analysis_category] || "📧";
  const categoryLabel = (email.analysis_category || "unknown").replace(/_/g, " ");
  const confidence = Math.round((email.analysis_confidence || 0) * 100);
  const receivedAt = email.received_at ? new Date(email.received_at) : null;
  const timeStr = receivedAt ? fmtRelativeTime(email.received_at) : "";

  return `
    <div class="scanned-email-card ${email.is_actionable ? 'actionable' : ''}" onclick="toggleEmailDetail(${idx})">
      <div class="scanned-email-header">
        <div class="scanned-email-meta">
          <span class="scanned-email-icon">${icon}</span>
          <div class="scanned-email-info">
            <div class="scanned-email-subject">${esc(email.subject || '(no subject)')}</div>
            <div class="scanned-email-sender">
              ${esc(email.sender_name || email.sender_email)}
              ${email.sender_name ? `<span class="text-muted">&lt;${esc(email.sender_email)}&gt;</span>` : ''}
            </div>
          </div>
        </div>
        <div class="scanned-email-right">
          <span class="scanned-email-time">${timeStr}</span>
          ${email.is_actionable
            ? `<span class="scanned-email-badge actionable-badge">✨ ${categoryLabel} · ${confidence}%</span>`
            : `<span class="scanned-email-badge">Not actionable</span>`
          }
          ${email.has_attachments ? '<span class="scanned-email-attachment">📎</span>' : ''}
          <span class="scanned-email-chevron" id="chevron-email-${idx}">▸</span>
        </div>
      </div>
      <div class="scanned-email-detail" id="email-detail-${idx}" style="display:none">
        <div class="email-detail-grid">
          <div class="email-detail-row">
            <span class="email-detail-label">From</span>
            <span>${esc(email.sender_name)} &lt;${esc(email.sender_email)}&gt;</span>
          </div>
          <div class="email-detail-row">
            <span class="email-detail-label">To</span>
            <span>${(email.recipients || []).map(r => esc(r)).join(", ") || "—"}</span>
          </div>
          <div class="email-detail-row">
            <span class="email-detail-label">Date</span>
            <span>${receivedAt ? receivedAt.toLocaleString() : "—"}</span>
          </div>
          <div class="email-detail-row">
            <span class="email-detail-label">Category</span>
            <span>${icon} ${categoryLabel} (${confidence}% confidence)</span>
          </div>
          ${email.analysis_summary ? `
          <div class="email-detail-row">
            <span class="email-detail-label">Analysis</span>
            <span>${esc(email.analysis_summary)}</span>
          </div>` : ''}
        </div>
        <div class="email-detail-body">
          <div class="email-detail-body-label">Email Body</div>
          <div class="email-detail-body-text">${esc(email.body_text || email.body_snippet || '(no body)')}</div>
        </div>
        ${email.suggestion_id ? `
        <div class="email-detail-actions">
          <span class="scanned-email-badge actionable-badge">📋 Suggestion created — see Pending tab</span>
        </div>` : ''}
      </div>
    </div>
  `;
}

function toggleEmailDetail(idx) {
  const detail = document.getElementById("email-detail-" + idx);
  const chevron = document.getElementById("chevron-email-" + idx);
  if (detail.style.display === "none") {
    detail.style.display = "block";
    chevron.textContent = "▾";
  } else {
    detail.style.display = "none";
    chevron.textContent = "▸";
  }
}

// ── Email Suggestions ──────────────────────────────────────────

async function loadEmailSuggestions() {
  const container = document.getElementById("email-suggestions");
  container.innerHTML = '<div class="empty-state">Loading suggestions...</div>';

  try {
    const [pending, approved, rejected] = await Promise.all([
      api("GET", "/api/v1/email/suggestions?status=pending&limit=50"),
      api("GET", "/api/v1/email/suggestions?status=approved&limit=50"),
      api("GET", "/api/v1/email/suggestions?status=rejected&limit=30"),
    ]);

    emailSuggestions = { pending, approved, rejected };

    document.getElementById("tab-count-pending").textContent = pending.length;
    document.getElementById("tab-count-approved").textContent = approved.length;
    document.getElementById("tab-count-rejected").textContent = rejected.length;

    if (emailCurrentTab !== "emails" && emailCurrentTab !== "history") {
      renderEmailTab(emailCurrentTab);
    }
  } catch (e) {
    container.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

function switchEmailTab(tab) {
  emailCurrentTab = tab;
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab === tab);
  });

  const allEmails = document.getElementById("email-all-list");
  const suggestions = document.getElementById("email-suggestions");
  const history = document.getElementById("email-history");
  const draftsList = document.getElementById("email-drafts-list");
  const analytics = document.getElementById("email-analytics");

  allEmails.style.display = "none";
  suggestions.style.display = "none";
  history.style.display = "none";
  draftsList.style.display = "none";
  analytics.style.display = "none";

  if (tab === "emails") {
    allEmails.style.display = "block";
    renderScannedEmails();
  } else if (tab === "drafts") {
    draftsList.style.display = "block";
    renderDrafts();
  } else if (tab === "history") {
    history.style.display = "block";
    loadScanHistory();
  } else if (tab === "analytics") {
    analytics.style.display = "block";
    loadAnalytics();
  } else {
    suggestions.style.display = "block";
    renderEmailTab(tab);
  }
}

function renderEmailTab(tab) {
  const container = document.getElementById("email-suggestions");
  const items = emailSuggestions[tab] || [];

  if (!items.length) {
    const msgs = {
      pending: "No pending suggestions. Scan your inbox to find meetings & tasks!",
      approved: "No approved suggestions yet.",
      rejected: "No rejected suggestions."
    };
    container.innerHTML = `<div class="empty-state"><div class="empty-icon">💡</div>${msgs[tab] || "No items."}</div>`;
    return;
  }

  container.innerHTML = items.map(s => renderSuggestionCard(s, tab)).join("");
}

function renderSuggestionCard(s, tab) {
  const categoryIcons = {
    meeting_request: "🤝", meeting_update: "📝", meeting_cancellation: "🚫",
    task_assignment: "📋", deadline_reminder: "⏰", event_invitation: "🎟️",
    rsvp_request: "✉️", scheduling_poll: "📊", follow_up: "🔄",
  };
  const priorityColors = { high: "var(--red)", medium: "var(--orange)", low: "var(--green)" };

  const icon = categoryIcons[s.category] || "📧";
  const priorityColor = priorityColors[s.priority] || "var(--text2)";
  const categoryLabel = s.category.replace(/_/g, " ");

  let timeInfo = "";
  if (s.proposed_start) {
    const start = new Date(s.proposed_start);
    const end = s.proposed_end ? new Date(s.proposed_end) : null;
    timeInfo = `
      <div class="suggestion-time">
        📅 ${start.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
        · ${start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
        ${end ? '— ' + end.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''}
      </div>`;
  }

  let conflictBadge = "";
  if (s.has_conflict) {
    conflictBadge = `<span class="suggestion-conflict">⚠️ Conflict: ${esc(s.conflict_details)}</span>`;
  }

  let alternativeSlots = "";
  if (s.alternative_slots && s.alternative_slots.length > 0) {
    alternativeSlots = `
      <div class="suggestion-alternatives">
        <span class="alt-label">Alternative times:</span>
        ${s.alternative_slots.slice(0, 3).map(slot => {
          const st = new Date(slot.start);
          return `<span class="alt-slot">${st.toLocaleDateString(undefined, { weekday: 'short' })} ${st.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</span>`;
        }).join("")}
      </div>`;
  }

  let attendeesInfo = "";
  if (s.attendees && s.attendees.length > 0) {
    attendeesInfo = `<div class="suggestion-attendees">👥 ${s.attendees.slice(0, 4).map(a => esc(a)).join(", ")}${s.attendees.length > 4 ? ` +${s.attendees.length - 4} more` : ""}</div>`;
  }

  let locationInfo = "";
  if (s.location) {
    locationInfo = `<div class="suggestion-location">📍 ${esc(s.location)}</div>`;
  }

  let actions = "";
  if (tab === "pending") {
    actions = `
      <div class="suggestion-actions">
        <button class="btn btn-primary btn-sm" onclick="approveSuggestion('${s.id}')">✅ Approve & Create</button>
        <button class="btn btn-ghost btn-sm" onclick="rejectSuggestion('${s.id}')">Dismiss</button>
      </div>`;
  } else if (tab === "approved") {
    actions = `<div class="suggestion-status-badge status-approved">✅ Event Created</div>`;
  } else {
    actions = `<div class="suggestion-status-badge status-rejected">Dismissed</div>`;
  }

  return `
    <div class="suggestion-card">
      <div class="suggestion-card-header">
        <div class="suggestion-meta">
          <span class="suggestion-icon">${icon}</span>
          <span class="suggestion-category">${categoryLabel}</span>
          <span class="suggestion-priority" style="color:${priorityColor}">● ${s.priority}</span>
          <span class="suggestion-confidence">${Math.round(s.confidence * 100)}%</span>
        </div>
        ${conflictBadge}
      </div>
      <div class="suggestion-card-body">
        <h4 class="suggestion-title">${esc(s.title)}</h4>
        <div class="suggestion-email-source">
          <span class="suggestion-from">From: ${esc(s.email_sender)}</span>
          <span class="suggestion-subject">Re: ${esc(s.email_subject)}</span>
        </div>
        <p class="suggestion-desc">${esc(s.description)}</p>
        ${timeInfo}
        ${locationInfo}
        ${attendeesInfo}
        ${alternativeSlots}
      </div>
      ${actions}
    </div>
  `;
}

async function triggerEmailScan() {
  const btn = document.getElementById("btn-email-scan");
  const statusBar = document.getElementById("email-scan-status");
  const statusText = document.getElementById("email-scan-text");
  const provider = document.getElementById("email-provider-select").value;
  const sinceHours = parseInt(document.getElementById("email-since-hours").value);

  btn.disabled = true;
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin"><path d="M21 12a9 9 0 11-6.219-8.56"/></svg> Scanning...`;
  statusBar.style.display = "flex";
  statusBar.className = "scan-banner scanning";
  statusText.textContent = `Scanning ${provider === 'google' ? 'Gmail' : 'Outlook'} inbox for the last ${sinceHours}h...`;

  try {
    const result = await api("POST", "/api/v1/email/scan", {
      provider,
      since_hours: sinceHours,
      max_emails: 50,
    });

    statusBar.className = "scan-banner success";
    statusText.textContent = `✅ Scanned ${result.emails_scanned} emails · ${result.actionable_found} actionable · ${result.suggestions_created} suggestions`;

    if (result.errors && result.errors.length > 0) {
      statusBar.className = "scan-banner warning";
      statusText.textContent += ` · ⚠️ ${result.errors.length} error(s)`;
    }

    await Promise.all([loadEmailSuggestions(), loadScannedEmails()]);
    if (scannedEmails.length > 0) switchEmailTab("emails");
    if (emailSuggestions.pending && emailSuggestions.pending.length > 0) switchEmailTab("pending");
  } catch (e) {
    statusBar.className = "scan-banner error";
    statusText.textContent = `Scan failed: ${e.message}`;
  }

  btn.disabled = false;
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Scan`;

  setTimeout(() => { statusBar.style.display = "none"; }, 10000);
}

async function approveSuggestion(id) {
  const card = event.target.closest(".suggestion-card");
  const btns = card.querySelectorAll("button");
  btns.forEach(b => b.disabled = true);

  try {
    const result = await api("POST", `/api/v1/email/suggestions/${id}/approve`);
    const actionsDiv = card.querySelector(".suggestion-actions");
    actionsDiv.innerHTML = `<div class="suggestion-status-badge status-approved">✅ Event Created: ${esc(result.title)}</div>`;
    await loadEmailSuggestions();
    showToast("Event created!");
  } catch (e) {
    showToast("Failed to approve: " + e.message, "error");
    btns.forEach(b => b.disabled = false);
  }
}

async function rejectSuggestion(id) {
  const card = event.target.closest(".suggestion-card");
  const btns = card.querySelectorAll("button");
  btns.forEach(b => b.disabled = true);

  try {
    await api("POST", `/api/v1/email/suggestions/${id}/reject`);
    const actionsDiv = card.querySelector(".suggestion-actions");
    actionsDiv.innerHTML = `<div class="suggestion-status-badge status-rejected">Dismissed</div>`;
    await loadEmailSuggestions();
  } catch (e) {
    showToast("Failed to reject: " + e.message, "error");
    btns.forEach(b => b.disabled = false);
  }
}

async function loadScanHistory() {
  const container = document.getElementById("email-history");
  container.innerHTML = '<div class="empty-state">Loading scan history...</div>';

  try {
    const history = await api("GET", "/api/v1/email/scan-history?limit=20");
    if (!history.length) {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div>No scans yet. Click Scan to get started.</div>';
      return;
    }

    container.innerHTML = `
      <div class="scan-history-list">
        ${history.map(h => `
          <div class="scan-history-row">
            <div class="scan-history-info">
              <span class="scan-history-provider">${h.provider === 'google' ? '📧 Gmail' : '📬 Outlook'}</span>
              <span class="scan-history-time">${fmtRelativeTime(h.scanned_at)}</span>
            </div>
            <div class="scan-history-stats">
              <span class="stat-pill">📨 ${h.emails_scanned} scanned</span>
              <span class="stat-pill accent">💡 ${h.actionable_found} actionable</span>
              <span class="stat-pill green">📋 ${h.suggestions_created} created</span>
              ${h.errors_count > 0 ? `<span class="stat-pill red">⚠️ ${h.errors_count} errors</span>` : ''}
            </div>
          </div>
        `).join("")}
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

// ── Scheduling View ──────────────────────────────────────────────

let schedulingCurrentTab = "links";
let createdLinks = [];

async function loadSchedulingView() {
  try { await loadGuides(); } catch (_) {}
  try { await loadOnboardingStatus(); } catch (_) {}
}

function switchSchedulingTab(tab) {
  schedulingCurrentTab = tab;
  document.querySelectorAll("[data-stab]").forEach(t => {
    t.classList.toggle("active", t.dataset.stab === tab);
  });
  const panels = {
    links: "sched-links-panel", booking: "sched-booking-panel",
    hook: "sched-hook-panel", guides: "sched-guides-panel",
    onboarding: "sched-onboarding-panel",
  };
  Object.values(panels).forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
  });
  const active = panels[tab];
  if (active) document.getElementById(active).style.display = "block";
}

// Scheduling Links
async function createSchedulingLink() {
  const type = document.getElementById("link-type-select").value;
  const attendeeEmail = document.getElementById("link-attendee-email").value.trim();
  const duration = parseInt(document.getElementById("link-duration").value);
  if (!attendeeEmail) { showToast("Enter attendee email first", "error"); return; }
  const btn = document.querySelector("#sched-links-panel .btn-primary");
  btn.disabled = true; btn.textContent = "Creating…";
  try {
    const path = type === "availability"
      ? "/api/v1/email/scheduling-links/availability"
      : "/api/v1/email/scheduling-links/suggested";
    const body = type === "availability"
      ? { attendee_email: attendeeEmail, duration_minutes: duration, days_ahead: 7 }
      : { attendee_email: attendeeEmail, duration_minutes: duration, suggested_windows: [] };
    const res = await api("POST", path, body);
    createdLinks.unshift({ url: res.url, mode: res.mode, attendee: attendeeEmail, duration, created_at: new Date().toISOString() });
    renderSchedulingLinks();
    document.getElementById("link-attendee-email").value = "";
    showToast("Link created!");
  } catch (e) { showToast("Failed: " + e.message, "error"); }
  btn.disabled = false; btn.textContent = "+ Create Link";
}

function renderSchedulingLinks() {
  const container = document.getElementById("sched-links-list");
  if (!createdLinks.length) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon">🔗</div>No scheduling links yet. Create one above.</div>';
    return;
  }
  container.innerHTML = createdLinks.map((l, i) => `
    <div class="link-card">
      <div class="link-card-meta">
        <span class="link-mode-badge">${l.mode === "availability" ? "📅 Availability" : "💡 Suggested"}</span>
        <span class="link-duration">${l.duration} min · ${esc(l.attendee)}</span>
        <span class="link-time">${fmtRelativeTime(l.created_at)}</span>
      </div>
      <div class="link-url-row">
        <code class="link-url">${esc(l.url)}</code>
        <button class="btn btn-ghost btn-sm" onclick="copyLinkUrl(${i})">\ud83d\udccb Copy</button>
      </div>
    </div>
  `).join("");
}

function copyLinkUrl(idx) {
  const url = createdLinks[idx]?.url;
  if (!url) return;
  navigator.clipboard.writeText(url)
    .then(() => showToast("Link copied!"))
    .catch(() => {
      const ta = document.createElement("textarea");
      ta.value = url; document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); document.body.removeChild(ta);
      showToast("Link copied!");
    });
}

// Booking Page
async function lookupBookingSlots() {
  const url = document.getElementById("booking-url").value.trim();
  const duration = parseInt(document.getElementById("booking-duration").value);
  const daysAhead = parseInt(document.getElementById("booking-days").value);
  const result = document.getElementById("booking-slots-result");
  if (!url) { showToast("Enter a booking page URL", "error"); return; }
  result.innerHTML = '<div class="empty-state">Fetching available slots…</div>';
  try {
    const slots = await api("POST", "/api/v1/email/booking-page/slots", {
      url, duration_minutes: duration, days_ahead: daysAhead,
    });
    if (!slots.length) {
      result.innerHTML = '<div class="empty-state">No available slots found in that window.</div>';
      return;
    }
    result.innerHTML = `
      <div class="booking-slots-header"><h4>${slots.length} available slot${slots.length !== 1 ? "s" : ""}</h4></div>
      <div class="booking-slots-grid">
        ${slots.map(s => {
          const start = new Date(s.start || s.start_time || s);
          const end = s.end ? new Date(s.end) : null;
          const dateStr = start.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
          const timeStr = start.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) +
            (end ? " – " + end.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "");
          return `<div class="booking-slot">${dateStr}<br><strong>${timeStr}</strong></div>`;
        }).join("")}
      </div>`;
  } catch (e) { result.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
}

// Message Hook
async function submitMessageHook() {
  const message = document.getElementById("hook-message").value.trim();
  const sender = document.getElementById("hook-sender").value.trim();
  const source = document.getElementById("hook-source").value;
  const autoCreate = document.getElementById("hook-auto-create").checked;
  const result = document.getElementById("hook-result");
  if (!message) { showToast("Paste a message first", "error"); return; }
  result.innerHTML = '<div class="empty-state">Analysing…</div>';
  try {
    const res = await api("POST", "/api/v1/email/hook/message", {
      message, sender: sender || "unknown", source, auto_create: autoCreate,
    });
    renderHookResult(res, result);
  } catch (e) { result.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
}

function renderHookResult(res, container) {
  if (!res.extracted) {
    container.innerHTML = '<div class="hook-result-card no-meeting"><div class="hook-result-icon">🤷</div>No scheduling commitment found in this message.</div>';
    return;
  }
  const conf = Math.round((res.confidence || 0) * 100);
  const confColor = conf >= 85 ? "var(--green)" : conf >= 60 ? "var(--orange)" : "var(--red)";
  const dateStr = res.proposed_start ? new Date(res.proposed_start).toLocaleString() : "\u2014";
  container.innerHTML = `
    <div class="hook-result-card">
      <div class="hook-result-header">
        <span class="hook-confidence" style="color:${confColor}">${conf}% confidence</span>
        ${res.event_created ? '<span class="hook-badge event-created">\u2705 Event Created</span>' : ""}
      </div>
      <div class="hook-result-grid">
        ${res.title ? `<div class="hook-row"><span>Title</span><strong>${esc(res.title)}</strong></div>` : ""}
        ${res.proposed_start ? `<div class="hook-row"><span>When</span><strong>${dateStr}</strong></div>` : ""}
        ${res.duration_minutes ? `<div class="hook-row"><span>Duration</span><strong>${res.duration_minutes} min</strong></div>` : ""}
        ${res.location ? `<div class="hook-row"><span>Location</span><strong>${esc(res.location)}</strong></div>` : ""}
        ${res.attendees && res.attendees.length ? `<div class="hook-row"><span>Attendees</span><strong>${res.attendees.map(a => esc(a)).join(", ")}</strong></div>` : ""}
      </div>
      ${!res.event_created ? `<div class="hook-result-actions"><button class="btn btn-primary btn-sm" onclick="hookAutoCreate()">\ud83d\udcc5 Create Event</button></div>` : ""}
    </div>`;
  window._lastHookResult = res;
}

async function hookAutoCreate() {
  const message = document.getElementById("hook-message").value.trim();
  const sender = document.getElementById("hook-sender").value.trim();
  const source = document.getElementById("hook-source").value;
  const result = document.getElementById("hook-result");
  try {
    const res = await api("POST", "/api/v1/email/hook/message", {
      message, sender: sender || "unknown", source, auto_create: true,
    });
    renderHookResult(res, result);
    if (res.event_created) showToast("Event created!");
  } catch (e) { showToast("Failed: " + e.message, "error"); }
}

// Guides
async function loadGuides() {
  const container = document.getElementById("guides-content");
  if (!container) return;
  try {
    const guides = await api("GET", "/api/v1/email/guides");
    renderGuides(guides, container);
  } catch (e) {
    if (container) container.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

function renderGuides(guides, container) {
  const prefs = guides.scheduling_preferences || {};
  const style = guides.style_guide || {};
  container.innerHTML = `
    <div class="guide-section">
      <div class="guide-section-header">
        <h4>\ud83d\udcc6 Scheduling Preferences</h4>
        <button class="btn btn-ghost btn-sm" onclick="saveSchedulingPrefs()">Save</button>
      </div>
      <div class="form-row wrap-row">
        <div class="form-group flex-1"><label>Start Time</label><input id="prefs-hours-start" type="time" class="input input-sm" value="${esc(prefs.working_hours_start || "09:00")}" /></div>
        <div class="form-group flex-1"><label>End Time</label><input id="prefs-hours-end" type="time" class="input input-sm" value="${esc(prefs.working_hours_end || "17:00")}" /></div>
        <div class="form-group flex-1"><label>Default Duration (min)</label><input id="prefs-duration" type="number" class="input input-sm" min="15" max="480" value="${prefs.meeting_duration_minutes || 30}" /></div>
        <div class="form-group flex-1"><label>Buffer (min)</label><input id="prefs-buffer" type="number" class="input input-sm" min="0" max="60" value="${prefs.buffer_minutes || 15}" /></div>
      </div>
    </div>
    <div class="guide-section" style="margin-top:1rem">
      <div class="guide-section-header">
        <h4>\u270d\ufe0f Email Style Guide</h4>
        <button class="btn btn-ghost btn-sm" onclick="saveStyleGuide()">Save</button>
      </div>
      <div class="form-group">
        <label>Tone</label>
        <select id="style-tone" class="select-minimal">
          ${["professional", "friendly", "formal", "casual"].map(t =>
            `<option value="${t}" ${(style.tone || "professional") === t ? "selected" : ""}>${t}</option>`
          ).join("")}
        </select>
      </div>
      <div class="form-group"><label>Signature</label><textarea id="style-signature" class="input textarea" rows="3">${esc(style.signature || "")}</textarea></div>
      <div class="form-group"><label>Custom AI Instructions</label><textarea id="style-instructions" class="input textarea" rows="3" placeholder="e.g. Always include a Zoom link; avoid bullet points">${esc(style.custom_instructions || "")}</textarea></div>
    </div>`;
}

async function saveSchedulingPrefs() {
  try {
    const start    = document.getElementById("prefs-hours-start").value;
    const end      = document.getElementById("prefs-hours-end").value;
    const duration = document.getElementById("prefs-duration").value;
    const buffer   = document.getElementById("prefs-buffer").value;
    const content  = `· Working hours: ${start}–${end}\n· Default meeting duration: ${duration} minutes\n· Buffer between meetings: ${buffer} minutes`;
    await api("PUT", "/api/v1/email/guides/preferences", { content });
    showToast("Scheduling preferences saved!");
  } catch (e) { showToast("Failed: " + e.message, "error"); }
}

async function saveStyleGuide() {
  try {
    const tone         = document.getElementById("style-tone").value;
    const signature    = document.getElementById("style-signature").value;
    const instructions = document.getElementById("style-instructions").value;
    const parts = [`· Tone: ${tone}`];
    if (signature)    parts.push(`· Signature: ${signature}`);
    if (instructions) parts.push(`· Instructions: ${instructions}`);
    await api("PUT", "/api/v1/email/guides/style", { content: parts.join("\n") });
    showToast("Style guide saved!");
  } catch (e) { showToast("Failed: " + e.message, "error"); }
}

// Onboarding
async function startOnboarding() {
  const btn = document.getElementById("btn-start-onboarding");
  if (btn) { btn.disabled = true; btn.textContent = "Starting…"; }
  try {
    const res = await api("POST", "/api/v1/email/onboarding/start");
    showToast(res.message || "Onboarding started!");
    setTimeout(loadOnboardingStatus, 2000);
  } catch (e) {
    showToast("Failed: " + e.message, "error");
    if (btn) { btn.disabled = false; btn.textContent = "Start Onboarding"; }
  }
}

async function loadOnboardingStatus() {
  const container = document.getElementById("onboarding-content");
  if (!container) return;
  try {
    const status = await api("GET", "/api/v1/email/onboarding/status");
    renderOnboardingStatus(status, container);
  } catch (e) {
    container.innerHTML = renderOnboardingDefault();
  }
}

function renderOnboardingStatus(status, container) {
  const steps = status.steps || [];
  const completed = status.completed_steps || 0;
  const total = status.total_steps || steps.length || 0;
  const pct = total ? Math.round((completed / total) * 100) : 0;
  container.innerHTML = `
    <div class="onboarding-card">
      <div class="onboarding-header">
        <h4>Setup Progress</h4>
        <span class="onboarding-pct">${pct}% complete</span>
      </div>
      <div class="onboarding-progress"><div class="onboarding-bar" style="width:${pct}%"></div></div>
      ${steps.length ? `
      <div class="onboarding-steps">
        ${steps.map(s => `
          <div class="onboarding-step ${s.completed ? "done" : ""}">
            <span class="step-icon">${s.completed ? "\u2705" : "\u2b1c"}</span>
            <div class="step-info">
              <div class="step-title">${esc(s.title || s.name || String(s))}</div>
              ${s.description ? `<div class="step-desc">${esc(s.description)}</div>` : ""}
            </div>
          </div>`).join("")}
      </div>` : ""}
      ${pct < 100 ? `<button class="btn btn-primary" id="btn-start-onboarding" onclick="startOnboarding()" style="margin-top:1rem">Start Onboarding</button>` : '<p class="text-muted" style="margin-top:.75rem">\u2705 All steps complete!</p>'}
    </div>`;
}

function renderOnboardingDefault() {
  return `
    <div class="onboarding-card">
      <div class="onboarding-header"><h4>Get Started with Chronos</h4></div>
      <p class="text-muted" style="margin-bottom:1rem">Run the onboarding flow to set up scheduling preferences, email style guide, and connected accounts in one guided step.</p>
      <button class="btn btn-primary" id="btn-start-onboarding" onclick="startOnboarding()">Start Onboarding</button>
    </div>`;
}

// ── User Preferences / Autopilot ─────────────────────────────

let userPreferences = { autopilot_enabled: false };

async function loadUserPreferences() {
  try {
    userPreferences = await api("GET", "/api/v1/settings/user-preferences");
    const toggle = document.getElementById("autopilot-toggle");
    if (toggle) toggle.checked = !!userPreferences.autopilot_enabled;
    const label = document.getElementById("autopilot-label");
    if (label) label.textContent = userPreferences.autopilot_enabled ? "Autopilot ON" : "Autopilot";
  } catch (e) { /* ignore — endpoint may need auth first */ }
}

async function toggleAutopilot() {
  const toggle = document.getElementById("autopilot-toggle");
  const label = document.getElementById("autopilot-label");
  const newVal = toggle.checked;
  try {
    await api("PUT", "/api/v1/settings/user-preferences", { autopilot_enabled: newVal });
    userPreferences.autopilot_enabled = newVal;
    if (label) label.textContent = newVal ? "Autopilot ON" : "Autopilot";
    showToast(newVal ? "Autopilot enabled — 1:1 meeting drafts will be sent automatically" : "Autopilot disabled");
  } catch (e) {
    toggle.checked = !newVal;
    showToast("Failed: " + e.message, "error");
  }
}

// ── Drafts ─────────────────────────────────────────────────────

let emailDrafts = [];

async function loadDrafts() {
  try {
    emailDrafts = await api("GET", "/api/v1/email/drafts");
    const badge = document.getElementById("tab-count-drafts");
    if (badge) badge.textContent = emailDrafts.length;
    if (emailCurrentTab === "drafts") renderDrafts();
  } catch (e) {
    emailDrafts = [];
  }
}

function renderDrafts() {
  const container = document.getElementById("email-drafts-list");
  if (!emailDrafts.length) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon">\u270d\ufe0f</div>No drafts yet. Scan your inbox to compose AI-drafted meeting replies.</div>';
    return;
  }
  container.innerHTML = emailDrafts.map(d => renderDraftCard(d)).join("");
}

function renderDraftCard(d) {
  const statusLabels = {
    pending: "\u23f3 Pending Review",
    sent: "\u2705 Sent",
    autopilot_sent: "\ud83e\udd16 Auto-sent",
    discarded: "\ud83d\uddd1 Discarded",
  };
  const statusColors = {
    pending: "var(--accent)",
    sent: "var(--green)",
    autopilot_sent: "#9b59b6",
    discarded: "var(--text3)",
  };
  const status = d.status || "pending";
  const statusLabel = statusLabels[status] || status;
  const statusColor = statusColors[status] || "var(--text2)";
  const createdAt = d.created_at ? fmtRelativeTime(d.created_at) : "";
  const isPending = status === "pending";

  const proposedTimes = (d.proposed_windows || []).slice(0, 3).map(w => {
    const start = new Date(w.start || w.date || w);
    if (isNaN(start)) return "";
    return start.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) +
      " " + start.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }).filter(Boolean).join(" · ");

  return `
    <div class="draft-card" id="draft-card-${d.id}">
      <div class="draft-card-header">
        <div class="draft-meta">
          <span class="draft-status-badge" style="color:${statusColor}">${statusLabel}</span>
          ${d.is_group_meeting ? '<span class="draft-badge">\ud83d\udc65 Group</span>' : '<span class="draft-badge">1:1</span>'}
          ${d.autopilot_eligible ? '<span class="draft-badge autopilot-badge">\ud83e\udd16 Autopilot eligible</span>' : ''}
        </div>
        <span class="draft-time">${createdAt}</span>
      </div>
      <div class="draft-card-body">
        <div class="draft-subject">${esc(d.email_subject || "(no subject)")}</div>
        <div class="draft-from">To: ${esc(d.email_sender || "")}</div>
        ${proposedTimes ? `<div class="draft-times">\ud83d\udcc5 Proposed: ${proposedTimes}</div>` : ""}
        <div class="draft-preview">${esc((d.reply_body || "").slice(0, 240))}${(d.reply_body || "").length > 240 ? "\u2026" : ""}</div>
      </div>
      ${isPending ? `
      <div class="draft-actions">
        <button class="btn btn-primary btn-sm" onclick="sendDraft('${d.id}')">\ud83d\udce4 Send Now</button>
        <button class="btn btn-ghost btn-sm" onclick="deleteDraft('${d.id}')">\ud83d\uddd1 Discard</button>
      </div>` : ""}
    </div>
  `;
}

async function sendDraft(id) {
  const card = document.getElementById("draft-card-" + id);
  const btns = card ? card.querySelectorAll("button") : [];
  btns.forEach(b => b.disabled = true);
  try {
    await api("POST", `/api/v1/email/drafts/${id}/send`);
    showToast("Draft sent!");
    await loadDrafts();
  } catch (e) {
    showToast("Send failed: " + e.message, "error");
    btns.forEach(b => b.disabled = false);
  }
}

async function deleteDraft(id) {
  if (!confirm("Discard this draft?")) return;
  try {
    await api("DELETE", `/api/v1/email/drafts/${id}`);
    showToast("Draft discarded");
    await loadDrafts();
  } catch (e) {
    showToast("Failed: " + e.message, "error");
  }
}

// ── Analytics ──────────────────────────────────────────────────

async function loadAnalytics() {
  const container = document.getElementById("email-analytics");
  container.innerHTML = '<div class="empty-state">Loading analytics...</div>';
  try {
    const summary = await api("GET", "/api/v1/email/analytics/summary?days=30");
    renderAnalyticsSummary(summary, container);
  } catch (e) {
    container.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

function renderAnalyticsSummary(summary, container) {
  const events = summary.events || summary;
  const statCards = [
    { label: "Drafts Composed",  value: events.draft_composed          || 0, icon: "\u270d\ufe0f", color: "var(--accent)" },
    { label: "Drafts Sent",      value: (events.draft_sent || 0) + (events.draft_sent_autopilot || 0), icon: "\ud83d\udce4", color: "var(--green)" },
    { label: "Auto-pilot Sent",  value: events.draft_sent_autopilot    || 0, icon: "\ud83e\udd16", color: "#9b59b6" },
    { label: "Links Created",    value: events.link_created            || 0, icon: "\ud83d\udd17", color: "#3498db" },
    { label: "Links Booked",     value: events.link_booked             || 0, icon: "\ud83d\udcc5", color: "#e67e22" },
    { label: "Scans Completed",  value: events.scan_completed          || 0, icon: "\ud83d\udd0d", color: "var(--text2)" },
  ];

  const total = statCards.reduce((a, c) => a + c.value, 0);

  if (!total) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon">\ud83d\udcca</div>No analytics data yet. Scan emails to start tracking activity.</div>';
    return;
  }

  container.innerHTML = `
    <div class="analytics-header">
      <h3>Last 30 Days</h3>
      <span class="analytics-total">${total} total events</span>
    </div>
    <div class="analytics-stats-grid">
      ${statCards.map(c => `
        <div class="analytics-stat-card">
          <div class="analytics-stat-icon" style="color:${c.color}">${c.icon}</div>
          <div class="analytics-stat-value">${c.value}</div>
          <div class="analytics-stat-label">${c.label}</div>
        </div>
      `).join("")}
    </div>
    ${summary.period_days ? `<div class="analytics-footer">Period: last ${summary.period_days} days</div>` : ""}
  `;
}

// ── API helper ─────────────────────────────────────────────────

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (token) opts.headers["Authorization"] = "Bearer " + token;
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  if (res.status === 401) { logout(); throw new Error("Session expired"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// ── Util ───────────────────────────────────────────────────────

function esc(s) { const d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }
function fmtTime(iso) { return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }); }

function fmtRelativeTime(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now - d;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

// ── Toast ──────────────────────────────────────────────────────

let toastTimer = null;
function showToast(msg, type = "success") {
  const toast = document.getElementById("settings-toast");
  toast.textContent = msg;
  toast.style.borderColor = type === "error" ? "var(--red)" : "var(--green)";
  toast.style.color = type === "error" ? "var(--red)" : "var(--green)";
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3000);
}

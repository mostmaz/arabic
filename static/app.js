/* OpenSooq Scraper — frontend */

"use strict";

let currentPage  = 1;
let currentQuery = "";
let pollTimer    = null;
let searchTimer  = null;
let listingsMap  = {};   // listing_id / index → listing object

// ── Bootstrap ────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  loadListings();
  startPolling();
});

// ── Polling ──────────────────────────────────────────────────────────────────

function startPolling() {
  pollStatus();
  pollTimer = setInterval(pollStatus, 2000);
}

async function pollStatus() {
  try {
    const s = await apiFetch("/api/status");
    renderStatus(s);

    // Refresh listings when a scrape just finished
    if (s.status === "done" || s.status === "stopped") {
      loadListings();
    }

    // Update login badge
    const badge = document.getElementById("loginBadge");
    if (s.logged_in) {
      badge.className = "login-badge logged-in";
    } else {
      badge.className = "login-badge guest";
    }
  } catch (_) {}
}

function renderStatus(s) {
  const dot   = document.getElementById("statusDot");
  const text  = document.getElementById("statusText");
  const fill  = document.getElementById("progressFill");
  const label = document.getElementById("progressLabel");
  const scrapeBtn = document.getElementById("scrapeBtn");
  const stopBtn   = document.getElementById("stopBtn");

  const status  = s.status || "idle";
  const running = s.is_running;

  dot.className = `status-dot ${status}`;
  text.textContent = s.message || capitalize(status);

  const pct = (s.total > 0) ? Math.round(s.current / s.total * 100) : 0;
  fill.style.width  = pct + "%";
  label.textContent = s.total > 0 ? `${s.current} / ${s.total}  (${pct}%)` : "";

  scrapeBtn.disabled = running;
  stopBtn.classList.toggle("hidden", !running);
  scrapeBtn.classList.toggle("hidden", running);
}

// ── Scraping ─────────────────────────────────────────────────────────────────

async function startScrape() {
  const url = document.getElementById("urlInput").value.trim();
  if (!url) { alert("Please enter a URL."); return; }

  const noImages = document.getElementById("noImages").checked;

  const res = await apiFetch("/api/scrape", "POST", { url, no_images: noImages });
  if (res.error) { alert("Error: " + res.error); return; }

  renderStatus({ status: "scraping", is_running: true, message: "Starting…", current: 0, total: 0 });
}

async function stopScrape() {
  await apiFetch("/api/stop", "POST");
}

// ── Listings ─────────────────────────────────────────────────────────────────

async function loadListings(page = 1) {
  currentPage = page;
  const q = encodeURIComponent(currentQuery);
  const data = await apiFetch(`/api/listings?page=${page}&per_page=24&q=${q}`);

  const grid     = document.getElementById("grid");
  const empty    = document.getElementById("emptyState");
  const countEl  = document.getElementById("totalCount");
  const pagEl    = document.getElementById("pagination");

  countEl.textContent = `${data.total} listing${data.total !== 1 ? "s" : ""}`;

  if (!data.items || data.items.length === 0) {
    grid.innerHTML = "";
    grid.appendChild(empty);
    empty.classList.remove("hidden");
    pagEl.innerHTML = "";
    return;
  }

  empty.classList.add("hidden");

  // Store listings in a map so openDetail can look them up without JSON escaping
  listingsMap = {};
  data.items.forEach((l, i) => {
    const key = l.listing_id || String(i);
    l._key = key;
    listingsMap[key] = l;
  });

  grid.innerHTML = data.items.map(cardHTML).join("");

  // Pagination
  renderPagination(data.page, data.pages, pagEl);
}

function cardHTML(l) {
  const img = l.thumbnail
    ? `<img class="card-img" src="${esc(l.thumbnail)}" loading="lazy" onerror="this.outerHTML='<div class=card-img-placeholder>📷</div>'">`
    : `<div class="card-img-placeholder">📷</div>`;

  const phone = l.phone
    ? `<div class="card-phone">📞 ${esc(l.phone)}</div>`
    : "";

  return `
  <div class="card" onclick="openDetail('${esc(l._key)}')">
    ${img}
    <div class="card-body">
      <div class="card-title">${esc(l.title || "—")}</div>
      <div class="card-price">${esc(l.price || "—")}</div>
      ${phone}
      <div class="card-meta">
        ${l.location ? `<div class="card-meta-row">📍 ${esc(l.location)}</div>` : ""}
        ${l.date_posted ? `<div class="card-meta-row">🕐 ${esc(l.date_posted)}</div>` : ""}
        ${l.condition ? `<div class="card-meta-row">✅ ${esc(l.condition)}</div>` : ""}
      </div>
    </div>
  </div>`;
}

function renderPagination(current, total, el) {
  if (total <= 1) { el.innerHTML = ""; return; }

  let html = "";
  const prev = current - 1;
  const next = current + 1;

  html += `<button class="page-btn" ${current===1?"disabled":""} onclick="loadListings(${prev})">‹</button>`;

  for (let p = 1; p <= total; p++) {
    if (p === 1 || p === total || Math.abs(p - current) <= 2) {
      html += `<button class="page-btn ${p===current?"active":""}" onclick="loadListings(${p})">${p}</button>`;
    } else if (Math.abs(p - current) === 3) {
      html += `<span class="page-btn" style="cursor:default">…</span>`;
    }
  }

  html += `<button class="page-btn" ${current===total?"disabled":""} onclick="loadListings(${next})">›</button>`;
  el.innerHTML = html;
}

// ── Search ────────────────────────────────────────────────────────────────────

function onSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    currentQuery = document.getElementById("searchInput").value.trim();
    loadListings(1);
  }, 350);
}

// ── Detail modal ──────────────────────────────────────────────────────────────

function openDetail(key) {
  const l = listingsMap[key];
  if (!l) return;

  const images = Array.isArray(l.images) ? l.images : [];
  const locals = Array.isArray(l.local_images) ? l.local_images : [];

  // Build image strip: prefer local paths served via /img/
  let imgSrcs;
  if (locals.length) {
    imgSrcs = locals.map(p => {
      let rel = p.replace(/\\/g, "/");
      if (rel.startsWith("images/")) rel = rel.slice(7);
      return `/img/${rel}`;
    });
  } else if (l.thumbnail) {
    imgSrcs = [l.thumbnail];
  } else {
    imgSrcs = images.slice(0, 8);
  }

  const imgHtml = imgSrcs.length
    ? imgSrcs.map(s => `<img src="${esc(s)}" onerror="this.style.display='none'">`).join("")
    : "";

  const phoneHtml = l.phone
    ? `<div class="detail-phone-box">📞 <span>${esc(l.phone)}</span></div>`
    : "";

  document.getElementById("detailContent").innerHTML = `
    ${imgSrcs.length ? `<div class="detail-images">${imgHtml}</div>` : ""}
    <div class="detail-title">${esc(l.title || "—")}</div>
    <div class="detail-price">${esc(l.price || "—")}</div>
    ${phoneHtml}
    <div class="detail-grid">
      ${field("Location", l.location)}
      ${field("Date",     l.date_posted)}
      ${field("Condition", l.condition)}
      ${field("Seller",   l.seller_name)}
      ${field("ID",       l.listing_id)}
    </div>
    ${l.description ? `<div class="detail-desc">${esc(l.description)}</div>` : ""}
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <a class="detail-link" href="${esc(l.url)}" target="_blank" rel="noopener">
        Open on OpenSooq ↗
      </a>
    </div>
  `;

  document.getElementById("detailOverlay").classList.remove("hidden");
  document.getElementById("detailModal").classList.remove("hidden");
}

function field(label, value) {
  if (!value) return "";
  return `<div class="detail-field">
    <div class="detail-field-label">${label}</div>
    <div class="detail-field-value">${esc(value)}</div>
  </div>`;
}

function closeDetail() {
  document.getElementById("detailOverlay").classList.add("hidden");
  document.getElementById("detailModal").classList.add("hidden");
}

// ── Login modal ───────────────────────────────────────────────────────────────

function switchTab(tab) {
  document.getElementById("panelCookies").classList.toggle("hidden", tab !== "cookies");
  document.getElementById("panelLogin").classList.toggle("hidden",   tab !== "login");
  document.getElementById("tabCookies").classList.toggle("active",   tab === "cookies");
  document.getElementById("tabLogin").classList.toggle("active",     tab === "login");
  document.getElementById("loginError").classList.add("hidden");
}

async function submitCookies() {
  const raw     = document.getElementById("cookieJson").value.trim();
  const errEl   = document.getElementById("loginError");
  const spinner = document.getElementById("loginSpinner");

  if (!raw) {
    errEl.textContent = "Please paste your cookie JSON first.";
    errEl.classList.remove("hidden");
    return;
  }

  let parsed;
  try { parsed = JSON.parse(raw); }
  catch (_) {
    errEl.textContent = "Invalid JSON — make sure you copied the full export from Cookie-Editor.";
    errEl.classList.remove("hidden");
    return;
  }

  errEl.classList.add("hidden");
  spinner.classList.remove("hidden");

  const res = await apiFetch("/api/cookies", "POST", parsed);
  spinner.classList.add("hidden");

  if (res.error) {
    errEl.textContent = res.error;
    errEl.classList.remove("hidden");
  } else {
    document.getElementById("cookieJson").value = "";
    closeLoginModal();
    pollStatus();
  }
}

function openLoginModal() {
  document.getElementById("loginOverlay").classList.remove("hidden");
  document.getElementById("loginModal").classList.remove("hidden");
  document.getElementById("loginError").classList.add("hidden");
  document.getElementById("loginSpinner").classList.add("hidden");

  // Check login state to show/hide logout button
  apiFetch("/api/login/status").then(s => {
    document.getElementById("logoutBtn").classList.toggle("hidden", !s.logged_in);
    // Only disable the phone/password fields when already logged in
    // Never disable the cookie textarea — user may want to re-import
    if (document.getElementById("loginPhone")) {
      document.getElementById("loginPhone").disabled    = s.logged_in;
      document.getElementById("loginPassword").disabled = s.logged_in;
    }
  });
}

function closeLoginModal() {
  document.getElementById("loginOverlay").classList.add("hidden");
  document.getElementById("loginModal").classList.add("hidden");
}

async function submitLogin() {
  const phone    = document.getElementById("loginPhone").value.trim();
  const password = document.getElementById("loginPassword").value;
  const errEl    = document.getElementById("loginError");
  const spinner  = document.getElementById("loginSpinner");

  if (!phone || !password) {
    errEl.textContent = "Mobile number and password are required.";
    errEl.classList.remove("hidden");
    return;
  }

  errEl.classList.add("hidden");
  spinner.classList.remove("hidden");

  const res = await apiFetch("/api/login", "POST", { phone, password });
  spinner.classList.add("hidden");

  if (res.error) {
    errEl.textContent = res.error;
    errEl.classList.remove("hidden");
  } else {
    closeLoginModal();
    pollStatus();
  }
}

async function doLogout() {
  await apiFetch("/api/login", "DELETE");
  closeLoginModal();
  pollStatus();
}

async function clearPhones() {
  if (!confirm("This will clear all stored phone numbers so they get re-scraped. Continue?")) return;
  const res = await apiFetch("/api/listings/clear-phones", "POST");
  alert(`Cleared phone numbers for ${res.count ?? 0} listings.`);
  loadListings();
}

async function clearAll() {
  if (!confirm("Delete ALL scraped listings and downloaded images? This cannot be undone.")) return;
  await apiFetch("/api/listings/clear-all", "POST");
  listingsMap = {};
  loadListings(1);
}

// ── Utils ─────────────────────────────────────────────────────────────────────

async function apiFetch(path, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  try {
    const r = await fetch(path, opts);
    return await r.json();
  } catch (e) {
    return { error: String(e) };
  }
}

function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function capitalize(s) {
  return s ? s[0].toUpperCase() + s.slice(1) : "";
}

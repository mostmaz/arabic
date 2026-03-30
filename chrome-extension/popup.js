// popup.js

const SERVER = "http://localhost:5000";

let pageType = "other";
let categoryUrls = [];

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await detectPage();
  syncQueueStatus();

  document.getElementById("scrapeOneBtn").addEventListener("click", scrapeOne);
  document.getElementById("scrapeAllBtn").addEventListener("click", scrapeAll);
  document.getElementById("stopBtn").addEventListener("click", stopAll);
});

// ── Page detection ────────────────────────────────────────────────────────────

async function detectPage() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url.includes("opensooq.com")) {
    setOther("Not an OpenSooq page.");
    return;
  }

  let response;
  try {
    response = await chrome.tabs.sendMessage(tab.id, { action: "detect" });
  } catch (_) {
    // Content script not injected yet — inject manually
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    try {
      response = await chrome.tabs.sendMessage(tab.id, { action: "detect" });
    } catch (_) {
      setOther("Could not read page. Try reloading the tab.");
      return;
    }
  }

  if (!response) {
    setOther("No response from page.");
    return;
  }

  if (response.type === "listing") {
    pageType = "listing";
    const d = response.data;
    showSection("listingControls");
    setBadge("listing", "📄 Listing Page");
    document.getElementById("infoText").innerHTML =
      `<strong>${escHtml(d.title || "Untitled")}</strong><br>` +
      (d.price ? `Price: ${escHtml(d.price)}<br>` : "") +
      (d.phone ? `📞 ${escHtml(d.phone)}` : "No phone visible");
  } else if (response.type === "category") {
    pageType = "category";
    categoryUrls = response.urls;
    showSection("categoryControls");
    setBadge("category", "📂 Category Page");
    document.getElementById("infoText").innerHTML =
      `Found <strong>${categoryUrls.length}</strong> listings on this page.`;
  } else {
    setOther("Navigate to a listing or category page.");
  }
}

// ── Scrape single listing ─────────────────────────────────────────────────────

async function scrapeOne() {
  const btn = document.getElementById("scrapeOneBtn");
  btn.disabled = true;
  btn.textContent = "⏳ Scraping...";

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  let data;
  try {
    const res = await chrome.tabs.sendMessage(tab.id, { action: "extract" });
    data = res?.data;
  } catch (_) {}

  if (!data) {
    btn.textContent = "✗ Failed — reload page and try again";
    btn.disabled = false;
    return;
  }

  try {
    const res = await fetch(`${SERVER}/api/listings/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const json = await res.json();
    if (json.status === "ok") {
      btn.textContent = "✓ Saved!";
      setTimeout(() => { btn.textContent = "⬇ Scrape This Listing"; btn.disabled = false; }, 2000);
    } else {
      btn.textContent = "✗ Server error";
      btn.disabled = false;
    }
  } catch (_) {
    btn.textContent = "✗ Cannot reach localhost:5000";
    btn.disabled = false;
  }
}

// ── Scrape category ───────────────────────────────────────────────────────────

async function scrapeAll() {
  if (!categoryUrls.length) return;
  document.getElementById("scrapeAllBtn").classList.add("hidden");
  document.getElementById("stopBtn").classList.remove("hidden");
  document.getElementById("progressWrap").classList.remove("hidden");
  document.getElementById("statsRow").classList.remove("hidden");

  await chrome.runtime.sendMessage({ action: "start_queue", urls: categoryUrls });
  pollQueueStatus();
}

async function stopAll() {
  await chrome.runtime.sendMessage({ action: "stop_queue" });
  document.getElementById("stopBtn").classList.add("hidden");
  document.getElementById("scrapeAllBtn").classList.remove("hidden");
  document.getElementById("statusText").textContent = "Stopped.";
}

// ── Queue status polling ──────────────────────────────────────────────────────

function pollQueueStatus() {
  const timer = setInterval(async () => {
    const s = await chrome.runtime.sendMessage({ action: "get_status" });
    if (!s) return;
    updateStats(s);
    if (!s.isRunning) clearInterval(timer);
  }, 800);
}

function syncQueueStatus() {
  chrome.runtime.sendMessage({ action: "get_status" }, s => {
    if (!s || !s.isRunning) return;
    // A scrape is already running from a previous popup open
    pageType = "category";
    showSection("categoryControls");
    document.getElementById("scrapeAllBtn").classList.add("hidden");
    document.getElementById("stopBtn").classList.remove("hidden");
    document.getElementById("progressWrap").classList.remove("hidden");
    document.getElementById("statsRow").classList.remove("hidden");
    updateStats(s);
    pollQueueStatus();
  });
}

function updateStats(s) {
  const total = s.stats.total || 1;
  const done  = s.stats.done + s.stats.errors;
  const pct   = Math.round((done / total) * 100);

  document.getElementById("progressBar").style.width = pct + "%";
  document.getElementById("statDone").textContent    = s.stats.done;
  document.getElementById("statSkipped").textContent = s.stats.skipped;
  document.getElementById("statErrors").textContent  = s.stats.errors;
  document.getElementById("statusText").textContent  =
    s.isRunning
      ? `Processing… ${done}/${total} (${s.remaining} remaining)`
      : `Done — ${s.stats.done} scraped, ${s.stats.skipped} skipped, ${s.stats.errors} errors`;

  if (!s.isRunning) {
    document.getElementById("stopBtn").classList.add("hidden");
    document.getElementById("scrapeAllBtn").classList.remove("hidden");
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function showSection(id) {
  ["listingControls", "categoryControls", "otherMsg"].forEach(s => {
    document.getElementById(s).classList.toggle("hidden", s !== id);
  });
}

function setBadge(type, label) {
  const el = document.getElementById("pageTypeBadge");
  el.className = `page-type ${type}`;
  el.textContent = label;
}

function setOther(msg) {
  setBadge("other", "❔ Unknown");
  document.getElementById("infoText").textContent = "";
  document.getElementById("otherMsg").classList.remove("hidden");
  document.getElementById("otherMsg").querySelector(".info-row").textContent = msg;
  ["listingControls", "categoryControls"].forEach(id =>
    document.getElementById(id).classList.add("hidden"));
}

function escHtml(str) {
  return String(str).replace(/[&<>"']/g, c =>
    ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

// Listen for live updates from background
chrome.runtime.onMessage.addListener(msg => {
  if (msg.type === "queue_status") updateStats(msg);
});

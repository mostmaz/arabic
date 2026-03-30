// background.js — service worker
// Manages the category scrape queue: opens each listing URL in a tab,
// waits for content.js to extract data, sends to Flask, then closes tab.

const SERVER = "http://localhost:5000";

let queue = [];
let isRunning = false;
let currentTabId = null;
let stats = { done: 0, skipped: 0, errors: 0, total: 0 };

// ── Queue management ──────────────────────────────────────────────────────────

async function startQueue(urls) {
  // Ask Flask which URLs are already scraped
  let scraped = new Set();
  try {
    const res = await fetch(`${SERVER}/api/listings/urls`);
    const data = await res.json();
    scraped = new Set(data.urls || []);
  } catch (_) {}

  queue = urls.filter(u => !scraped.has(u));
  const skipped = urls.length - queue.length;

  stats = { done: 0, skipped, errors: 0, total: queue.length };
  isRunning = true;

  broadcastStatus();
  processNext();
}

async function processNext() {
  if (!isRunning || queue.length === 0) {
    isRunning = false;
    broadcastStatus("done");
    return;
  }

  const url = queue.shift();
  broadcastStatus("opening", url);

  try {
    const tab = await chrome.tabs.create({ url, active: false });
    currentTabId = tab.id;

    // Wait for page to load then extract
    await waitForTab(tab.id);
    await sleep(2500); // let JS render

    let data = null;
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content.js"],
      });
      data = result?.result;
    } catch (_) {}

    if (!data) {
      // Fallback: send message to content script
      try {
        const response = await chrome.tabs.sendMessage(tab.id, { action: "extract" });
        data = response?.data;
      } catch (_) {}
    }

    if (data) {
      await sendToFlask(data);
      stats.done++;
    } else {
      stats.errors++;
    }

    await chrome.tabs.remove(tab.id).catch(() => {});
    currentTabId = null;
  } catch (e) {
    stats.errors++;
    if (currentTabId) {
      await chrome.tabs.remove(currentTabId).catch(() => {});
      currentTabId = null;
    }
  }

  broadcastStatus();

  // Delay between listings (human-like)
  await sleep(1500 + Math.random() * 2000);
  processNext();
}

function waitForTab(tabId) {
  return new Promise(resolve => {
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(resolve, 15000); // fallback timeout
  });
}

async function sendToFlask(data) {
  await fetch(`${SERVER}/api/listings/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

function broadcastStatus(status, currentUrl) {
  chrome.runtime.sendMessage({
    type: "queue_status",
    isRunning,
    stats,
    currentUrl: currentUrl || "",
    remaining: queue.length,
  }).catch(() => {});
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ── Message handler ───────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "start_queue") {
    startQueue(msg.urls);
    sendResponse({ ok: true });
  } else if (msg.action === "stop_queue") {
    isRunning = false;
    queue = [];
    if (currentTabId) {
      chrome.tabs.remove(currentTabId).catch(() => {});
      currentTabId = null;
    }
    broadcastStatus("stopped");
    sendResponse({ ok: true });
  } else if (msg.action === "get_status") {
    sendResponse({ isRunning, stats, remaining: queue.length });
  }
  return true;
});

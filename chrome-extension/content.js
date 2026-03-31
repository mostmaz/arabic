// content.js — runs on all opensooq.com pages

function isListingPage() {
  return /\/\d{6,}/.test(location.pathname) ||
         /\/(search|ar)\/.+-\d{5,}/.test(location.pathname);
}

function getText(...selectors) {
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) {
      const t = el.innerText || el.textContent || "";
      if (t.trim()) return t.trim();
    }
  }
  return "";
}

function getTelNumber() {
  for (const a of document.querySelectorAll("a[href^='tel:']")) {
    const num = a.getAttribute("href").replace("tel:", "").replace(/\s/g, "").trim();
    if (num.length >= 8) return num;
  }
  return "";
}

async function revealPhone() {
  const existing = getTelNumber();
  if (existing) return existing;

  // Find the phone button — most specific first, never click <a> tags (they navigate)
  let btn = null;

  // 1. OpenSooq uses id="primary" on the phone button
  const primaryBtn = document.querySelector("button#primary");
  if (primaryBtn) {
    const t = (primaryBtn.innerText || primaryBtn.textContent || "").replace(/\s/g, "");
    if (/\d{5,}/.test(t) || /07\d/.test(t)) btn = primaryBtn;
  }

  // 2. button with known class + bg-primary (phone button only, not chat)
  if (!btn) {
    const candidate = document.querySelector("button.bg-primary, button[class*='bg-primary']");
    if (candidate) {
      const t = (candidate.innerText || candidate.textContent || "").replace(/\s/g, "");
      if (/\d{5,}/.test(t) || /07\d/.test(t)) btn = candidate;
    }
  }

  // 3. Any BUTTON (not <a>) whose text contains a masked phone number
  if (!btn) {
    for (const el of document.querySelectorAll("button")) {
      const t = (el.innerText || el.textContent || "").replace(/\s/g, "");
      if (/07[\dX]{8,}/.test(t)) { btn = el; break; }
    }
  }

  if (!btn) return "";

  btn.click();

  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 100));
    const num = getTelNumber();
    if (num) return num;
  }

  const text = (btn.innerText || btn.textContent || "").replace(/\s/g, "");
  const m = text.match(/07\d{8,9}/);
  return m ? m[0] : "";
}

async function extractListing() {
  const idMatch = location.pathname.match(/\/(\d{6,})/);
  const listing_id = idMatch ? idMatch[1] : "";

  const phone = await revealPhone();

  // ── Images ────────────────────────────────────────────────────────────────────
  const images = [];
  const seenHash = new Set();

  function addImg(src) {
    if (!src || typeof src !== "string") return;
    if (!src.includes("opensooq-images.os-cdn.com/previews/")) return;
    if (src.includes(".mp4") || src.includes("avatar") || src.includes("placeholder")) return;
    // Normalize to full-res 2000x0
    const normalized = src.replace(/\/previews\/[^/]+\//, "/previews/2000x0/");
    // Dedup by filename (strip size prefix); treat .webp and .jpg.webp as same image
    const hashMatch = normalized.match(/\/previews\/[^/]+\/(.+)/);
    const hash = (hashMatch ? hashMatch[1] : normalized).replace(/^(.+?)\.jpg(\.webp)$/, "$1$2");
    if (seenHash.has(hash)) return;
    seenHash.add(hash);
    images.push(normalized);
  }

  // Strategy 1: parse __NEXT_DATA__ as JSON and recursively walk every string value
  // (avoids regex truncation on JSON-escaped slashes like \/previews\/0x240\/filename)
  try {
    const nextDataEl = document.getElementById("__NEXT_DATA__");
    if (nextDataEl) {
      const walk = v => {
        if (typeof v === "string") { addImg(v); }
        else if (Array.isArray(v)) { v.forEach(walk); }
        else if (v && typeof v === "object") { Object.values(v).forEach(walk); }
      };
      walk(JSON.parse(nextDataEl.textContent));
    }
  } catch (_) {}

  // Strategy 2: data-src on lazy-loaded gallery images not yet visible in DOM
  document.querySelectorAll("img[data-src*='os-cdn.com']").forEach(img => {
    addImg(img.getAttribute("data-src"));
  });

  // Strategy 3: loaded img elements (catches anything not in __NEXT_DATA__)
  document.querySelectorAll("img[src*='os-cdn.com/previews/']").forEach(img => {
    addImg(img.src);
  });

  // Strategy 4: srcset (2000w preferred, otherwise last/largest entry)
  document.querySelectorAll("img[srcset*='os-cdn.com']").forEach(img => {
    const parts = img.srcset.split(",").map(s => s.trim());
    const best = parts.find(s => s.includes("2000w")) || parts[parts.length - 1];
    if (best) addImg(best.split(" ")[0].trim());
  });

  console.log("[OpenSooq Scraper] images found:", images.length, images);

  // ── Price ─────────────────────────────────────────────────────────────────────
  let price = "";
  for (const el of document.querySelectorAll("span, div, h2, h3, strong, b")) {
    const t = (el.innerText || el.textContent || "").trim();
    if (/^\d[\d,.\s]{1,12}(IQD|دينار|USD|\$)?$/.test(t) && t.replace(/\D/g, "").length >= 3) {
      price = t;
      break;
    }
  }
  if (!price) price = getText("[class*='price']", "[class*='Price']", "[data-testid*='price']");

  return {
    listing_id,
    url: location.href,
    title: getText("h1", "[class*='post-title']", "[class*='PostTitle']", "[class*='listing-title']"),
    price,
    description: getText("[class*='description']", "[class*='Description']", "[class*='post-body']", ".desc"),
    location: getText("[class*='location']", "[class*='Location']", "[class*='breadcrumb']", "[class*='area']"),
    date_posted: getText("[class*='date']", "[class*='Date']", "[class*='time']", "time"),
    condition: getText("[class*='condition']", "[class*='Condition']"),
    seller_name: getText("[class*='seller']", "[class*='Seller']", "[class*='owner']", "[class*='user-name']"),
    phone,
    images,
    local_images: [],
  };
}

function extractCategoryUrls() {
  const urls = [];
  const seen = new Set();
  document.querySelectorAll("a[href]").forEach(a => {
    const href = a.href;
    if (
      (/\/ar\/[^/]+\/[^/]+-\d{5,}/.test(href) ||
       /\/(search|post)\/\d{6,}/.test(href) ||
       /\/\d{7,}(?:[/?#]|$)/.test(href)) &&
      !seen.has(href) &&
      !href.includes("?page=") &&
      href.includes("opensooq.com")
    ) {
      seen.add(href);
      urls.push(href);
    }
  });
  return urls;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "detect") {
    if (isListingPage()) {
      extractListing().then(data => sendResponse({ type: "listing", data }));
    } else {
      sendResponse({ type: "category", urls: extractCategoryUrls() });
    }
  } else if (msg.action === "extract") {
    extractListing().then(data => sendResponse({ data }));
  }
  return true;
});

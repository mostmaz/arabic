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
  // Try tel: href links first
  for (const a of document.querySelectorAll("a[href^='tel:']")) {
    const num = a.getAttribute("href").replace("tel:", "").replace(/\s/g, "").trim();
    if (num.length >= 8) return num;
  }
  return "";
}

async function revealPhone() {
  // If already visible, return immediately
  const existing = getTelNumber();
  if (existing) return existing;

  // Try every clickable element — find the one that looks like a phone button
  // OpenSooq shows a partial number (e.g. "077166663XX") on the button itself
  const allClickable = document.querySelectorAll("a, button, [role='button'], [onclick]");
  let btn = null;

  for (const el of allClickable) {
    const cls = (el.className || "").toLowerCase();
    const text = (el.innerText || el.textContent || "").trim();
    const href = (el.getAttribute("href") || "").toLowerCase();
    const dataType = (el.getAttribute("data-type") || "").toLowerCase();
    const dataAction = (el.getAttribute("data-action") || "").toLowerCase();

    // Exact OpenSooq phone button class
    if (el.className.includes("button_button__FPuHG") && el.className.includes("bg-primary")) {
      btn = el;
      break;
    }

    // Match by class / attribute
    if (
      cls.includes("phone") || cls.includes("call") ||
      cls.includes("contact") || cls.includes("showphone") ||
      dataType.includes("phone") || dataAction.includes("phone") ||
      href.includes("phone")
    ) {
      btn = el;
      break;
    }

    // Match by text: contains partial phone pattern like "07716XXXXX" or "07X"
    if (/07\d[\dX]{4,}/.test(text) || /\+964/.test(text)) {
      btn = el;
      break;
    }
  }

  if (!btn) return "";

  btn.click();

  // Wait up to 6s for a full tel: link to appear
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 100));
    const num = getTelNumber();
    if (num) return num;
  }

  // Last resort: read the button text after click for a full number
  const text = (btn.innerText || btn.textContent || "").replace(/\s/g, "");
  const m = text.match(/07\d{8,9}/);
  return m ? m[0] : "";
}

async function extractListing() {
  // Listing ID from URL
  const idMatch = location.pathname.match(/\/(\d{6,})/);
  const listing_id = idMatch ? idMatch[1] : "";

  // Reveal and get phone number
  const phone = await revealPhone();

  // ── Images: only from the main listing gallery ─────────────────────────────
  const images = [];
  const seen = new Set();

  // The hero image has fetchpriority="high" — use it to locate the gallery container
  const heroImg = document.querySelector("img[fetchpriority='high'][src*='os-cdn.com']")
                || document.querySelector("img[fetchpriority='high']");

  let galleryEl = null;
  if (heroImg) {
    // Walk up until we find a container holding 2+ os-cdn images (the gallery)
    let el = heroImg.parentElement;
    while (el && el !== document.body) {
      const count = el.querySelectorAll("img[src*='os-cdn.com']").length;
      if (count >= 2) { galleryEl = el; break; }
      el = el.parentElement;
    }
  }

  // Scope to gallery, or fall back to only the first half of the DOM
  const scope = galleryEl || document.body;
  scope.querySelectorAll("img[src*='os-cdn.com']").forEach(img => {
    const src = img.src || "";
    if (src && !seen.has(src) && !src.includes("avatar") && !src.includes("placeholder")) {
      seen.add(src);
      images.push(src);
  // ── Price ───────────────────────────────────────────────────────────────────
  // Find the first element whose text looks like a price number (e.g. "1,250,000")
  let price = "";
  const allEls = document.querySelectorAll("span, div, h1, h2, h3, p, strong, b");
  for (const el of allEls) {
    const t = (el.innerText || el.textContent || "").trim();
    // Price pattern: digits with commas/dots, optionally followed by currency
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
      (
        /\/ar\/[^/]+\/[^/]+-\d{5,}/.test(href) ||
        /\/(search|post)\/\d{6,}/.test(href) ||
        /\/\d{7,}(?:[/?#]|$)/.test(href)
      ) &&
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

// Listen for messages from popup / background
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "detect") {
    if (isListingPage()) {
      extractListing().then(data => sendResponse({ type: "listing", data }));
    } else {
      const urls = extractCategoryUrls();
      sendResponse({ type: "category", urls });
    }
  } else if (msg.action === "extract") {
    extractListing().then(data => sendResponse({ data }));
  }
  return true; // keep channel open for async response
});

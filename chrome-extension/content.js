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

  // Images — grab from gallery/slider elements
  const images = [];
  const seen = new Set();
  const imgCandidates = document.querySelectorAll(
    ".slick-slide img, [class*='gallery'] img, [class*='slider'] img, " +
    "[class*='swiper'] img, [class*='carousel'] img, " +
    "img[data-src], img[data-original], img[src]"
  );
  imgCandidates.forEach(img => {
    const src = img.dataset.src || img.dataset.original || img.src || "";
    if (
      src && src.startsWith("http") && !seen.has(src) &&
      !src.includes("placeholder") && !src.includes("avatar") &&
      !src.includes("000.svg") && !src.includes("logo") &&
      !src.includes("icon") && src.match(/\.(jpg|jpeg|png|webp)/i)
    ) {
      seen.add(src);
      images.push(src);
    }
  });

  return {
    listing_id,
    url: location.href,
    title: getText("h1", "[class*='post-title']", "[class*='PostTitle']", "[class*='listing-title']"),
    price: getText("[class*='price']", "[class*='Price']", "[data-testid*='price']"),
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

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
      btn = el; break;
    }
    if (cls.includes("phone") || cls.includes("call") || cls.includes("contact") ||
        cls.includes("showphone") || dataType.includes("phone") ||
        dataAction.includes("phone") || href.includes("phone")) {
      btn = el; break;
    }
    if (/07\d[\dX]{4,}/.test(text) || /\+964/.test(text)) {
      btn = el; break;
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

  // ── Images ───────────────────────────────────────────────────────────────────
  const images = [];
  const seenHash = new Set();

  function addImg(src) {
    if (!src || src.includes(".mp4.") || src.includes("avatar") || src.includes("placeholder")) return;
    const hashMatch = src.match(/previews\/[^/]+\/(.+)/);
    const hash = hashMatch ? hashMatch[1] : src;
    if (seenHash.has(hash)) return;
    seenHash.add(hash);
    images.push(src);
  }

  // Strategy 1: gallery section thumbnail strip (0x240 → upscale to 2000x0)
  const gallerySection =
    document.getElementById("listingViewGalleryModalDesktop") ||
    document.getElementById("listingViewGallery") ||
    document.querySelector("[id*='GalleryModal']") ||
    document.querySelector("[id*='gallery']");

  if (gallerySection) {
    gallerySection.querySelectorAll("img[src*='os-cdn.com'][src*='0x240']").forEach(img => {
      addImg(img.src.replace("/0x240/", "/2000x0/"));
    });
  }

  // Strategy 2: srcset — find all images with os-cdn srcsets (main slider)
  if (images.length === 0) {
    document.querySelectorAll("img[srcset*='os-cdn.com']").forEach(img => {
      const parts = img.srcset.split(",").map(s => s.trim());
      const best = parts.find(s => s.includes("2000w")) || parts[parts.length - 1];
      if (best) addImg(best.split(" ")[0].trim());
    });
  }

  // Strategy 3: any os-cdn image that isn't a thumbnail or avatar
  if (images.length === 0) {
    document.querySelectorAll("img[src*='os-cdn.com']").forEach(img => {
      const src = img.src || "";
      if (!src.includes("0x240") && !src.includes("0x84")) addImg(src);
    });
  }

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

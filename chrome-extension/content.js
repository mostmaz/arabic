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
    const normalized = src.replace(/\/previews\/[^/]+\//, "/previews/2000x0/");
    const hashMatch = normalized.match(/\/previews\/[^/]+\/(.+)/);
    // Treat abc.webp and abc.jpg.webp as the same image for dedup
    const hash = (hashMatch ? hashMatch[1] : normalized).replace(/\.jpg(\.webp)$/, "$1");
    if (seenHash.has(hash)) return;
    seenHash.add(hash);
    images.push(normalized);
  }

  // Open gallery: simulate a real click with full pointer-event sequence.
  // element.click() sets isTrusted=false; a full MouseEvent sequence is closer
  // to what the browser fires for a genuine user click and works with React.
  const galleryTrigger = document.querySelector(
    "img[src*='os-cdn.com/previews/']:not([src*='avatar']):not([src*='placeholder'])"
  );
  if (galleryTrigger) {
    const anchor = galleryTrigger.closest("a");
    const savedHref = anchor?.getAttribute("href") ?? null;
    if (anchor) anchor.removeAttribute("href");

    const router = window.__NEXT_ROUTER_INSTANCE__ || window.next?.router || null;
    const origPush    = router?.push?.bind(router);
    const origReplace = router?.replace?.bind(router);
    if (router && origPush) {
      const guard = (url, ...args) => {
        const target = typeof url === "string" ? url : (url?.pathname ?? "");
        const samePage = target.startsWith("?") || target.startsWith("#") ||
                         target.includes(listing_id) || target === location.pathname;
        return samePage ? origPush.call(router, url, ...args) : Promise.resolve(false);
      };
      router.push    = guard;
      router.replace = guard;
    }

    // Dispatch the full event sequence a real pointer/mouse interaction fires
    const rect = galleryTrigger.getBoundingClientRect();
    const ex = rect.left + rect.width  / 2;
    const ey = rect.top  + rect.height / 2;
    const eOpts = { bubbles: true, cancelable: true, view: window, clientX: ex, clientY: ey };
    galleryTrigger.dispatchEvent(new PointerEvent("pointerdown", eOpts));
    galleryTrigger.dispatchEvent(new MouseEvent ("mousedown",   eOpts));
    galleryTrigger.dispatchEvent(new PointerEvent("pointerup",  eOpts));
    galleryTrigger.dispatchEvent(new MouseEvent ("mouseup",     eOpts));
    galleryTrigger.dispatchEvent(new MouseEvent ("click",       eOpts));

    await new Promise(r => setTimeout(r, 300));

    if (router && origPush)    router.push    = origPush;
    if (router && origReplace) router.replace = origReplace;
    if (anchor && savedHref)   anchor.setAttribute("href", savedHref);

    await new Promise(r => setTimeout(r, 1500)); // wait for gallery animation
  }

  // After gallery opens, collect only the currently displayed (largest visible) image.
  // This avoids picking up similar-listing thumbnails from the rest of the page,
  // since the gallery image is always the biggest preview on screen.
  function collectCurrentSlideImage() {
    let best = null, bestArea = 0;
    document.querySelectorAll("img[src*='os-cdn.com/previews/']").forEach(img => {
      if (!img.src || img.src.includes("avatar") || img.src.includes("placeholder")) return;
      const rect = img.getBoundingClientRect();
      // Must be large AND actually inside the viewport — off-screen slides have
      // the same dimensions but are translated outside the visible area
      const inViewport = rect.width > 100 && rect.height > 100 &&
                         rect.right > 0 && rect.left < window.innerWidth &&
                         rect.bottom > 0 && rect.top < window.innerHeight;
      if (inViewport) {
        const area = rect.width * rect.height;
        if (area > bestArea) { bestArea = area; best = img; }
      }
    });
    if (best) addImg(best.src);
  }

  // Read "N / M" counter anywhere in the page to get total slide count
  function getSlideTotal() {
    for (const el of document.querySelectorAll("span, div, p, strong")) {
      if (el.children.length > 0) continue;
      const m = (el.textContent || "").trim().match(/^(\d+)\s*\/\s*(\d+)$/);
      if (m) {
        const total = parseInt(m[2]);
        if (total >= 2 && total <= 50) return total;
      }
    }
    return 0;
  }

  // Advance to next slide using ArrowRight — works with all gallery/slider libraries
  // without needing to know any button class names
  function nextSlide() {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", keyCode: 39, bubbles: true }));
  }

  // Collect slide 1, then advance through all remaining slides
  collectCurrentSlideImage();

  const total = getSlideTotal();
  const maxAdvances = total > 1 ? total - 1 : 30;
  let noNewStreak = 0;

  for (let i = 0; i < maxAdvances; i++) {
    nextSlide();
    await new Promise(r => setTimeout(r, 3000)); // wait for lazy image to load
    const before = images.length;
    collectCurrentSlideImage();
    if (images.length === before) {
      if (++noNewStreak >= 3 && total === 0) break;
    } else {
      noNewStreak = 0;
    }
  }
  // Extra collect in case last slide was slow
  await new Promise(r => setTimeout(r, 1000));
  collectCurrentSlideImage();

  // Close gallery
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", keyCode: 27, bubbles: true }));
  await new Promise(r => setTimeout(r, 300));

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

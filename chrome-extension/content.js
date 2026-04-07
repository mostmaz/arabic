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
    const hash = (hashMatch ? hashMatch[1] : normalized).replace(/\.jpg(\.webp)$/, "$1");
    if (seenHash.has(hash)) return;
    seenHash.add(hash);
    images.push(normalized);
  }

  // Collect the largest CDN preview image that is fully inside the viewport.
  // When the gallery is open this is always the active slide image.
  function collectCurrentSlideImage() {
    let best = null, bestArea = 0;
    document.querySelectorAll("img[src*='os-cdn.com/previews/']").forEach(img => {
      if (!img.src || img.src.includes("avatar") || img.src.includes("placeholder")) return;
      const r = img.getBoundingClientRect();
      if (r.width > 100 && r.height > 100 &&
          r.right > 0 && r.left < window.innerWidth &&
          r.bottom > 0 && r.top  < window.innerHeight) {
        const area = r.width * r.height;
        if (area > bestArea) { bestArea = area; best = img; }
      }
    });
    if (best) addImg(best.src);
  }

  // Read total from the "N / M" counter shown in the gallery overlay
  function getSlideTotal() {
    for (const el of document.querySelectorAll("span, div, p, strong")) {
      if (el.children.length > 0) continue;
      const m = (el.textContent || "").trim().match(/^(\d+)\s*\/\s*(\d+)$/);
      if (m) { const t = parseInt(m[2]); if (t >= 1 && t <= 50) return t; }
    }
    return 0;
  }

  // Find the ">" next-slide button that appears on the right side of the gallery
  function findNextBtn() {
    // Try known slider class names first
    for (const s of [".swiper-button-next","[class*='swiper-button-next']",
                     ".slick-next","[class*='slick-next']"]) {
      const el = document.querySelector(s);
      if (el && el.tagName !== "A") return el;
    }
    // Position-based fallback: button on the right half of the screen,
    // vertically centred — that's the gallery ">" arrow
    for (const btn of document.querySelectorAll("button")) {
      const r = btn.getBoundingClientRect();
      if (r.width < 10 || r.height < 10) continue;
      if (r.left > window.innerWidth * 0.6 &&
          r.top  > window.innerHeight * 0.2 &&
          r.top  < window.innerHeight * 0.8) return btn;
    }
    return null;
  }

  // ── Step 1: open the gallery ────────────────────────────────────────────────
  // Prefer "Show More Photos" button (always a <button>, never navigates).
  // Fall back to clicking the main listing image (needs navigation guard).

  let galleryOpened = false;

  // Try "Show More Photos" button
  for (const el of document.querySelectorAll("button, [role='button']")) {
    if ((el.textContent || "").includes("Show More Photos")) {
      el.click();
      await new Promise(r => setTimeout(r, 1500));
      galleryOpened = true;
      break;
    }
  }

  // Fall back: click first listing image (block Next.js navigation)
  if (!galleryOpened) {
    const trigger = document.querySelector(
      "img[src*='os-cdn.com/previews/']:not([src*='avatar']):not([src*='placeholder'])"
    );
    if (trigger) {
      const anchor = trigger.closest("a");
      const savedHref = anchor?.getAttribute("href") ?? null;
      if (anchor) anchor.removeAttribute("href");

      const router = window.__NEXT_ROUTER_INSTANCE__ || window.next?.router || null;
      const origPush    = router?.push?.bind(router);
      const origReplace = router?.replace?.bind(router);
      if (router && origPush) {
        const guard = (url, ...args) => {
          const t = typeof url === "string" ? url : (url?.pathname ?? "");
          const ok = t.startsWith("?") || t.startsWith("#") ||
                     t.includes(listing_id) || t === location.pathname;
          return ok ? origPush.call(router, url, ...args) : Promise.resolve(false);
        };
        router.push = guard; router.replace = guard;
      }

      const rect = trigger.getBoundingClientRect();
      const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
      const eo = { bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy };
      trigger.dispatchEvent(new PointerEvent("pointerdown", eo));
      trigger.dispatchEvent(new MouseEvent ("mousedown",   eo));
      trigger.dispatchEvent(new PointerEvent("pointerup",  eo));
      trigger.dispatchEvent(new MouseEvent ("mouseup",     eo));
      trigger.dispatchEvent(new MouseEvent ("click",       eo));

      await new Promise(r => setTimeout(r, 300));
      if (router && origPush)    router.push    = origPush;
      if (router && origReplace) router.replace = origReplace;
      if (anchor && savedHref)   anchor.setAttribute("href", savedHref);
      await new Promise(r => setTimeout(r, 1500));
    }
  }

  // ── Step 2: collect slide 1, then advance through all slides ────────────────
  collectCurrentSlideImage();

  const total      = getSlideTotal();
  const maxClicks  = total > 1 ? total - 1 : 30;
  const nextBtn    = findNextBtn();
  let noNewStreak  = 0;

  for (let i = 0; i < maxClicks; i++) {
    if (nextBtn) {
      nextBtn.click();
    } else {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", keyCode: 39, bubbles: true }));
    }
    await new Promise(r => setTimeout(r, 3000));   // wait for lazy image
    const before = images.length;
    collectCurrentSlideImage();
    if (images.length === before) {
      if (++noNewStreak >= 3 && total === 0) break;
    } else {
      noNewStreak = 0;
    }
  }
  await new Promise(r => setTimeout(r, 1000));
  collectCurrentSlideImage();  // catch final slide

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

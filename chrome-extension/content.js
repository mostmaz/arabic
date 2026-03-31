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

  // Open gallery: click first listing image.
  // Two navigation-prevention layers are needed for Next.js <Link> components:
  //   1. Remove the <a> href (blocks browser default navigation)
  //   2. Stub router.push/replace (blocks Next.js client-side routing via onClick)
  const galleryTrigger = document.querySelector(
    "img[src*='os-cdn.com/previews/']:not([src*='avatar']):not([src*='placeholder'])"
  );
  if (galleryTrigger) {
    const anchor = galleryTrigger.closest("a");
    const savedHref = anchor?.getAttribute("href") ?? null;
    if (anchor) anchor.removeAttribute("href");

    // Stub the Next.js router so router.push() / router.replace() are no-ops
    const router = window.__NEXT_ROUTER_INSTANCE__ || window.next?.router || null;
    const origPush    = router?.push?.bind(router);
    const origReplace = router?.replace?.bind(router);
    const noop = () => Promise.resolve(false);
    if (router) { router.push = noop; router.replace = noop; }

    galleryTrigger.click();
    await new Promise(r => setTimeout(r, 300));

    // Restore router and href
    if (router && origPush)    router.push    = origPush;
    if (router && origReplace) router.replace = origReplace;
    if (anchor && savedHref)   anchor.setAttribute("href", savedHref);

    await new Promise(r => setTimeout(r, 800));
  }

  // Locate the gallery modal that just opened — scope all collection to it so we
  // never pick up similar-listing thumbnails from the rest of the page
  function findGalleryModal() {
    const sels = [
      "#listingViewGalleryModalDesktop", "#listingViewGalleryModal",
      "[class*='GalleryModal']",  "[class*='gallery-modal']",
      "[class*='ImageModal']",    "[class*='image-modal']",
      "[class*='PhotoViewer']",   "[class*='photo-viewer']",
      "[class*='LightBox']",      "[class*='lightbox']",
    ];
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el && el.getBoundingClientRect().height > 0) return el;
    }
    return null;
  }

  const galleryModal = findGalleryModal();

  // Collect only from gallery modal; fall back to full document if modal not found
  function collectSlideImages() {
    (galleryModal || document).querySelectorAll("img[src*='os-cdn.com/previews/']")
      .forEach(img => addImg(img.src));
  }

  // Read "N / M" slide counter scoped to the gallery
  function getSlideTotal() {
    const root = galleryModal || document;
    for (const el of root.querySelectorAll("span, div, p, strong")) {
      if (el.children.length > 0) continue;
      const m = (el.textContent || "").trim().match(/^(\d+)\s*\/\s*(\d+)$/);
      if (m) {
        const total = parseInt(m[2]);
        if (total >= 2 && total <= 50) return total;
      }
    }
    return 0;
  }

  // Find next-slide button, preferring gallery scope
  function findNextBtn() {
    const sels = [
      ".swiper-button-next", "[class*='swiper-button-next']",
      ".slick-next",         "[class*='slick-next']",
      "button[aria-label='Next']",        "button[aria-label='next']",
      "button[aria-label*='Next slide']", "button[aria-label*='next slide']",
      "[class*='next-btn']:not(a)", "[class*='nextBtn']:not(a)",
      "[class*='arrow-right']:not(a)",    "[class*='arrowRight']:not(a)",
    ];
    for (const root of [galleryModal, document].filter(Boolean)) {
      for (const s of sels) {
        const el = root.querySelector(s);
        if (el && el.tagName !== "A") return el;
      }
    }
    return null;
  }

  collectSlideImages(); // collect first/current slide

  const nextBtn = findNextBtn();
  if (nextBtn) {
    const total = getSlideTotal();
    const maxClicks = total > 1 ? total - 1 : 30;
    let noNewStreak = 0;

    for (let i = 0; i < maxClicks; i++) {
      nextBtn.click();
      await new Promise(r => setTimeout(r, 600));
      const before = images.length;
      collectSlideImages();
      if (images.length === before) {
        if (++noNewStreak >= 3 && total === 0) break;
      } else {
        noNewStreak = 0;
      }
    }
    await new Promise(r => setTimeout(r, 500));
    collectSlideImages();
  }

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

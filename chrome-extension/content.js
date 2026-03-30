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

function extractListing() {
  // Listing ID from URL
  const idMatch = location.pathname.match(/\/(\d{6,})/);
  const listing_id = idMatch ? idMatch[1] : "";

  // Phone — already visible when logged in
  let phone = "";
  const telLink = document.querySelector("a[href^='tel:']");
  if (telLink) {
    phone = telLink.getAttribute("href").replace("tel:", "").trim();
  }

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
      sendResponse({ type: "listing", data: extractListing() });
    } else {
      const urls = extractCategoryUrls();
      sendResponse({ type: "category", urls });
    }
  } else if (msg.action === "extract") {
    sendResponse({ data: extractListing() });
  }
  return true;
});

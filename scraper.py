"""
OpenSooq Mobile & Tablet Listings Scraper
Scrapes: https://iq.opensooq.com/ar/موبايل-تابلت

Two-phase approach:
  Phase 1 — collect listing URLs from index pages
  Phase 2 — visit each listing detail page one at a time, extract full data
"""

import asyncio
import csv
import json
import math
import random
import re
import argparse
from pathlib import Path
from dataclasses import dataclass, asdict, field, fields as dc_fields
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from playwright.async_api import async_playwright, Page, BrowserContext
from bs4 import BeautifulSoup
from tqdm import tqdm


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = (
    "https://iq.opensooq.com/ar/%D9%85%D9%88%D8%A8%D8%A7%D9%8A%D9%84-%D8%AA%D8%A7%D8%A8%D9%84%D8%AA"
)

# Realistic desktop Chrome user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.0.0",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
    {"width": 1536, "height": 864},
]

# Domains to block (ads, analytics, tracking) — speeds up loads + less fingerprinting
BLOCKED_DOMAINS = [
    "google-analytics.com", "googletagmanager.com", "facebook.net",
    "facebook.com/tr", "doubleclick.net", "googlesyndication.com",
    "scorecardresearch.com", "hotjar.com", "segment.com",
    "amplitude.com", "mixpanel.com", "intercom.io",
    "criteo.com", "taboola.com", "outbrain.com",
]

# Stealth JS injected into every page before any script runs
STEALTH_JS = """
() => {
    // 1. Hide webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. Realistic plugins list
    const pluginData = [
        { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',                description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
        { name: 'Native Client',      filename: 'internal-nacl-plugin',               description: '' },
    ];
    const pluginArray = Object.create(PluginArray.prototype);
    pluginData.forEach((p, i) => {
        const plugin = Object.create(Plugin.prototype);
        Object.defineProperty(plugin, 'name',        { value: p.name });
        Object.defineProperty(plugin, 'filename',    { value: p.filename });
        Object.defineProperty(plugin, 'description', { value: p.description });
        pluginArray[i] = plugin;
    });
    Object.defineProperty(pluginArray, 'length', { value: pluginData.length });
    pluginArray.item      = (i) => pluginArray[i];
    pluginArray.namedItem = (name) => pluginData.find(p => p.name === name) || null;
    pluginArray.refresh   = () => {};
    Object.defineProperty(navigator, 'plugins', { get: () => pluginArray });

    // 3. Realistic language list
    Object.defineProperty(navigator, 'languages', { get: () => ['ar-IQ', 'ar', 'en-US', 'en'] });

    // 4. Inject chrome runtime object
    if (!window.chrome) {
        window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {}, app: {} };
    }

    // 5. Permissions — return realistic state
    const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (_origQuery) {
        window.navigator.permissions.query = (params) =>
            params.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : _origQuery.call(window.navigator.permissions, params);
    }

    // 6. Hardware / memory — realistic values
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(_) {}

    // 7. Subtle canvas noise — breaks canvas fingerprinting
    const _noise = (Math.random() * 2) | 0;  // 0 or 1
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const img = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
            for (let i = 0; i < img.data.length; i += 400) img.data[i] ^= _noise;
            ctx.putImageData(img, 0, 0);
        }
        return _toDataURL.apply(this, [type, ...args]);
    };

    // 8. Hide automation in toString checks
    const _nativeToString = Function.prototype.toString;
    Function.prototype.toString = function() {
        if (this === window.navigator.permissions.query) return 'function query() { [native code] }';
        return _nativeToString.call(this);
    };
}
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Listing:
    listing_id: str
    title: str
    price: str
    description: str
    location: str
    date_posted: str
    condition: str
    seller_name: str
    url: str
    images: list = field(default_factory=list)       # all remote image URLs
    local_images: list = field(default_factory=list)  # downloaded local paths


# ---------------------------------------------------------------------------
# Human-like helpers
# ---------------------------------------------------------------------------

async def jitter_sleep(base_s: float, jitter_s: float = 0.5) -> None:
    """Sleep for base_s ± gaussian jitter."""
    t = base_s + random.gauss(0, jitter_s)
    await asyncio.sleep(max(0.1, t))


async def human_scroll(page: Page) -> None:
    """Scroll down in natural increments, then back up a little."""
    height = await page.evaluate("document.body.scrollHeight")
    viewport_h = page.viewport_size["height"] if page.viewport_size else 768
    pos = 0
    while pos < height:
        step = random.randint(200, 500)
        pos = min(pos + step, height)
        await page.evaluate(f"window.scrollTo({{top: {pos}, behavior: 'smooth'}})")
        await asyncio.sleep(random.uniform(0.08, 0.25))
    # small scroll back up — human behaviour
    await asyncio.sleep(random.uniform(0.3, 0.7))
    await page.evaluate(f"window.scrollTo({{top: {max(0, pos - random.randint(100, 300))}, behavior: 'smooth'}})")
    await asyncio.sleep(random.uniform(0.2, 0.5))


async def human_mouse_wander(page: Page) -> None:
    """Move mouse along a bezier-like path to a random point on the page."""
    vp = page.viewport_size or {"width": 1366, "height": 768}
    # pick a random target
    tx = random.randint(50, vp["width"] - 50)
    ty = random.randint(50, vp["height"] - 50)
    # intermediate control point
    cx = random.randint(50, vp["width"] - 50)
    cy = random.randint(50, vp["height"] - 50)
    steps = random.randint(12, 25)
    for i in range(steps + 1):
        t = i / steps
        # quadratic bezier
        x = int((1 - t) ** 2 * (vp["width"] // 2) + 2 * (1 - t) * t * cx + t ** 2 * tx)
        y = int((1 - t) ** 2 * (vp["height"] // 2) + 2 * (1 - t) * t * cy + t ** 2 * ty)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.01, 0.04))


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------

async def new_context(playwright, ua: str, viewport: dict) -> tuple:
    """Create a fresh browser + context with full stealth settings."""
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--window-size=1366,768",
            "--disable-extensions",
        ],
    )
    context = await browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale="ar-IQ",
        timezone_id="Asia/Baghdad",
        geolocation={"latitude": 33.3152, "longitude": 44.3661},  # Baghdad
        permissions=["geolocation"],
        extra_http_headers={
            "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    await context.add_init_script(f"({STEALTH_JS})()")

    # Block ad/analytics domains
    async def block_request(route, request):
        if any(d in request.url for d in BLOCKED_DOMAINS):
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", block_request)
    return browser, context


# ---------------------------------------------------------------------------
# Phase 1 — collect listing URLs from index pages
# ---------------------------------------------------------------------------

def extract_listing_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for a in soup.select("a[href]"):
        href = a["href"]
        # OpenSooq listing URLs contain a numeric post ID
        if re.search(r"/ar/.+/\d+", href) or re.search(r"post/\d+", href):
            full = href if href.startswith("http") else f"https://iq.opensooq.com{href}"
            if full not in urls:
                urls.append(full)
    return urls


def get_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    nums = []
    for a in soup.select("ul.pagination li a, .pager a, [class*='pagination'] a, [class*='page'] a"):
        t = a.get_text(strip=True)
        if t.isdigit():
            nums.append(int(t))
    if nums:
        return max(nums)
    el = soup.select_one("[data-last-page], [data-pages]")
    if el:
        try:
            return int(el.get("data-last-page") or el.get("data-pages"))
        except (TypeError, ValueError):
            pass
    return 1


async def collect_listing_urls(
    page: Page,
    max_pages: int,
) -> list[str]:
    """Phase 1: scrape index pages to gather all listing URLs."""
    all_urls: list[str] = []

    print(f"\n[Phase 1] Collecting listing URLs (up to {max_pages} pages) ...")
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    await jitter_sleep(2.0, 0.5)
    await human_scroll(page)

    html = await page.content()
    urls = extract_listing_urls(html)
    all_urls.extend(urls)
    total_pages = get_total_pages(html)
    pages_to_scrape = min(total_pages, max_pages)
    print(f"  Page 1: {len(urls)} URLs found | Total pages: {total_pages} | Will scan: {pages_to_scrape}")

    for p in range(2, pages_to_scrape + 1):
        url = f"{BASE_URL}?page={p}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await jitter_sleep(random.uniform(2.0, 4.0), 0.8)
            await human_scroll(page)
            html = await page.content()
            page_urls = extract_listing_urls(html)
            new = [u for u in page_urls if u not in all_urls]
            all_urls.extend(new)
            print(f"  Page {p}: {len(new)} new URLs (total: {len(all_urls)})")
        except Exception as e:
            print(f"  [warn] Index page {p} failed: {e}")
        await jitter_sleep(random.uniform(2.5, 5.0), 1.0)

    print(f"  Collected {len(all_urls)} listing URLs.\n")
    return all_urls


# ---------------------------------------------------------------------------
# Phase 2 — scrape each listing detail page
# ---------------------------------------------------------------------------

def parse_detail(html: str, url: str) -> Listing:
    soup = BeautifulSoup(html, "lxml")

    def first_text(*selectors) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(" ", strip=True)
        return ""

    # Listing ID from URL
    m = re.search(r"[/-](\d{6,})", url)
    listing_id = m.group(1) if m else ""

    title = first_text(
        "h1.postTitle", "h1[class*='title']", ".post-title h1",
        "h1", ".title", "[class*='PostTitle']",
    )

    price = first_text(
        ".postPrice", "[class*='price']", ".price-label",
        "[itemprop='price']", ".listing-price",
    )

    description = first_text(
        ".postDesc", "[class*='description']", ".desc",
        "[itemprop='description']", "#postDescription",
    )

    location = first_text(
        ".postLocation", "[class*='location']", ".city",
        "[itemprop='addressLocality']", "[class*='area']",
    )

    condition = first_text(
        "[class*='condition']", "[class*='Condition']",
        "[data-field='condition']",
    )

    seller_name = first_text(
        ".sellerName", "[class*='seller']", ".userName",
        "[itemprop='name']", ".user-name",
    )

    date_el = soup.select_one("time, [class*='date'], [datetime]")
    date_posted = ""
    if date_el:
        date_posted = date_el.get("datetime") or date_el.get_text(strip=True)

    # Collect ALL images from the detail page
    images: list[str] = []
    for img in soup.select(
        ".slick-slide img, .gallery img, [class*='gallery'] img, "
        "[class*='slider'] img, .postImages img, .post-images img, img[data-src], img[src]"
    ):
        src = img.get("data-src") or img.get("data-original") or img.get("src") or ""
        if src and src.startswith("http") and src not in images:
            # skip tiny icons/avatars
            if not any(x in src for x in ["placeholder", "avatar", "icon", "logo", "1x1"]):
                images.append(src)

    return Listing(
        listing_id=listing_id,
        title=title,
        price=price,
        description=description,
        location=location,
        date_posted=date_posted,
        condition=condition,
        seller_name=seller_name,
        url=url,
        images=images,
    )


async def scrape_detail_page(page: Page, url: str, referer: str) -> Optional[Listing]:
    """Visit one listing page and return a Listing, or None on failure."""
    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
            referer=referer,
        )
        await jitter_sleep(random.uniform(1.5, 3.0), 0.5)
        await human_mouse_wander(page)
        await human_scroll(page)
        await jitter_sleep(random.uniform(0.5, 1.5), 0.3)

        html = await page.content()
        listing = parse_detail(html, url)
        return listing
    except Exception as e:
        print(f"  [error] {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Image downloading
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return re.sub(r"[^\w-]", "_", s)


async def download_listing_images(
    listing: Listing,
    images_root: Path,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
) -> None:
    """Download all images for one listing into images/<listing_id>/."""
    if not listing.images:
        return
    folder = images_root / (listing.listing_id or _safe_name(listing.url[-20:]))
    folder.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://iq.opensooq.com/",
    }

    for idx, img_url in enumerate(listing.images):
        ext = Path(urlparse(img_url).path).suffix or ".jpg"
        ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)[:5]
        dest = folder / f"{idx:03d}{ext}"
        if dest.exists():
            listing.local_images.append(str(dest))
            continue
        async with sem:
            try:
                async with session.get(
                    img_url, headers=headers, timeout=aiohttp.ClientTimeout(total=25)
                ) as resp:
                    if resp.status == 200:
                        dest.write_bytes(await resp.read())
                        listing.local_images.append(str(dest))
                    else:
                        print(f"    [warn] Image HTTP {resp.status}: {img_url}")
            except Exception as e:
                print(f"    [warn] Image download failed: {e}")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def listing_to_row(l: Listing) -> dict:
    d = asdict(l) if hasattr(l, "__dataclass_fields__") else l.__dict__.copy()
    d["images"] = " | ".join(l.images)
    d["local_images"] = " | ".join(l.local_images)
    return d


def asdict(l: Listing) -> dict:
    return {f.name: getattr(l, f.name) for f in dc_fields(l)}


def save_csv(listings: list[Listing], path: Path) -> None:
    if not listings:
        return
    row_keys = list(asdict(listings[0]).keys())
    row_keys[row_keys.index("images")] = "images"
    row_keys[row_keys.index("local_images")] = "local_images"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=row_keys)
        writer.writeheader()
        for l in listings:
            row = asdict(l)
            row["images"] = " | ".join(l.images)
            row["local_images"] = " | ".join(l.local_images)
            writer.writerow(row)
    print(f"Saved {len(listings)} listings → {path}")


def save_json(listings: list[Listing], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(l) for l in listings], f, ensure_ascii=False, indent=2)
    print(f"Saved {len(listings)} listings → {path}")


def load_state(state_file: Path) -> set[str]:
    """Return set of already-scraped URLs."""
    if not state_file.exists():
        return set()
    try:
        return set(json.loads(state_file.read_text()))
    except Exception:
        return set()


def save_state(done: set[str], state_file: Path) -> None:
    state_file.write_text(json.dumps(list(done)))


# ---------------------------------------------------------------------------
# Main scrape orchestration
# ---------------------------------------------------------------------------

async def scrape(
    max_pages: int = 5,
    output_csv: Optional[str] = "listings.csv",
    output_json: Optional[str] = "listings.json",
    images_dir: Optional[str] = "images",
    delay_between: float = 4.0,
    resume: bool = True,
) -> list[Listing]:

    state_file = Path(".scrape_state.json")
    done_urls: set[str] = load_state(state_file) if resume else set()
    if done_urls:
        print(f"[resume] Skipping {len(done_urls)} already-scraped listings.")

    ua = random.choice(USER_AGENTS)
    vp = random.choice(VIEWPORTS)

    all_listings: list[Listing] = []
    img_sem = asyncio.Semaphore(6)  # max concurrent image downloads

    async with async_playwright() as pw:
        browser, context = await new_context(pw, ua, vp)
        page = await context.new_page()

        # ---- Phase 1: collect URLs ----------------------------------------
        listing_urls = await collect_listing_urls(page, max_pages)
        pending = [u for u in listing_urls if u not in done_urls]
        print(f"[Phase 2] Scraping {len(pending)} listing detail pages ...")

        # ---- Phase 2: scrape each listing one at a time --------------------
        async with aiohttp.ClientSession() as http_session:
            for i, url in enumerate(tqdm(pending, desc="Listings")):
                # Rotate UA/viewport every ~15 listings to vary fingerprint
                if i > 0 and i % 15 == 0:
                    await browser.close()
                    ua = random.choice(USER_AGENTS)
                    vp = random.choice(VIEWPORTS)
                    browser, context = await new_context(pw, ua, vp)
                    page = await context.new_page()
                    print(f"  [stealth] Rotated browser profile at listing {i}")

                referer = BASE_URL if i == 0 else (all_listings[-1].url if all_listings else BASE_URL)
                listing = await scrape_detail_page(page, url, referer)

                if listing:
                    # Download images immediately after scraping this listing
                    if images_dir:
                        await download_listing_images(
                            listing, Path(images_dir), http_session, img_sem
                        )
                    all_listings.append(listing)
                    done_urls.add(url)
                    save_state(done_urls, state_file)

                # Occasional longer pause (~every 20 listings) to avoid rate limits
                if (i + 1) % 20 == 0:
                    pause = random.uniform(15, 30)
                    print(f"  [cool-down] Pausing {pause:.0f}s after {i+1} listings ...")
                    await asyncio.sleep(pause)
                else:
                    await jitter_sleep(delay_between, delay_between * 0.4)

        await browser.close()

    print(f"\nDone. {len(all_listings)} listings scraped.")

    if output_csv:
        save_csv(all_listings, Path(output_csv))
    if output_json:
        save_json(all_listings, Path(output_json))

    # Clean up state file on full success
    if not pending or len(all_listings) == len(pending):
        state_file.unlink(missing_ok=True)

    return all_listings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape OpenSooq IQ mobile & tablet listings (one by one, stealth mode)"
    )
    parser.add_argument("--pages", type=int, default=5,
                        help="Index pages to scan for URLs (default: 5)")
    parser.add_argument("--csv", type=str, default="listings.csv")
    parser.add_argument("--json", type=str, default="listings.json")
    parser.add_argument("--no-csv", action="store_true")
    parser.add_argument("--no-json", action="store_true")
    parser.add_argument("--images-dir", type=str, default="images",
                        help="Directory for downloaded images (default: images/)")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--delay", type=float, default=4.0,
                        help="Base seconds between listing requests (default: 4.0)")
    parser.add_argument("--no-resume", action="store_true",
                        help="Ignore previous progress and start fresh")
    args = parser.parse_args()

    asyncio.run(
        scrape(
            max_pages=args.pages,
            output_csv=None if args.no_csv else args.csv,
            output_json=None if args.no_json else args.json,
            images_dir=None if args.no_images else args.images_dir,
            delay_between=args.delay,
            resume=not args.no_resume,
        )
    )


if __name__ == "__main__":
    main()

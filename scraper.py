"""
OpenSooq Scraper — manual URL-driven, login-aware
User pastes a URL; app scrapes that page. Login cookies are saved
so phone numbers are revealed on detail pages.
"""

import asyncio
import glob
import json
import random
import re
import threading
import time
import argparse
from pathlib import Path
from dataclasses import dataclass, field, fields as dc_fields
from typing import Optional, Callable
from urllib.parse import urlparse

import aiohttp
from playwright.async_api import async_playwright, Page
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
]

BLOCKED_DOMAINS = [
    "google-analytics.com", "googletagmanager.com", "facebook.net",
    "doubleclick.net", "googlesyndication.com", "scorecardresearch.com",
    "hotjar.com", "segment.com", "amplitude.com", "criteo.com",
    "taboola.com", "outbrain.com",
]

STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    const pluginData = [
        { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',                description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
        { name: 'Native Client',      filename: 'internal-nacl-plugin',               description: '' },
    ];
    const arr = Object.create(PluginArray.prototype);
    pluginData.forEach((p, i) => {
        const pl = Object.create(Plugin.prototype);
        ['name','filename','description'].forEach(k => Object.defineProperty(pl, k, { value: p[k] }));
        arr[i] = pl;
    });
    Object.defineProperty(arr, 'length', { value: 3 });
    arr.item = i => arr[i]; arr.namedItem = n => pluginData.find(p=>p.name===n)||null; arr.refresh=()=>{};
    Object.defineProperty(navigator, 'plugins', { get: () => arr });
    Object.defineProperty(navigator, 'languages', { get: () => ['ar-IQ','ar','en-US','en'] });
    if (!window.chrome) window.chrome = { runtime:{}, loadTimes:()=>{}, csi:()=>{}, app:{} };
    const _oq = navigator.permissions && navigator.permissions.query;
    if (_oq) navigator.permissions.query = p =>
        p.name==='notifications' ? Promise.resolve({state:Notification.permission}) : _oq.call(navigator.permissions,p);
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(_) {}
    const _n = (Math.random()*2)|0, _td = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(t,...a) {
        const c=this.getContext('2d');
        if(c){const d=c.getImageData(0,0,this.width||1,this.height||1);for(let i=0;i<d.data.length;i+=400)d.data[i]^=_n;c.putImageData(d,0,0);}
        return _td.apply(this,[t,...a]);
    };
}
"""

STATE_FILE    = Path("state.json")
LISTINGS_FILE = Path("listings.json")
COOKIES_FILE  = Path("cookies.json")

LOGIN_URL  = "https://iq.opensooq.com/ar/user/login"
BASE_DOMAIN = "iq.opensooq.com"


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
    phone: str
    url: str
    images: list = field(default_factory=list)
    local_images: list = field(default_factory=list)


def _asdict(l: Listing) -> dict:
    return {f.name: getattr(l, f.name) for f in dc_fields(l)}


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def write_state(**kwargs) -> None:
    try:
        current = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except Exception:
        current = {}
    current.update(kwargs)
    current["updated_at"] = time.time()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def read_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"status": "idle"}


# ---------------------------------------------------------------------------
# Listings persistence
# ---------------------------------------------------------------------------

def load_listings() -> list:
    try:
        if LISTINGS_FILE.exists():
            return json.loads(LISTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def save_listing(listing: Listing) -> None:
    all_listings = load_listings()
    d = _asdict(listing)
    ids = [l.get("listing_id") for l in all_listings]
    if listing.listing_id and listing.listing_id in ids:
        all_listings = [d if l.get("listing_id") == listing.listing_id else l for l in all_listings]
    else:
        all_listings.insert(0, d)
    LISTINGS_FILE.write_text(json.dumps(all_listings, ensure_ascii=False, indent=2), encoding="utf-8")


def purge_svg_images() -> None:
    """Delete any 000.svg files that were previously downloaded and remove them from listings.json."""
    images_root = Path("images")
    deleted = []
    if images_root.exists():
        for svg in images_root.rglob("000.svg"):
            svg.unlink(missing_ok=True)
            deleted.append(str(svg))

    if not deleted:
        return

    # Scrub references from listings.json
    all_listings = load_listings()
    changed = False
    for l in all_listings:
        before = l.get("local_images", [])
        after = [p for p in before if not p.endswith("000.svg")]
        if len(after) != len(before):
            l["local_images"] = after
            changed = True
    if changed:
        LISTINGS_FILE.write_text(json.dumps(all_listings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cleanup] removed {len(deleted)} 000.svg file(s)")


# ---------------------------------------------------------------------------
# Cookies persistence
# ---------------------------------------------------------------------------

def save_cookies(cookies: list) -> None:
    COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cookies() -> list:
    try:
        if COOKIES_FILE.exists():
            return json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def is_logged_in() -> bool:
    cookies = load_cookies()
    # Check for any cookie from opensooq domain — if we have session cookies we're logged in
    os_cookies = [c for c in cookies if "opensooq" in c.get("domain", "")]
    return len(os_cookies) > 0


def import_browser_cookies(raw: list) -> list:
    """
    Normalize cookies exported from browser extensions (Cookie-Editor, EditThisCookie, etc.)
    into Playwright-compatible format and save them.

    Browser extensions export slightly different field names:
      expirationDate → expires
      sameSite "unspecified"/"no_restriction" → "Lax"/"None"
      storeId, hostOnly, session fields are stripped
    """
    sameSite_map = {
        "unspecified": "Lax",
        "no_restriction": "None",
        "lax": "Lax",
        "strict": "Strict",
        "none": "None",
    }

    normalized = []
    for c in raw:
        name  = c.get("name", "")
        value = c.get("value", "")
        if not name:
            continue

        domain = c.get("domain", "")
        # Ensure domain starts with dot for cross-subdomain cookies
        if domain and not domain.startswith(".") and not domain.startswith("http"):
            domain = "." + domain

        expires = c.get("expires") or c.get("expirationDate") or -1

        raw_ss = str(c.get("sameSite", "Lax")).lower()
        same_site = sameSite_map.get(raw_ss, "Lax")

        normalized.append({
            "name":     name,
            "value":    value,
            "domain":   domain,
            "path":     c.get("path", "/"),
            "expires":  float(expires),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure":   bool(c.get("secure", False)),
            "sameSite": same_site,
        })

    save_cookies(normalized)
    return normalized


# ---------------------------------------------------------------------------
# Human-like helpers
# ---------------------------------------------------------------------------

async def jitter_sleep(base_s: float, jitter_s: float = 0.5) -> None:
    await asyncio.sleep(max(0.1, base_s + random.gauss(0, jitter_s)))


async def human_scroll(page: Page) -> None:
    height = await page.evaluate("document.body.scrollHeight")
    pos = 0
    while pos < height:
        pos = min(pos + random.randint(250, 500), height)
        await page.evaluate(f"window.scrollTo({{top:{pos},behavior:'smooth'}})")
        await asyncio.sleep(random.uniform(0.08, 0.22))
    await asyncio.sleep(random.uniform(0.3, 0.6))
    await page.evaluate(f"window.scrollTo({{top:{max(0,pos-200)},behavior:'smooth'}})")
    await asyncio.sleep(random.uniform(0.2, 0.4))


async def human_mouse_wander(page: Page) -> None:
    vp = page.viewport_size or {"width": 1366, "height": 768}
    tx, ty = random.randint(50, vp["width"]-50), random.randint(50, vp["height"]-50)
    cx, cy = random.randint(50, vp["width"]-50), random.randint(50, vp["height"]-50)
    steps = random.randint(10, 20)
    for i in range(steps + 1):
        t = i / steps
        x = int((1-t)**2*(vp["width"]//2) + 2*(1-t)*t*cx + t**2*tx)
        y = int((1-t)**2*(vp["height"]//2) + 2*(1-t)*t*cy + t**2*ty)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.01, 0.04))


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------

def _find_chrome() -> Optional[str]:
    import os
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    patterns = [
        str(Path.home() / "AppData/Local/ms-playwright/chromium-*/chrome-win/chrome.exe"),
        str(Path.home() / "AppData/Local/ms-playwright/chromium_headless_shell-*/chrome-win/headless_shell.exe"),
        str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        str(Path.home() / ".cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell"),
        "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
        "/root/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell",
    ]
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        if matches:
            return matches[-1]

    for candidate in [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium-browser",
        "/usr/bin/chromium", "/snap/bin/chromium",
    ]:
        if Path(candidate).is_file():
            return candidate

    return None


async def new_context(playwright, ua: str, viewport: dict, load_cookies_flag: bool = True) -> tuple:
    chrome_path = _find_chrome()
    kwargs: dict = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-extensions",
        ],
    }
    if chrome_path:
        kwargs["executable_path"] = chrome_path

    browser = await playwright.chromium.launch(**kwargs)
    context = await browser.new_context(
        user_agent=ua, viewport=viewport,
        locale="ar-IQ", timezone_id="Asia/Baghdad",
        geolocation={"latitude": 33.3152, "longitude": 44.3661},
        permissions=["geolocation"],
        extra_http_headers={
            "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="120","Google Chrome";v="120","Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    await context.add_init_script(f"({STEALTH_JS})()")

    # Inject saved cookies (login session)
    if load_cookies_flag:
        cookies = load_cookies()
        if cookies:
            await context.add_cookies(cookies)

    async def _block(route, req):
        if any(d in req.url for d in BLOCKED_DOMAINS):
            await route.abort()
        else:
            await route.continue_()
    await context.route("**/*", _block)

    return browser, context


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def do_login(phone: str, password: str) -> dict:
    """
    Log in to OpenSooq with mobile number + password (two-step flow).
    Step 1: enter phone number → click Next
    Step 2: enter password → submit
    Saves cookies on success. Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    ua = random.choice(USER_AGENTS)
    vp = random.choice(VIEWPORTS)

    # Strip country code prefix if user included it (e.g. +964 or 00964)
    phone = re.sub(r"^\+964|^00964", "", phone.strip()).strip()

    async with async_playwright() as pw:
        browser, context = await new_context(pw, ua, vp, load_cookies_flag=False)
        page = await context.new_page()

        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await jitter_sleep(2.0, 0.5)

            # ── Step 1: phone number field ───────────────────────────────────
            phone_sel = (
                "input[type='tel'], "
                "input[name='mobile'], input[name='phone'], input[name='mobileNumber'], "
                "input[id*='mobile'], input[id*='phone'], "
                "input[placeholder*='mobile'], input[placeholder*='phone'], "
                "input[placeholder*='رقم'], input[placeholder*='موبايل']"
            )
            await page.wait_for_selector(phone_sel, timeout=10000)
            await page.click(phone_sel)
            await jitter_sleep(0.4, 0.1)
            await page.fill(phone_sel, "")
            await page.type(phone_sel, phone, delay=random.randint(70, 130))
            await jitter_sleep(0.8, 0.2)

            # Click "Next" button
            next_sel = (
                "button:has-text('Next'), button:has-text('التالي'), "
                "button[type='submit'], input[type='submit']"
            )
            await page.click(next_sel)
            await jitter_sleep(2.0, 0.5)

            # ── Step 2: password field ───────────────────────────────────────
            pass_sel = "input[type='password']"
            try:
                await page.wait_for_selector(pass_sel, timeout=10000)
            except Exception:
                # Maybe it went straight to OTP or error
                html = await page.content()
                soup = BeautifulSoup(html, "lxml")
                err_el = soup.select_one("[class*='error'], [class*='alert'], .invalid")
                err_msg = err_el.get_text(strip=True) if err_el else "Phone step failed — check number."
                return {"ok": False, "error": err_msg}

            await page.fill(pass_sel, "")
            await page.type(pass_sel, password, delay=random.randint(70, 130))
            await jitter_sleep(0.8, 0.3)

            # Submit password
            submit_sel = (
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Login'), button:has-text('دخول'), "
                "button:has-text('تسجيل الدخول'), button:has-text('Sign in')"
            )
            await page.click(submit_sel)
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
            await jitter_sleep(2.5, 0.5)

            # ── Check success ────────────────────────────────────────────────
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            success = bool(
                soup.select_one("a[href*='logout'], a[href*='signout'], .user-menu, [class*='userAvatar'], [class*='UserAvatar']")
                or "logout" in html.lower()
            )

            if not success:
                err_el = soup.select_one("[class*='error'], [class*='alert'], .invalid, .alert-danger")
                err_msg = err_el.get_text(strip=True) if err_el else "Login failed — check phone number and password."
                return {"ok": False, "error": err_msg}

            cookies = await context.cookies()
            save_cookies(cookies)
            return {"ok": True}

        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Phone number reveal
# ---------------------------------------------------------------------------

async def reveal_phone(page: Page) -> str:
    """Click the show-phone button and return the revealed number.

    Strategy:
    1. Collect any tel: hrefs already on the page before clicking.
    2. Click the show-phone button.
    3. Wait for a NEW tel: href to appear (the revealed number).
    4. Return that number.
    5. If no tel: link appears, return empty string — never guess from page text.
    """

    def _tel_hrefs(html: str) -> list[str]:
        """Return all unique tel: href values found in the page."""
        soup = BeautifulSoup(html, "lxml")
        results = []
        for a in soup.select("a[href^='tel:']"):
            num = re.sub(r"[^\d+]", "", a["href"].replace("tel:", ""))
            if len(num) >= 8 and num not in results:
                results.append(num)
        return results

    phone_btn_selectors = [
        "[class*='showPhone']",
        "[class*='show-phone']",
        "[class*='ShowPhone']",
        "button[class*='phone']",
        "button[class*='Phone']",
        "a[class*='phone']:not([href^='tel:'])",
        "[data-action*='phone']",
        "[data-type*='phone']",
        "button:has-text('اظهار')",
        "button:has-text('الرقم')",
        "button:has-text('اتصل')",
    ]

    try:
        # Snapshot tel: links before we click anything
        before = _tel_hrefs(await page.content())

        # Find and click the show-phone button
        clicked = False
        for sel in phone_btn_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.scroll_into_view_if_needed()
                    await jitter_sleep(0.4, 0.1)
                    await loc.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            return ""

        # Wait up to 6 s for a new tel: link to appear
        try:
            await page.wait_for_selector("a[href^='tel:']", timeout=6000)
        except Exception:
            pass

        await jitter_sleep(0.8, 0.2)

        after = _tel_hrefs(await page.content())

        # Prefer newly appeared tel: links
        new_nums = [n for n in after if n not in before]
        if new_nums:
            return new_nums[0]

        # If the button revealed the same link (already present), return it
        if after:
            return after[0]

    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------------
# Detail page parser
# ---------------------------------------------------------------------------

def parse_detail(html: str, url: str) -> Listing:
    soup = BeautifulSoup(html, "lxml")

    def first_text(*selectors) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(" ", strip=True)
        return ""

    m = re.search(r"[/-](\d{6,})", url)
    listing_id = m.group(1) if m else ""

    title = first_text("h1.postTitle","h1[class*='title']","h1",".title","[class*='PostTitle']")
    price = first_text(".postPrice","[class*='price']","[itemprop='price']",".listing-price")
    description = first_text(".postDesc","[class*='description']","[itemprop='description']","#postDescription")
    location = first_text(".postLocation","[class*='location']",".city","[itemprop='addressLocality']")
    condition = first_text("[class*='condition']","[class*='Condition']","[data-field='condition']")
    seller_name = first_text(".sellerName","[class*='seller']","[itemprop='name']",".user-name")

    date_el = soup.select_one("time,[class*='date'],[datetime]")
    date_posted = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else ""

    # Images — only listing CDN preview URLs, normalized to full resolution
    images: list[str] = []
    seen: set[str] = set()
    for img in soup.select("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("src") or ""
        if "opensooq-images.os-cdn.com/previews/" not in src:
            continue
        if any(x in src for x in ["avatar", "placeholder", ".mp4"]):
            continue
        normalized = re.sub(r"/previews/[^/]+/", "/previews/2000x0/", src)
        m = re.search(r"/previews/[^/]+/(.+)", normalized)
        h = re.sub(r"\.jpg(\.webp)$", r"\1", m.group(1) if m else normalized)
        if h in seen:
            continue
        seen.add(h)
        images.append(normalized)

    return Listing(
        listing_id=listing_id, title=title, price=price,
        description=description, location=location, date_posted=date_posted,
        condition=condition, seller_name=seller_name, phone="", url=url, images=images,
    )


# ---------------------------------------------------------------------------
# Index page: extract listing URLs
# ---------------------------------------------------------------------------

def extract_listing_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for a in soup.select("a[href]"):
        href = a["href"]
        # Match OpenSooq listing URL patterns:
        # /ar/<category>/<title>-<id>  or  /post/<id>  or  any path ending in a long numeric id
        if (re.search(r"/ar/[^/]+/[^/]+-\d{5,}", href)
                or re.search(r"/post/\d+", href)
                or re.search(r"/\d{7,}(?:[/?#]|$)", href)):
            full = href if href.startswith("http") else f"https://iq.opensooq.com{href}"
            # Exclude pagination / category-only links
            if full not in urls and not re.search(r"[?&]page=", full):
                urls.append(full)
    return urls


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

async def download_listing_images(
    listing: Listing, images_root: Path,
    session: aiohttp.ClientSession, sem: asyncio.Semaphore,
) -> None:
    if not listing.images:
        return
    folder = images_root / (listing.listing_id or re.sub(r"[^\w-]", "_", listing.url[-20:]))
    folder.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": "https://iq.opensooq.com/"}

    for idx, img_url in enumerate(u for u in listing.images if "000.svg" not in u):
        ext = re.sub(r"[^a-zA-Z0-9.]", "", Path(urlparse(img_url).path).suffix or ".jpg")[:5]
        dest = folder / f"{idx:03d}{ext}"
        if dest.exists():
            if str(dest) not in listing.local_images:
                listing.local_images.append(str(dest))
            continue
        async with sem:
            try:
                async with session.get(img_url, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
                    if r.status == 200:
                        dest.write_bytes(await r.read())
                        listing.local_images.append(str(dest))
            except Exception as e:
                print(f"  [warn] image: {e}")


# ---------------------------------------------------------------------------
# Playwright image extractor (runs in page JS context)
# ---------------------------------------------------------------------------

async def extract_images_from_page(page: Page) -> list[str]:
    """
    Open the listing gallery, navigate every slide, collect the largest
    visible CDN preview image per slide.
    """
    images: list[str] = []
    seen: set[str] = set()

    def add_img(src: str) -> None:
        if not src or "opensooq-images.os-cdn.com/previews/" not in src:
            return
        if any(x in src for x in ("avatar", "placeholder", ".mp4")):
            return
        normalized = re.sub(r"/previews/[^/]+/", "/previews/2000x0/", src)
        m = re.search(r"/previews/[^/]+/(.+)", normalized)
        h = re.sub(r"\.jpg(\.webp)$", r"\1", m.group(1) if m else normalized)
        if h in seen:
            return
        seen.add(h)
        images.append(normalized)
        print(f"  [img] {normalized}", flush=True)

    async def collect_current() -> None:
        src = await page.evaluate("""() => {
            let best = null, bestArea = 0;
            document.querySelectorAll('img[src*="os-cdn.com/previews/"]').forEach(img => {
                if (!img.src || img.src.includes('avatar') || img.src.includes('placeholder')) return;
                const r = img.getBoundingClientRect();
                if (r.width > 100 && r.height > 100 &&
                    r.right > 0 && r.left < window.innerWidth &&
                    r.bottom > 0 && r.top  < window.innerHeight) {
                    const area = r.width * r.height;
                    if (area > bestArea) { bestArea = area; best = img.src; }
                }
            });
            return best;
        }""")
        if src:
            add_img(src)

    async def next_slide() -> None:
        # Try Swiper API first (bypasses focus issues), fall back to ArrowRight
        used_swiper = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('[class*="swiper"]')) {
                if (el.swiper && el.swiper.slides && el.swiper.slides.length > 1) {
                    el.swiper.slideNext();
                    return true;
                }
            }
            return false;
        }""")
        if not used_swiper:
            await page.keyboard.press("ArrowRight")

    async def get_total() -> int:
        els = await page.query_selector_all("span, div, p, strong")
        for el in els:
            try:
                txt = (await el.text_content() or "").strip()
                m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", txt)
                if m:
                    t = int(m.group(2))
                    if 2 <= t <= 50:
                        return t
            except Exception:
                pass
        return 0

    # Open gallery with a trusted Playwright click
    try:
        trigger = page.locator(
            "img[src*='os-cdn.com/previews/']:not([src*='avatar']):not([src*='placeholder'])"
        ).first
        if await trigger.count() > 0:
            await trigger.click()
            await asyncio.sleep(1.5)
            print("  [gallery] opened", flush=True)
    except Exception as e:
        print(f"  [gallery] open failed: {e}", flush=True)

    await collect_current()

    total = await get_total()
    print(f"  [gallery] total slides: {total}", flush=True)
    max_advances = (total - 1) if total > 1 else 30
    no_new = 0

    for i in range(max_advances):
        await next_slide()
        await asyncio.sleep(2.5)
        before = len(images)
        await collect_current()
        if len(images) == before:
            no_new += 1
            if no_new >= 3 and total == 0:
                break
        else:
            no_new = 0

    await asyncio.sleep(1.0)
    await collect_current()

    await page.keyboard.press("Escape")
    await asyncio.sleep(0.5)

    print(f"  [gallery] done — {len(images)} images", flush=True)
    return images


# ---------------------------------------------------------------------------
# Core scrape function (called per user request)
# ---------------------------------------------------------------------------

def is_listing_url(url: str) -> bool:
    return bool(re.search(r"[/-](\d{7,})", url))


async def scrape_page(
    url: str,
    images_dir: Optional[str] = "images",
    stop_event: Optional[threading.Event] = None,
    on_progress: Optional[Callable] = None,
) -> list[Listing]:
    """Scrape one URL. If it's a category page, visits each listing on it."""
    if stop_event is None:
        stop_event = threading.Event()

    def progress(**kw):
        write_state(**kw)
        if on_progress:
            on_progress(kw)

    ua  = random.choice(USER_AGENTS)
    vp  = random.choice(VIEWPORTS)
    results: list[Listing] = []
    logged_in = is_logged_in()

    progress(status="scraping", url=url, current=0, total=0,
             message="Starting browser..." + (" (logged in)" if logged_in else " (guest)"))

    async with async_playwright() as pw:
        browser, context = await new_context(pw, ua, vp, load_cookies_flag=True)
        page = await context.new_page()

        try:
            # Navigate first so redirects (e.g. short URLs like opn.so/xxx) are followed,
            # then decide based on the final URL whether it's a listing or category page.
            progress(message="Fetching page...")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            final_url = page.url   # URL after all redirects

            if is_listing_url(final_url) or is_listing_url(url):
                # ── Single listing ───────────────────────────────────────────
                progress(message="Fetching listing...", total=1, current=0)
                await jitter_sleep(2.0, 0.5)
                await human_mouse_wander(page)
                await human_scroll(page)

                # Reveal phone if logged in
                phone = ""
                if logged_in:
                    progress(message="Revealing phone number...")
                    phone = await reveal_phone(page)

                html = await page.content()
                listing = parse_detail(html, final_url)
                listing.phone = phone
                listing.images = await extract_images_from_page(page)
                progress(current=1, message=f"Parsed: {listing.title or final_url}")

                if images_dir:
                    progress(message="Downloading images...")
                    async with aiohttp.ClientSession() as http:
                        sem = asyncio.Semaphore(6)
                        await download_listing_images(listing, Path(images_dir), http, sem)

                save_listing(listing)
                results.append(listing)

            else:
                # ── Category / index page ────────────────────────────────────
                progress(message="Fetching index page...")
                # Wait for JS to inject listing cards; try known card selectors
                for card_sel in (
                    "a[href*='/ar/']",
                    "[class*='post'] a",
                    "[class*='listing'] a",
                    "[class*='card'] a",
                    "li a[href]",
                ):
                    try:
                        await page.wait_for_selector(card_sel, timeout=8000)
                        break
                    except Exception:
                        pass
                await jitter_sleep(2.5, 0.5)
                await human_scroll(page)
                # Re-evaluate scroll height after lazy-loaded cards appear
                await jitter_sleep(1.5, 0.3)
                html = await page.content()

                listing_urls = extract_listing_urls(html)
                total = len(listing_urls)
                print(f"  [index] found {total} listing URLs on {url}")
                for u in listing_urls[:5]:
                    print(f"    {u}")
                progress(total=total, message=f"Found {total} listings on page")

                scraped_urls = {l.get("url") for l in load_listings()}
                pending = [u for u in listing_urls if u not in scraped_urls]
                skip = total - len(pending)
                if skip:
                    progress(message=f"{skip} already scraped, processing {len(pending)} new")

                async with aiohttp.ClientSession() as http:
                    img_sem = asyncio.Semaphore(6)

                    for i, lurl in enumerate(pending):
                        if stop_event.is_set():
                            progress(status="stopped", message="Stopped by user.")
                            break

                        progress(current=i+1, total=len(pending),
                                 message=f"Listing {i+1}/{len(pending)}: {lurl}")

                        try:
                            await page.goto(lurl, wait_until="domcontentloaded",
                                            timeout=30000, referer=url)
                            await jitter_sleep(random.uniform(1.5, 3.0), 0.5)
                            await human_mouse_wander(page)
                            await human_scroll(page)

                            phone = ""
                            if logged_in:
                                phone = await reveal_phone(page)

                            html = await page.content()
                            listing = parse_detail(html, lurl)
                            listing.phone = phone
                            listing.images = await extract_images_from_page(page)

                            if images_dir:
                                await download_listing_images(listing, Path(images_dir), http, img_sem)

                            save_listing(listing)
                            results.append(listing)
                        except Exception as e:
                            print(f"  [error] {lurl}: {e}")

                        if i < len(pending) - 1 and not stop_event.is_set():
                            await jitter_sleep(random.uniform(3.0, 6.0), 1.0)

        finally:
            # Refresh cookies (session may have been extended)
            try:
                fresh = await context.cookies()
                if fresh:
                    save_cookies(fresh)
            except Exception:
                pass
            await browser.close()

    if not stop_event.is_set():
        progress(status="done", message=f"Done — {len(results)} listings scraped.")
    return results

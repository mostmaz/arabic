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
    return any(c.get("name", "").lower() in ("osauth", "sso_token", "user_token", "token", "auth")
               for c in cookies)


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

async def do_login(email: str, password: str) -> dict:
    """
    Log in to OpenSooq using Playwright.
    Saves cookies on success. Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    ua = random.choice(USER_AGENTS)
    vp = random.choice(VIEWPORTS)

    async with async_playwright() as pw:
        browser, context = await new_context(pw, ua, vp, load_cookies_flag=False)
        page = await context.new_page()

        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await jitter_sleep(2.0, 0.5)

            # Fill email
            email_sel = (
                "input[type='email'], input[name='email'], "
                "input[id*='email'], input[placeholder*='email'], "
                "input[placeholder*='ايميل'], input[name='username']"
            )
            await page.fill(email_sel, "")
            await page.type(email_sel, email, delay=random.randint(60, 120))
            await jitter_sleep(0.5, 0.2)

            # Fill password
            pass_sel = "input[type='password']"
            await page.fill(pass_sel, "")
            await page.type(pass_sel, password, delay=random.randint(60, 120))
            await jitter_sleep(0.8, 0.3)

            # Submit
            submit_sel = (
                "button[type='submit'], input[type='submit'], "
                "button:has-text('تسجيل'), button:has-text('دخول'), "
                "button:has-text('Login'), button:has-text('Sign in')"
            )
            await page.click(submit_sel)
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
            await jitter_sleep(2.0, 0.5)

            # Check success — look for logout link or user menu
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            success = bool(
                soup.select_one("a[href*='logout'], a[href*='signout'], .user-menu, [class*='userAvatar']")
                or "logout" in html.lower()
            )

            if not success:
                # Check for error message
                err_el = soup.select_one(".alert-danger, .error-msg, [class*='error']")
                err_msg = err_el.get_text(strip=True) if err_el else "Login failed — check credentials."
                return {"ok": False, "error": err_msg}

            # Save cookies
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
    """Click the 'show phone' button and return the revealed number."""
    phone_btn_sel = (
        "button[class*='phone'], button[class*='Phone'], "
        "a[class*='phone'], a[class*='Phone'], "
        "[class*='showPhone'], [class*='show-phone'], "
        "button:has-text('اظهار'), button:has-text('الرقم'), "
        "button:has-text('Show'), button:has-text('Call'), "
        "[data-action*='phone'], [data-type*='phone']"
    )
    try:
        btn = page.locator(phone_btn_sel).first
        if await btn.count() > 0:
            await btn.scroll_into_view_if_needed()
            await jitter_sleep(0.5, 0.2)
            await btn.click()
            await jitter_sleep(1.5, 0.3)

            # Phone number should now be visible
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            for sel in [
                "[class*='phone'] a[href^='tel:']",
                "a[href^='tel:']",
                "[class*='phone']",
                "[class*='Phone']",
                "[data-type*='phone']",
            ]:
                el = soup.select_one(sel)
                if el:
                    number = el.get("href", "").replace("tel:", "") or el.get_text(strip=True)
                    cleaned = re.sub(r"[^\d+]", "", number)
                    if len(cleaned) >= 8:
                        return cleaned
    except Exception:
        pass

    # Fallback: extract any phone-like number from page
    html = await page.content()
    matches = re.findall(r"(?:07\d{8,9}|\+964\s?\d+)", html)
    return matches[0] if matches else ""


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

    # Images
    images: list[str] = []
    for img in soup.select(
        ".slick-slide img,.gallery img,[class*='gallery'] img,"
        "[class*='slider'] img,.postImages img,img[data-src],img[src]"
    ):
        src = img.get("data-src") or img.get("data-original") or img.get("src") or ""
        if src and src.startswith("http") and src not in images:
            if not any(x in src for x in ["placeholder","avatar","icon","logo","1x1"]):
                images.append(src)

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
        if re.search(r"/ar/.+/\d+", href) or re.search(r"post/\d+", href):
            full = href if href.startswith("http") else f"https://iq.opensooq.com{href}"
            if full not in urls:
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

    for idx, img_url in enumerate(listing.images):
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
            if is_listing_url(url):
                # ── Single listing ───────────────────────────────────────────
                progress(message="Fetching listing...", total=1, current=0)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await jitter_sleep(2.0, 0.5)
                await human_mouse_wander(page)
                await human_scroll(page)

                # Reveal phone if logged in
                phone = ""
                if logged_in:
                    progress(message="Revealing phone number...")
                    phone = await reveal_phone(page)

                html = await page.content()
                listing = parse_detail(html, url)
                listing.phone = phone
                progress(current=1, message=f"Parsed: {listing.title or url}")

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
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await jitter_sleep(2.0, 0.5)
                await human_scroll(page)
                html = await page.content()

                listing_urls = extract_listing_urls(html)
                total = len(listing_urls)
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

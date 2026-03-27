"""
OpenSooq Mobile & Tablet Listings Scraper
Scrapes: https://iq.opensooq.com/ar/موبايل-تابلت
"""

import asyncio
import csv
import json
import random
import re
import argparse
from pathlib import Path
from dataclasses import dataclass, asdict, fields
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from playwright.async_api import async_playwright, Page, BrowserContext
from bs4 import BeautifulSoup
from tqdm import tqdm


IMAGE_DIR = Path("images")


BASE_URL = "https://iq.opensooq.com/ar/%D9%85%D9%88%D8%A8%D8%A7%D9%8A%D9%84-%D8%AA%D8%A7%D8%A8%D9%84%D8%AA"


@dataclass
class Listing:
    title: str
    price: str
    location: str
    date_posted: str
    url: str
    image_url: str
    listing_id: str
    local_image: str = ""  # path to downloaded image on disk


async def random_delay(min_ms: int = 800, max_ms: int = 2500) -> None:
    """Sleep a random amount to mimic human browsing."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _image_filename(listing_id: str, image_url: str) -> str:
    """Derive a safe local filename from listing_id + original extension."""
    ext = Path(urlparse(image_url).path).suffix or ".jpg"
    ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)[:5]  # sanitise
    safe_id = re.sub(r"[^\w-]", "_", listing_id) if listing_id else "unknown"
    return f"{safe_id}{ext}"


async def download_images(
    listings: list["Listing"],
    images_dir: Path,
    concurrency: int = 8,
) -> None:
    """Download listing images concurrently; skips already-downloaded files."""
    images_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(concurrency)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://iq.opensooq.com/",
    }

    async def fetch_one(session: aiohttp.ClientSession, listing: "Listing") -> None:
        if not listing.image_url:
            return
        filename = _image_filename(listing.listing_id, listing.image_url)
        dest = images_dir / filename
        if dest.exists():
            listing.local_image = str(dest)
            return
        async with sem:
            try:
                async with session.get(
                    listing.image_url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status == 200:
                        dest.write_bytes(await resp.read())
                        listing.local_image = str(dest)
                    else:
                        print(f"[warn] Image {resp.status}: {listing.image_url}")
            except Exception as e:
                print(f"[warn] Failed to download image for {listing.listing_id}: {e}")

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_one(session, l) for l in listings if l.image_url]
        for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Downloading images"):
            await coro


async def setup_browser(playwright) -> tuple:
    """Launch a stealth-ish Chromium browser."""
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
        locale="ar-IQ",
        extra_http_headers={
            "Accept-Language": "ar,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    # Remove webdriver fingerprint
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, context


def parse_listings(html: str) -> list[Listing]:
    """Parse listing cards from page HTML."""
    soup = BeautifulSoup(html, "lxml")
    listings = []

    # OpenSooq listing cards — try multiple selector patterns
    cards = (
        soup.select("li.postItem")
        or soup.select("div.postItem")
        or soup.select("[data-postid]")
        or soup.select(".listing-item")
        or soup.select("article.post-card")
    )

    for card in cards:
        try:
            # Listing ID
            listing_id = (
                card.get("data-postid")
                or card.get("data-id")
                or card.get("id", "").replace("post_", "")
                or ""
            )

            # Title
            title_el = (
                card.select_one(".postTitle")
                or card.select_one(".listing-title")
                or card.select_one("h2")
                or card.select_one("h3")
                or card.select_one("[class*='title']")
            )
            title = title_el.get_text(strip=True) if title_el else ""

            # Price
            price_el = (
                card.select_one(".postPrice")
                or card.select_one(".price")
                or card.select_one("[class*='price']")
            )
            price = price_el.get_text(strip=True) if price_el else ""

            # Location
            location_el = (
                card.select_one(".postLocation")
                or card.select_one(".location")
                or card.select_one("[class*='location']")
                or card.select_one("[class*='city']")
            )
            location = location_el.get_text(strip=True) if location_el else ""

            # Date
            date_el = (
                card.select_one(".postDate")
                or card.select_one("time")
                or card.select_one("[class*='date']")
                or card.select_one("[datetime]")
            )
            if date_el:
                date_posted = date_el.get("datetime") or date_el.get_text(strip=True)
            else:
                date_posted = ""

            # URL
            link_el = card.select_one("a[href]")
            if link_el:
                href = link_el["href"]
                url = href if href.startswith("http") else f"https://iq.opensooq.com{href}"
            else:
                url = ""

            # Image
            img_el = card.select_one("img[src]") or card.select_one("img[data-src]")
            if img_el:
                image_url = img_el.get("data-src") or img_el.get("src") or ""
            else:
                image_url = ""

            if title or url:
                listings.append(
                    Listing(
                        title=title,
                        price=price,
                        location=location,
                        date_posted=date_posted,
                        url=url,
                        image_url=image_url,
                        listing_id=listing_id,
                    )
                )
        except Exception as e:
            print(f"[warn] Failed to parse card: {e}")
            continue

    return listings


async def get_page_html(page: Page, url: str) -> str:
    """Navigate to URL and return the fully rendered HTML."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await random_delay(1000, 2000)

    # Scroll down to trigger lazy-loading
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    await random_delay(500, 1000)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await random_delay(500, 1000)

    return await page.content()


async def get_total_pages(page: Page, html: str) -> int:
    """Detect total number of pages from pagination."""
    soup = BeautifulSoup(html, "lxml")

    # Try common pagination patterns
    pag = soup.select("ul.pagination li a") or soup.select(".pager a") or soup.select("[class*='page'] a")

    page_numbers = []
    for a in pag:
        text = a.get_text(strip=True)
        if text.isdigit():
            page_numbers.append(int(text))

    if page_numbers:
        return max(page_numbers)

    # Try data attribute
    last_page_el = soup.select_one("[data-last-page]")
    if last_page_el:
        try:
            return int(last_page_el["data-last-page"])
        except (ValueError, KeyError):
            pass

    return 1  # fallback: single page


def build_page_url(base: str, page_num: int) -> str:
    """Build paginated URL."""
    if page_num <= 1:
        return base
    # OpenSooq uses ?page=N pattern
    return f"{base}?page={page_num}"


def save_csv(listings: list[Listing], path: Path) -> None:
    fieldnames = [f.name for f in fields(Listing)]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([asdict(l) for l in listings])
    print(f"Saved {len(listings)} listings to {path}")


def save_json(listings: list[Listing], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(l) for l in listings], f, ensure_ascii=False, indent=2)
    print(f"Saved {len(listings)} listings to {path}")


async def scrape(
    max_pages: int = 5,
    output_csv: Optional[str] = "listings.csv",
    output_json: Optional[str] = "listings.json",
    images_dir: Optional[str] = "images",
    headless: bool = True,
) -> list[Listing]:
    all_listings: list[Listing] = []

    async with async_playwright() as pw:
        browser, context = await setup_browser(pw)
        page = await context.new_page()

        print(f"Fetching page 1: {BASE_URL}")
        html = await get_page_html(page, BASE_URL)

        page1_listings = parse_listings(html)
        all_listings.extend(page1_listings)
        print(f"Page 1: found {len(page1_listings)} listings")

        total_pages = await get_total_pages(page, html)
        pages_to_scrape = min(total_pages, max_pages)
        print(f"Total pages detected: {total_pages} | Will scrape: {pages_to_scrape}")

        for p in tqdm(range(2, pages_to_scrape + 1), desc="Scraping pages"):
            url = build_page_url(BASE_URL, p)
            try:
                html = await get_page_html(page, url)
                page_listings = parse_listings(html)
                all_listings.extend(page_listings)
                print(f"Page {p}: found {len(page_listings)} listings")
            except Exception as e:
                print(f"[error] Page {p} failed: {e}")
                break
            await random_delay(1000, 3000)

        await browser.close()

    # Deduplicate by listing_id (keep first occurrence)
    seen = set()
    unique = []
    for l in all_listings:
        key = l.listing_id or l.url
        if key and key not in seen:
            seen.add(key)
            unique.append(l)
        elif not key:
            unique.append(l)

    print(f"\nTotal unique listings scraped: {len(unique)}")

    if images_dir:
        await download_images(unique, Path(images_dir))

    if output_csv:
        save_csv(unique, Path(output_csv))
    if output_json:
        save_json(unique, Path(output_json))

    return unique


def main():
    parser = argparse.ArgumentParser(
        description="Scrape OpenSooq IQ mobile & tablet listings"
    )
    parser.add_argument(
        "--pages", type=int, default=5,
        help="Maximum number of pages to scrape (default: 5)"
    )
    parser.add_argument(
        "--csv", type=str, default="listings.csv",
        help="Output CSV file path (default: listings.csv)"
    )
    parser.add_argument(
        "--json", type=str, default="listings.json",
        help="Output JSON file path (default: listings.json)"
    )
    parser.add_argument(
        "--no-csv", action="store_true",
        help="Disable CSV output"
    )
    parser.add_argument(
        "--no-json", action="store_true",
        help="Disable JSON output"
    )
    parser.add_argument(
        "--images-dir", type=str, default="images",
        help="Directory to save downloaded images (default: images/)"
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Disable image downloading"
    )
    args = parser.parse_args()

    asyncio.run(
        scrape(
            max_pages=args.pages,
            output_csv=None if args.no_csv else args.csv,
            output_json=None if args.no_json else args.json,
            images_dir=None if args.no_images else args.images_dir,
        )
    )


if __name__ == "__main__":
    main()

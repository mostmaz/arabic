"""
Quick test: scrape phone number from a single listing.
Run: python test_phone.py
"""
import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

URL = "https://iq.opensooq.com/ar/search/278818233"
COOKIES_FILE = Path("cookies.json")


async def main():
    cookies = []
    if COOKIES_FILE.exists():
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        print(f"[+] Loaded {len(cookies)} cookies")
    else:
        print("[!] No cookies.json found — running as guest")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # visible so you can see what's happening
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        if cookies:
            try:
                await context.add_cookies(cookies)
                print("[+] Cookies injected")
            except Exception as e:
                print(f"[!] Cookie inject error: {e}")

        page = await context.new_page()
        print(f"[+] Navigating to {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Print all buttons on the page
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        buttons = soup.select("button, a[class*='btn'], [class*='phone'], [class*='call']")
        print(f"\n[+] Phone/call related elements ({len(buttons)}):")
        for b in buttons[:20]:
            print(f"    tag={b.name} class={b.get('class','')} text={b.get_text(strip=True)[:50]!r}")

        # Check for tel: links before clicking
        tel_before = [a["href"] for a in soup.select("a[href^='tel:']")]
        print(f"\n[+] tel: links before click: {tel_before}")

        # Try clicking show-phone buttons
        phone_selectors = [
            "[class*='showPhone']",
            "[class*='show-phone']",
            "[class*='ShowPhone']",
            "button[class*='phone']",
            "button[class*='Phone']",
            "[data-action*='phone']",
            "button:has-text('اظهار')",
            "button:has-text('الرقم')",
            "button:has-text('اتصل')",
            "button:has-text('Show')",
        ]

        clicked = False
        for sel in phone_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    txt = await loc.inner_text()
                    print(f"\n[+] Clicking button: sel={sel!r} text={txt!r}")
                    await loc.scroll_into_view_if_needed()
                    await loc.click()
                    clicked = True
                    break
            except Exception as e:
                pass

        if not clicked:
            print("\n[!] No show-phone button found — checking all buttons:")
            all_btns = soup.select("button")
            for b in all_btns:
                print(f"    {b.get('class','')} | {b.get_text(strip=True)[:60]!r}")

        await asyncio.sleep(3)

        html2 = await page.content()
        soup2 = BeautifulSoup(html2, "lxml")
        tel_after = [a["href"] for a in soup2.select("a[href^='tel:']")]
        print(f"\n[+] tel: links after click: {tel_after}")

        # Regex fallback on full page
        matches = re.findall(r"07\d{8,9}", html2)
        print(f"[+] 07XXXXXXXX matches in page: {list(set(matches))[:10]}")

        print("\n[+] Waiting 10s — check the browser window...")
        await asyncio.sleep(10)
        await browser.close()


asyncio.run(main())

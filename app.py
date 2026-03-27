"""
OpenSooq Scraper — Flask web app
Provides:
  - Dashboard UI (/)
  - POST /api/scrape        — start scraping a URL
  - POST /api/stop          — stop current scrape
  - GET  /api/status        — scraper state
  - GET  /api/listings      — paginated listing data
  - DELETE /api/listings/<id> — delete one listing
  - POST /api/login         — log in to OpenSooq
  - DELETE /api/login       — log out (delete cookies)
  - GET  /api/login/status  — check login state
  - GET  /img/<path>        — serve downloaded images
"""

import asyncio
import json
import threading
from pathlib import Path

from flask import Flask, render_template, jsonify, request, send_from_directory

from scraper import (
    do_login, is_logged_in, load_cookies, save_cookies,
    load_listings, read_state, write_state, scrape_page,
    COOKIES_FILE, LISTINGS_FILE, STATE_FILE,
)

app = Flask(__name__)

IMAGES_DIR = Path("images")

# ── Scraper thread state ─────────────────────────────────────────────────────
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _is_running() -> bool:
    return _thread is not None and _thread.is_alive()


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Scraping control ─────────────────────────────────────────────────────────

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    global _thread, _stop_event

    if _is_running():
        return jsonify({"error": "A scrape is already in progress."}), 409

    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    if not url.startswith("http"):
        url = "https://" + url

    no_images = body.get("no_images", False)
    _stop_event = threading.Event()

    def _run():
        asyncio.run(scrape_page(
            url=url,
            images_dir=None if no_images else str(IMAGES_DIR),
            stop_event=_stop_event,
        ))

    _thread = threading.Thread(target=_run, name="scraper", daemon=True)
    _thread.start()
    return jsonify({"status": "started", "url": url})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not _is_running():
        return jsonify({"error": "No scrape running."}), 400
    _stop_event.set()
    return jsonify({"status": "stopping"})


@app.route("/api/status")
def api_status():
    state = read_state()
    state["is_running"] = _is_running()
    state["logged_in"] = is_logged_in()
    return jsonify(state)


# ── Listings ─────────────────────────────────────────────────────────────────

@app.route("/api/listings")
def api_listings():
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(48, max(1, int(request.args.get("per_page", 24))))
    q        = request.args.get("q", "").strip().lower()

    all_items = load_listings()

    if q:
        all_items = [
            l for l in all_items
            if q in (l.get("title","") + " " + l.get("description","") + " " + l.get("location","")).lower()
        ]

    total = len(all_items)
    start = (page - 1) * per_page
    items = all_items[start : start + per_page]

    # Resolve thumbnail path
    for item in items:
        local = item.get("local_images", [])
        if isinstance(local, list):
            first = local[0] if local else None
        else:
            parts = [p.strip() for p in str(local).split("|") if p.strip()]
            first = parts[0] if parts else None

        if first:
            # Convert OS path to URL path (strip images/ prefix)
            rel = Path(first).as_posix()
            if rel.startswith("images/"):
                rel = rel[len("images/"):]
            item["thumbnail"] = f"/img/{rel}"
        else:
            item["thumbnail"] = None

    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    })


@app.route("/api/listings/<listing_id>", methods=["DELETE"])
def api_delete_listing(listing_id: str):
    all_items = load_listings()
    new_items = [l for l in all_items if l.get("listing_id") != listing_id]
    if len(new_items) == len(all_items):
        return jsonify({"error": "Not found"}), 404
    LISTINGS_FILE.write_text(json.dumps(new_items, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"status": "deleted"})


# ── Login ────────────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    if _is_running():
        return jsonify({"error": "Cannot log in while scraping is in progress."}), 409

    body = request.get_json(silent=True) or {}
    phone    = (body.get("phone") or "").strip()
    password = (body.get("password") or "")

    if not phone or not password:
        return jsonify({"error": "phone and password are required"}), 400

    write_state(status="logging_in", message="Logging in to OpenSooq...")
    result = asyncio.run(do_login(phone, password))
    if result["ok"]:
        write_state(status="idle", message="Logged in successfully.")
        return jsonify({"status": "ok", "logged_in": True})
    else:
        write_state(status="idle", message=f"Login failed: {result['error']}")
        return jsonify({"error": result["error"]}), 401


@app.route("/api/login", methods=["DELETE"])
def api_logout():
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
    write_state(status="idle", message="Logged out.")
    return jsonify({"status": "logged_out"})


@app.route("/api/login/status")
def api_login_status():
    return jsonify({"logged_in": is_logged_in()})


# ── Static images ─────────────────────────────────────────────────────────────

@app.route("/img/<path:filepath>")
def serve_image(filepath: str):
    return send_from_directory(IMAGES_DIR, filepath)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    IMAGES_DIR.mkdir(exist_ok=True)
    print("\n  OpenSooq Dashboard → http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

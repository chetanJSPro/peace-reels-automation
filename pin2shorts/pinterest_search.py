#!/usr/bin/env python3
"""
pinterest_search.py — find public Pinterest video pins by keyword.

pin2shorts.py only accepts a pin/board/collection URL; it has no notion of a search
query. This module calls Pinterest's own search XHR endpoint (the same one the
pinterest.com search page calls from the browser once the page has loaded — no login,
no cookies of yours) and returns pin ids/urls for the video results, so automate.py can
turn a keyword list ("ai hanuman", "ai shiva", ...) into pin URLs to queue, instead of
you hand-collecting board links.

This is calling an undocumented internal endpoint, so it can break if Pinterest changes
its frontend. It only ever reads public search results (no login wall, no cookies) —
same boundary pin2shorts.py itself holds.
"""

from __future__ import annotations

import json
import time
from urllib.parse import quote

import requests

SEARCH_PAGE = "https://www.pinterest.com/search/pins/?q={query}&rs=typed"
RESOURCE_URL = "https://www.pinterest.com/resource/BaseSearchResource/get/"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _is_video_pin(pin: dict) -> bool:
    return bool(pin.get("videos") or pin.get("story_pin_data"))


def search_pins(query: str, limit: int = 10, timeout: float = 20.0, retries: int = 2) -> list[dict]:
    """Return up to `limit` public video pins for `query` as
    [{"pin_id", "url", "title"}, ...]. Best-effort: returns [] on any request/parse
    failure rather than raising, so one bad keyword doesn't kill a --discover run."""
    source_path = f"/search/pins/?q={quote(query)}&rs=typed"
    data_param = json.dumps({
        "options": {
            "query": query,
            "scope": "pins",
            "page_size": max(limit, 25),
            "rs": "typed",
            "redux_normalize_feed": True,
        },
        "context": {},
    })

    last_err = None
    for attempt in range(retries + 1):
        try:
            session = requests.Session()
            page = session.get(SEARCH_PAGE.format(query=quote(query)), headers=BASE_HEADERS, timeout=timeout)
            page.raise_for_status()
            xhr_headers = dict(BASE_HEADERS)
            xhr_headers.update({
                "Accept": "application/json, text/javascript, */*, q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "X-Pinterest-AppState": "active",
                "X-Pinterest-PWS-Handler": "www/search/[scope].js",
                "X-CSRFToken": session.cookies.get("csrftoken", ""),
                "Referer": SEARCH_PAGE.format(query=quote(query)),
            })
            resp = session.get(
                RESOURCE_URL,
                params={"source_url": source_path, "data": data_param},
                headers=xhr_headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            break
        except Exception as exc:  # noqa: BLE001 - best-effort scrape
            last_err = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  [pinterest_search] '{query}' failed: {last_err}")
            return []

    results = (payload.get("resource_response", {}).get("data", {}) or {}).get("results", [])
    out = []
    for pin in results:
        pid = pin.get("id")
        if not pid or not _is_video_pin(pin):
            continue
        title = pin.get("grid_title") or pin.get("title") or pin.get("description") or ""
        out.append({"pin_id": str(pid), "url": f"https://www.pinterest.com/pin/{pid}/", "title": title[:200]})
        if len(out) >= limit:
            break
    return out


def search_many(queries: list[str], per_query: int = 10, log=print) -> list[dict]:
    seen_ids: set[str] = set()
    out: list[dict] = []
    for q in queries:
        log(f"searching pinterest for '{q}' ...")
        found = search_pins(q, limit=per_query)
        new = [p for p in found if p["pin_id"] not in seen_ids]
        for p in new:
            seen_ids.add(p["pin_id"])
        out.extend(new)
        log(f"  -> {len(new)} new video pin(s)")
    return out


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Search Pinterest for video pins by keyword")
    p.add_argument("query", nargs="+", help="keyword(s), e.g. 'ai hanuman' 'ai shiva'")
    p.add_argument("--limit", type=int, default=10, help="max pins per keyword")
    args = p.parse_args()

    for r in search_many(args.query, per_query=args.limit):
        print(r["url"], "-", r["title"][:70])

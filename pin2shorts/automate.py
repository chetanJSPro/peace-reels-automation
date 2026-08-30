#!/usr/bin/env python3
"""
automate.py — hands-off queue runner.

Drop Pinterest links in queue.txt (one per line). This script drains the queue,
renders Shorts-ready files with the settings in config.json, generates upload
metadata, and records everything in manifest.csv + uploads.csv.

  python3 automate.py --add "https://pin.it/abc123"     # append links to the queue
  python3 automate.py --once                            # drain the queue, exit
  python3 automate.py --watch                           # keep polling (for cron/supervisor/desktop)
  python3 automate.py --status                          # what's queued / done / failed

Idempotent: pins already in manifest.csv are skipped, and processed lines move to
queue.done.txt, so it's safe to run every minute.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import pin2shorts as P
import pinterest_search as S

BASE = P.BASE
CONFIG = BASE / "config.json"
QUEUE = BASE / "queue.txt"
QUEUE_DONE = BASE / "queue.done.txt"
UPLOADS = BASE / "uploads.csv"

DEFAULTS = {
    "mode": "blur",
    "max_duration": 59,
    "watermark": "@bhagtivideotop",
    "fade": True,
    "skip_seen": True,
    "limit": None,
    "poll_seconds": 60,
    "hashtags": ["shorts", "bhakti", "bhagti", "krishna", "bhajan"],
    "title_template": "{title} {hashtag_first}",
    "description_template": (
        "{title}\n\n"
        "{hashtags}\n\n"
        "#Shorts #Bhakti #Bhajan"
    ),
    "min_duration": 3,
    "keywords": [],
    "per_keyword_limit": 6,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


# ---------------------------------------------------------------- queue ----
def read_queue() -> list[str]:
    if not QUEUE.exists():
        QUEUE.write_text("# one Pinterest link per line — automate.py drains this file\n", encoding="utf-8")
        return []
    return [l.strip() for l in QUEUE.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def drain(urls_done: list[str]) -> None:
    """Remove processed lines from queue.txt and append them to queue.done.txt."""
    if not urls_done:
        return
    lines = QUEUE.read_text(encoding="utf-8").splitlines()
    remaining = [l for l in lines if l.strip() not in set(urls_done)]
    QUEUE.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    with QUEUE_DONE.open("a", encoding="utf-8") as fh:
        for u in urls_done:
            fh.write(u + "\n")


def discover(cfg: dict) -> int:
    """Search Pinterest for cfg['keywords'] and queue any video pins not already
    downloaded (manifest.csv) or already sitting in queue.txt. Best-effort: a keyword
    that fails to scrape just yields 0 pins, it doesn't stop the others."""
    keywords = cfg.get("keywords") or []
    if not keywords:
        print("no keywords configured (config.json 'keywords') — nothing to discover")
        return 0
    already = P.seen_ids() | {u for u in read_queue() if "/pin/" in u}
    found = S.search_many(keywords, per_query=int(cfg.get("per_keyword_limit", 6)))
    new_urls = []
    for pin in found:
        if pin["pin_id"] in already or pin["url"] in already:
            continue
        new_urls.append(pin["url"])
        already.add(pin["pin_id"])
    if new_urls:
        add_urls(new_urls)
    print(f"discover: {len(found)} candidate(s), {len(new_urls)} new -> queued")
    return 0


def add_urls(urls: list[str]) -> None:
    with QUEUE.open("a", encoding="utf-8") as fh:
        for u in urls:
            fh.write(u.strip() + "\n")
    print(f"queued {len(urls)} link(s) -> {QUEUE}")


# ------------------------------------------------------------- metadata ----
def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    return text.strip(" .,-—|")


def make_metadata(row: dict, cfg: dict) -> dict:
    title = clean(row.get("title", "")) or "Bhakti video"
    if len(title) > 70:
        title = title[:67].rsplit(" ", 1)[0] + "…"
    tags = [t.lstrip("#").lower() for t in cfg["hashtags"]]
    hashtags = " ".join("#" + t.replace(" ", "") for t in tags)
    tpl_title = cfg["title_template"].format(
        title=title, hashtag_first="#" + (tags[0] if tags else "shorts"), hashtags=hashtags)
    desc = cfg["description_template"].format(
        title=title, hashtags=hashtags, url=row.get("url", ""), handle=cfg.get("watermark", ""))
    return {
        "file": Path(row["shorts_file"]).name,
        "title": clean(tpl_title)[:100],
        "description": desc[:4900],
        "tags": ",".join(tags),
        "source_url": row.get("url", ""),
        "uploaded": "no",
    }


def write_uploads(rows: list[dict], cfg: dict) -> None:
    new = [r for r in rows if r.get("status") == "rendered"]
    if not new:
        return
    meta = [make_metadata(r, cfg) for r in new]
    exists = UPLOADS.exists() and UPLOADS.stat().st_size > 0
    with UPLOADS.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(meta[0].keys()))
        if not exists:
            w.writeheader()
        w.writerows(meta)
    print(f"metadata for {len(meta)} clip(s) -> {UPLOADS}")


def status() -> None:
    queued = read_queue()
    rows = P.load_manifest()
    rendered = [r for r in rows if r.get("status") == "rendered"]
    failed = [r for r in rows if r.get("status") == "failed"]
    print(f"queued:    {len(queued)}")
    print(f"rendered:  {len(rendered)} -> {P.OUT_DIR}")
    print(f"failed:    {len(failed)}")
    for r in failed[-5:]:
        print(f"   ! {r['pin_id']}: {r.get('note','')[:90]}")
    if UPLOADS.exists():
        up = list(csv.DictReader(UPLOADS.open(encoding="utf-8")))
        pending = [u for u in up if u.get("uploaded", "no").lower() != "yes"]
        print(f"uploads:   {len(up) - len(pending)} done / {len(pending)} pending")


# ------------------------------------------------------------------ run ----
def run_once(cfg: dict, render_limit: int | None = None) -> int:
    urls = read_queue()
    if not urls:
        print("queue empty")
        return 0
    if render_limit:
        urls = urls[:render_limit]

    def log(msg=""):
        print(msg, flush=True)

    rows = P.process(
        urls,
        mode=cfg["mode"],
        max_duration=float(cfg["max_duration"]),
        watermark=cfg.get("watermark") or None,
        fade=bool(cfg.get("fade", True)),
        limit=cfg.get("limit"),
        skip_seen=bool(cfg.get("skip_seen", True)),
        log=log,
    )
    write_uploads(rows, cfg)
    drain([u for u in urls])
    ok = sum(1 for r in rows if r.get("status") == "rendered")
    print(f"\n{ok}/{len(rows)} rendered. Files in {P.OUT_DIR}")
    return 0


def main(argv=None) -> int:
    cfg = load_config()
    p = argparse.ArgumentParser(description="queue runner for pin2shorts")
    p.add_argument("--once", action="store_true", help="drain the queue once and exit")
    p.add_argument("--watch", action="store_true", help="keep polling every poll_seconds")
    p.add_argument("--add", nargs="+", metavar="URL", help="append links to queue.txt")
    p.add_argument("--status", action="store_true")
    p.add_argument("--discover", action="store_true",
                    help="search config.json 'keywords' on Pinterest and queue new video pins")
    p.add_argument("--poll", type=int, default=None, help="override poll_seconds")
    p.add_argument("--render-limit", type=int, default=None, help="max queue items to render this run")
    a = p.parse_args(argv)

    if a.add:
        add_urls(a.add)
        return 0
    if a.status:
        status()
        return 0
    if a.discover:
        return discover(cfg)
    if not (a.once or a.watch):
        p.print_help()
        return 1

    if a.once:
        return run_once(cfg, render_limit=a.render_limit)

    secs = a.poll or int(cfg.get("poll_seconds", 60))
    print(f"watching {QUEUE} every {secs}s — Ctrl+C to stop", flush=True)
    try:
        while True:
            try:
                if cfg.get("keywords"):
                    discover(cfg)
                run_once(cfg)
            except Exception as exc:
                print(f"cycle error: {exc}", flush=True)
            time.sleep(secs)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

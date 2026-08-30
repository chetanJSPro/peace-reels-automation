#!/usr/bin/env python3
"""
publish.py — push rendered, not-yet-uploaded rows from uploads.csv to YouTube.

Deliberately doesn't reimplement OAuth/upload: it shells out to the main repo's
src/uploader.py, which already knows how to refresh/store tokens and apply
privacy/thumbnail/caption settings from a config.yaml-style file. This pipeline uses
config.ai_bhagwan.yaml (repo root) so it publishes through a separate channel/token
from the original-content pipeline (config.yaml) — see that file for why.

  python3 publish.py                 # upload every pending row in uploads.csv
  python3 publish.py --limit 3       # cap how many uploads this run does
  python3 publish.py --dry-run       # show what would be uploaded, don't call the API
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
UPLOADS = BASE / "uploads.csv"
CONFIG_YAML = ROOT / "config.ai_bhagwan.yaml"


def load_uploads() -> list[dict]:
    if not UPLOADS.exists():
        return []
    with UPLOADS.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save_uploads(rows: list[dict]) -> None:
    if not rows:
        return
    with UPLOADS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def publish_pending(limit: int | None = None, dry_run: bool = False) -> int:
    rows = load_uploads()
    if not rows:
        print("uploads.csv is empty or missing — run automate.py --once first")
        return 0

    pending = [r for r in rows if r.get("uploaded", "no").lower() != "yes"]
    if limit:
        pending = pending[:limit]
    if not pending:
        print("nothing pending")
        return 0

    ok = fail = 0
    for row in pending:
        video_file = BASE / "shorts" / row["file"]
        if not video_file.exists():
            print(f"skip {row['file']}: rendered file missing")
            continue

        tags = [t for t in (row.get("tags") or "").split(",") if t]
        meta = {
            "title": row["title"],
            "description": row["description"],
            "tags": tags,
            "categoryId": "22",
            "video_file": str(video_file),
            "madeForKids": False,
            # Honest disclosure: these clips are AI-generated devotional art sourced from
            # Pinterest, not footage of real people/places.
            "containsSyntheticMedia": True,
        }

        if dry_run:
            print(f"[dry-run] would upload {video_file.name}: {meta['title']}")
            continue

        meta_path = BASE / f".meta_{video_file.stem}.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        print(f"uploading {video_file.name} ...")
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "src" / "uploader.py"), str(meta_path),
                 "--config", str(CONFIG_YAML)],
                cwd=str(ROOT),
            )
        finally:
            meta_path.unlink(missing_ok=True)

        if result.returncode == 0:
            row["uploaded"] = "yes"
            ok += 1
        else:
            print(f"upload FAILED for {video_file.name} (exit {result.returncode})")
            fail += 1

    if not dry_run:
        save_uploads(rows)
    print(f"\n{ok} uploaded, {fail} failed, {len(pending) - ok - fail} skipped")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Upload pending pin2shorts clips to YouTube")
    p.add_argument("--limit", type=int, default=None, help="max uploads this run")
    p.add_argument("--dry-run", action="store_true", help="print what would upload, don't call the API")
    a = p.parse_args(argv)
    return publish_pending(limit=a.limit, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())

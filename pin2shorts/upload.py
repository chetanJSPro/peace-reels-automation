#!/usr/bin/env python3
"""
upload.py — optional YouTube uploader (YouTube Data API v3).

Setup (one time):
  1. Google Cloud console → enable "YouTube Data API v3" → create OAuth client
     (Desktop app) → download as client_secret.json next to this file.
  2. pip install google-auth-oauthlib google-api-python-client
  3. python3 upload.py --auth            # browser sign-in once; token.json is cached

Usage:
  python3 upload.py --file shorts/123_blur.mp4 --title "..." --description "..." --tags a,b
  python3 upload.py --from-csv uploads.csv --privacy private     # uploads pending rows only
  python3 upload.py --from-csv uploads.csv --dry-run             # show what would upload

Uploads are marked uploaded=yes in uploads.csv. Default privacy is "private" so you
can review and schedule in Studio — change with --privacy public/unlisted.

Note: YouTube's API quota is generous but not infinite; the DailyQuotaExceeded error
just means wait 24h. Don't upload faster than you'd realistically review the content.
"""
from __future__ import annotations

import argparse
import csv
import http.client
import json
import random
import sys
import time
from pathlib import Path

import pin2shorts as P

BASE = P.BASE
CLIENT_SECRET = BASE / "client_secret.json"
TOKEN = BASE / "token.json"
UPLOADS = BASE / "uploads.csv"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
API = "youtube"
VER = "v3"


def creds():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise SystemExit("Run:  pip install google-auth-oauthlib google-api-python-client")
    if not CLIENT_SECRET.exists():
        raise SystemExit(f"Missing {CLIENT_SECRET} — see the setup notes at the top of upload.py")
    c = None
    if TOKEN.exists():
        c = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not c or not c.valid:
        if c and c.expired and c.refresh_token:
            c.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            c = flow.run_local_server(port=0)
        TOKEN.write_text(c.to_json(), encoding="utf-8")
    return c


def service():
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    return build(API, VER, credentials=creds())


def upload_one(svc, path: Path, title: str, desc: str, tags: list[str],
               privacy: str, category: str, retries: int = 5) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": title[:100],
            "description": desc[:4900],
            "tags": tags,
            "categoryId": category,
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(path), chunksize=8 << 20, resumable=True, mimetype="video/mp4")
    req = svc.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    for attempt in range(1, retries + 1):
        try:
            while resp is None:
                _, resp = req.next_chunk()
            return resp["id"]
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and attempt < retries:
                wait = (2 ** attempt) + random.random()
                print(f"  retry {attempt}/{retries} in {wait:.1f}s ({e.resp.status})")
                time.sleep(wait)
                continue
            raise
        except (http.client.HTTPException, OSError) as e:
            if attempt < retries:
                wait = (2 ** attempt) + random.random()
                print(f"  network error, retry {attempt}/{retries} in {wait:.1f}s ({e})")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("upload failed after retries")


def mark_done(name: str) -> None:
    rows = list(csv.DictReader(UPLOADS.open(encoding="utf-8")))
    for r in rows:
        if r["file"] == name:
            r["uploaded"] = "yes"
    with UPLOADS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="upload rendered clips to YouTube")
    p.add_argument("--auth", action="store_true", help="run the OAuth flow and cache token.json")
    p.add_argument("--file", type=Path, help="a single mp4 to upload")
    p.add_argument("--title", default="")
    p.add_argument("--description", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--from-csv", action="store_true", help="upload pending rows from uploads.csv")
    p.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    p.add_argument("--category", default="22", help="YouTube category id (22 = People & Blogs)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)

    if a.auth:
        creds()
        print(f"authorized — token cached at {TOKEN}")
        return 0

    if a.file:
        if a.dry_run:
            print(f"would upload {a.file} as '{a.title}' ({a.privacy})")
            return 0
        svc = service()
        vid = upload_one(svc, a.file, a.title or a.file.stem, a.description,
                         [t for t in a.tags.split(",") if t], a.privacy, a.category)
        print(f"uploaded https://youtu.be/{vid}")
        return 0

    if a.from_csv:
        if not UPLOADS.exists():
            print("no uploads.csv — run automate.py first")
            return 1
        rows = list(csv.DictReader(UPLOADS.open(encoding="utf-8")))
        pending = [r for r in rows if r.get("uploaded", "no").lower() != "yes"]
        print(f"{len(pending)} pending upload(s)")
        if a.dry_run:
            for r in pending:
                print(f"  {r['file']}  ->  {r['title']}")
            return 0
        svc = service()
        for r in pending:
            path = P.OUT_DIR / r["file"]
            if not path.exists():
                print(f"  skip (missing) {r['file']}")
                continue
            print(f"  uploading {r['file']}")
            try:
                vid = upload_one(svc, path, r["title"], r["description"],
                                 [t for t in r["tags"].split(",") if t], a.privacy, a.category)
                print(f"    https://youtu.be/{vid}")
                mark_done(r["file"])
            except Exception as exc:
                print(f"    FAILED: {str(exc)[:200]}")
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

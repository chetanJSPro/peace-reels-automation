from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm

from utils import mentions_foreign_place


@dataclass
class DownloadedVideo:
    path: Path
    credit: str
    source_url: str
    clip_id: str


def _best_video_file(video: dict[str, Any], portrait: bool = True) -> dict[str, Any] | None:
    files = video.get("video_files") or []
    # Prefer mp4, vertical-ish, and width/height near 1080x1920 without being gigantic.
    candidates = []
    for f in files:
        if f.get("file_type") != "video/mp4" or not f.get("link"):
            continue
        w, h = int(f.get("width") or 0), int(f.get("height") or 0)
        if portrait and not (h >= w):
            continue
        score = abs(w - 1080) + abs(h - 1920)
        # Prefer HD but not the largest 4K unless no option.
        if w >= 720 and h >= 1280:
            score -= 500
        candidates.append((score, f))
    if not candidates:
        # Fallback to any mp4
        candidates = [(0, f) for f in files if f.get("file_type") == "video/mp4" and f.get("link")]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def search_pexels_videos(query: str, *, orientation: str = "portrait", per_page: int = 10) -> list[dict[str, Any]]:
    key = os.getenv("PEXELS_API_KEY")
    if not key:
        raise RuntimeError("PEXELS_API_KEY is not set. Create a free key at https://www.pexels.com/api/ and put it in .env")
    headers = {"Authorization": key}
    params = {"query": query, "orientation": orientation, "per_page": per_page}
    r = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("videos", [])


def download_file(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 1024:
        return out_path
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        with open(out_path, "wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=out_path.name) as bar:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))
    return out_path


def fetch_videos_for_queries(
    queries: list[str],
    downloads_dir: str | Path,
    *,
    max_downloads: int = 6,
    orientation: str = "portrait",
    exclude_ids: set[str] | None = None,
    blocklist_check=mentions_foreign_place,
) -> list[DownloadedVideo]:
    """`blocklist_check` gates candidates by their page URL/slug (rejects on True) -- defaults to
    the India-content project's foreign-place blocklist. Pass None for niches with no such
    constraint (e.g. a car-edits channel, where footage isn't location-restricted)."""
    out: list[DownloadedVideo] = []
    seen_ids: set[str] = set()
    exclude_ids = exclude_ids or set()
    # See pixabay.py's fetch_videos_for_queries for why: recently-used clips are parked here and
    # only used as a last resort so a thin query pool doesn't fail the render.
    fallback: list[tuple[str, dict[str, Any], str, str]] = []
    shuffled_queries = list(queries)
    random.shuffle(shuffled_queries)
    for q in shuffled_queries:
        if len(out) >= max_downloads:
            break
        try:
            videos = search_pexels_videos(q, orientation=orientation, per_page=30)
        except Exception as e:
            print(f"Pexels search failed for {q!r}: {e}")
            continue
        random.shuffle(videos)
        for v in videos:
            if len(out) >= max_downloads:
                break
            vid = str(v.get("id"))
            if vid in seen_ids:
                continue
            page_url = v.get("url") or f"https://www.pexels.com/video/{vid}/"
            if blocklist_check and blocklist_check(page_url):
                continue
            best = _best_video_file(v, portrait=(orientation == "portrait"))
            if not best:
                continue
            user = (v.get("user") or {}).get("name", "Pexels creator")
            credit = f"Pexels video by {user}: {page_url}"
            if vid in exclude_ids:
                fallback.append((vid, best, credit, page_url))
                continue
            seen_ids.add(vid)
            path = Path(downloads_dir) / f"pexels_{vid}.mp4"
            try:
                download_file(best["link"], path)
                out.append(DownloadedVideo(path=path, credit=credit, source_url=page_url, clip_id=vid))
                time.sleep(0.2)
            except Exception as e:
                print(f"Download failed for {page_url}: {e}")

    for vid, best, credit, page_url in fallback:
        if len(out) >= max_downloads:
            break
        seen_ids.add(vid)
        path = Path(downloads_dir) / f"pexels_{vid}.mp4"
        try:
            download_file(best["link"], path)
            out.append(DownloadedVideo(path=path, credit=credit, source_url=page_url, clip_id=vid))
            time.sleep(0.2)
        except Exception as e:
            print(f"Download failed for {page_url}: {e}")
    return out

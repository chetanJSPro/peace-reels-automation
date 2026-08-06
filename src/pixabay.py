from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm

from pexels import download_file


@dataclass
class DownloadedVideo:
    path: Path
    credit: str
    source_url: str


def _best_video_file(video: dict[str, Any], portrait: bool = True) -> dict[str, Any] | None:
    # Pixabay video sizes: large, medium, small, tiny. No vertical-native footage,
    # so pick the tallest/widest available; normalize_clip() will crop to portrait later.
    videos = video.get("videos") or {}
    candidates = []
    for size in ("large", "medium", "small", "tiny"):
        f = videos.get(size)
        if not f or not f.get("url"):
            continue
        w, h = int(f.get("width") or 0), int(f.get("height") or 0)
        if w <= 0 or h <= 0:
            continue
        score = abs(w - 1080) + abs(h - 1920)
        if w >= 1080 or h >= 1080:
            score -= 300
        candidates.append((score, f))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def search_pixabay_videos(query: str, *, per_page: int = 12) -> list[dict[str, Any]]:
    key = os.getenv("PIXABAY_API_KEY")
    if not key:
        raise RuntimeError("PIXABAY_API_KEY is not set. Create a free key at https://pixabay.com/api/docs/ and put it in .env")
    params = {
        "key": key,
        "q": query,
        "video_type": "film",
        "safesearch": "true",
        "per_page": max(3, min(per_page, 200)),
    }
    r = requests.get("https://pixabay.com/api/videos/", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("hits", [])


def fetch_videos_for_queries(
    queries: list[str],
    downloads_dir: str | Path,
    *,
    max_downloads: int = 6,
    orientation: str = "portrait",
) -> list[DownloadedVideo]:
    out: list[DownloadedVideo] = []
    seen_ids: set[str] = set()
    # Shuffle query order so a topic with several queries doesn't always exhaust the same first
    # ones before hitting max_downloads, and pull a bigger page + shuffle it so repeat runs of
    # the same topic don't always land on the same top-ranked results (was the main cause of
    # "same background videos every time" — every run used to take results 1..N in fixed order).
    shuffled_queries = list(queries)
    random.shuffle(shuffled_queries)
    for q in shuffled_queries:
        if len(out) >= max_downloads:
            break
        try:
            videos = search_pixabay_videos(q, per_page=30)
        except Exception as e:
            print(f"Pixabay search failed for {q!r}: {e}")
            continue
        random.shuffle(videos)
        for v in videos:
            if len(out) >= max_downloads:
                break
            vid = str(v.get("id"))
            if vid in seen_ids:
                continue
            best = _best_video_file(v, portrait=(orientation == "portrait"))
            if not best:
                continue
            seen_ids.add(vid)
            user = v.get("user", "Pixabay creator")
            page_url = v.get("pageURL") or f"https://pixabay.com/videos/id-{vid}/"
            credit = f"Pixabay video by {user}: {page_url}"
            path = Path(downloads_dir) / f"pixabay_{vid}.mp4"
            try:
                download_file(best["url"], path)
                out.append(DownloadedVideo(path=path, credit=credit, source_url=page_url))
                time.sleep(0.2)
            except Exception as e:
                print(f"Download failed for {page_url}: {e}")
    return out

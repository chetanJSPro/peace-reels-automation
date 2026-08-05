from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

GRAPH_BASE = "https://graph.facebook.com/v21.0"


class InstagramUploadError(RuntimeError):
    pass


def _public_video_url(video_file: str, public_repo_raw_base: str) -> str:
    """The Graph API fetches the video from a public HTTPS URL — it does not accept a direct
    file upload. We publish through the same GitHub repo the automation already runs from,
    referencing the committed video by its raw.githubusercontent.com URL."""
    if not public_repo_raw_base:
        raise InstagramUploadError(
            "instagram.public_repo_raw_base is not set in config.yaml. Instagram's Graph API "
            "needs a public HTTPS URL to fetch the video from; see README 'Instagram automation'."
        )
    rel = Path(video_file).as_posix()
    # Only the part of the path after the repo root is meaningful to the raw URL.
    if "output/" in rel:
        rel = "output/" + rel.split("output/", 1)[1]
    return f"{public_repo_raw_base.rstrip('/')}/{rel}"


def publish_reel(
    metadata_json: str | Path,
    cfg: dict[str, Any],
    *,
    video_url: str | None = None,
) -> str:
    """Official Meta Graph API Instagram content-publishing flow for a Reel:
    1. create a media container from a public video URL
    2. poll until Meta finishes processing it
    3. publish the container

    Requires env vars IG_BUSINESS_ACCOUNT_ID and IG_ACCESS_TOKEN (long-lived token with
    instagram_content_publish + pages_show_list scopes on a Page-linked IG Business/Creator
    account). No scraping, no browser automation — only the documented, ToS-compliant API.

    `video_url` should point at a public HTTPS copy of the video (e.g. a GitHub Release asset
    the workflow just published) — pass it explicitly when the caller already knows it, otherwise
    it's derived from config's instagram.public_repo_raw_base as a fallback.
    """
    ig_user_id = os.getenv("IG_BUSINESS_ACCOUNT_ID")
    access_token = os.getenv("IG_ACCESS_TOKEN")
    if not ig_user_id or not access_token:
        raise InstagramUploadError("IG_BUSINESS_ACCOUNT_ID / IG_ACCESS_TOKEN not set in environment.")

    data = json.loads(Path(metadata_json).read_text(encoding="utf-8"))
    ig_cfg = cfg.get("instagram", {})
    if not video_url:
        video_url = _public_video_url(data["video_file"], ig_cfg.get("public_repo_raw_base", ""))

    caption = data["title"]
    if data.get("description"):
        # Instagram captions are plain text; keep it short and hashtag-friendly rather than the
        # full YouTube description block.
        tags = " ".join(t for t in data.get("tags", []) if t)
        caption = f"{data['title']}\n\n#{tags.replace(' ', ' #')}" if tags else data["title"]

    create_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "access_token": access_token,
        },
        timeout=60,
    )
    create_resp.raise_for_status()
    creation_id = create_resp.json()["id"]

    # Meta processes the video asynchronously; poll status_code until FINISHED.
    for _ in range(60):
        status_resp = requests.get(
            f"{GRAPH_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        status_resp.raise_for_status()
        status = status_resp.json().get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise InstagramUploadError(f"Instagram failed to process the video (creation_id={creation_id}).")
        time.sleep(5)
    else:
        raise InstagramUploadError("Timed out waiting for Instagram to finish processing the video.")

    publish_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=60,
    )
    publish_resp.raise_for_status()
    media_id = publish_resp.json()["id"]
    print(f"Instagram Reel published: {media_id}")
    return media_id


def main() -> None:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from utils import load_yaml, project_root

    parser = argparse.ArgumentParser(description="Publish a generated video as an Instagram Reel via the Graph API.")
    parser.add_argument("metadata_json", help="output/.../metadata.json")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--video-url", default=None, help="Public HTTPS URL of the already-hosted video (e.g. a GitHub Release asset).")
    args = parser.parse_args()
    root = project_root()
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    cfg = load_yaml(cfg_path)
    publish_reel(args.metadata_json, cfg, video_url=args.video_url)


if __name__ == "__main__":
    main()

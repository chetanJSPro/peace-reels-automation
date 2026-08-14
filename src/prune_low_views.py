from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from uploader import get_youtube_service
from utils import project_root, resolve_path


def list_channel_videos(youtube) -> list[dict]:
    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_playlist = channels["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids: list[str] = []
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        video_ids.extend(item["contentDetails"]["videoId"] for item in resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    videos: list[dict] = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        resp = youtube.videos().list(part="snippet,statistics", id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            videos.append(
                {
                    "id": item["id"],
                    "title": item["snippet"]["title"],
                    "views": int(item.get("statistics", {}).get("viewCount", 0)),
                }
            )
    return videos


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete channel videos under a view-count threshold.")
    parser.add_argument("--threshold", type=int, default=100, help="Delete videos with fewer views than this.")
    parser.add_argument("--dry-run", action="store_true", help="List candidates without deleting.")
    args = parser.parse_args()

    root = project_root()
    load_dotenv(root / ".env")

    client_secrets = resolve_path(root, os.getenv("YOUTUBE_CLIENT_SECRETS") or "youtube/client_secret.json")
    token_file = resolve_path(root, os.getenv("YOUTUBE_TOKEN_FILE") or "youtube/token.json")
    youtube = get_youtube_service(client_secrets, token_file)

    videos = list_channel_videos(youtube)
    videos.sort(key=lambda v: v["views"])
    candidates = [v for v in videos if v["views"] < args.threshold]

    print(f"{len(videos)} total videos on channel. {len(candidates)} under {args.threshold} views:\n")
    for v in candidates:
        print(f"  {v['views']:>6} views  {v['title']}  ({v['id']})")

    if args.dry_run:
        print("\nDry run: nothing deleted.")
        return

    if not candidates:
        print("\nNothing to delete.")
        return

    print()
    for v in candidates:
        try:
            youtube.videos().delete(id=v["id"]).execute()
            print(f"Deleted: {v['title']} ({v['id']})")
        except Exception as e:
            print(f"Failed to delete {v['id']} ({v['title']}): {e}")


if __name__ == "__main__":
    main()

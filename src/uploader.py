from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from utils import project_root, resolve_path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def get_youtube_service(client_secrets_file: str | Path, token_file: str | Path):
    creds = None
    token_file = Path(token_file)
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError:
                print("Saved refresh token is no longer valid; starting a fresh login.")
        if not refreshed:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file), SCOPES)
            creds = flow.run_local_server(port=8080)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def resumable_upload(request):
    response = None
    error = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"Uploaded {int(status.progress() * 100)}%")
        except Exception as e:  # googleapiclient.errors.HttpError etc.
            error = e
            retry += 1
            if retry > 5:
                raise
            print(f"Retrying upload after error: {error}")
    return response


def upload_from_metadata(metadata_json: str | Path, cfg: dict[str, Any]) -> str:
    """Official YouTube Data API upload.

    Important: Google states videos uploaded through unverified API projects created after
    2020-07-28 are restricted to private viewing mode until the project passes an API audit.
    Use this script for private drafts first; do not attempt unofficial upload bypasses.
    """
    root = project_root()
    data = json.loads(Path(metadata_json).read_text(encoding="utf-8"))
    upload_cfg = cfg.get("upload", {})

    client_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS") or cfg.get("youtube_client_secrets") or "client_secret.json"
    token_file = os.getenv("YOUTUBE_TOKEN_FILE") or cfg.get("youtube_token_file") or "token.json"
    client_secrets_path = resolve_path(root, client_secrets)
    token_path = resolve_path(root, token_file)
    if not client_secrets_path or not client_secrets_path.exists():
        raise FileNotFoundError(
            "client_secret.json not found. Create OAuth credentials in Google Cloud and download the file."
        )
    youtube = get_youtube_service(client_secrets_path, token_path or root / "token.json")

    privacy = upload_cfg.get("privacy_status", "private")
    status_body = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": bool(data.get("madeForKids", False)),
    }
    publish_at = upload_cfg.get("publish_at")
    if publish_at:
        status_body["privacyStatus"] = "private"
        status_body["publishAt"] = publish_at
    if "containsSyntheticMedia" in data:
        status_body["containsSyntheticMedia"] = bool(data["containsSyntheticMedia"])

    body = {
        "snippet": {
            "title": data["title"],
            "description": data["description"],
            "tags": data.get("tags", []),
            "categoryId": data.get("categoryId", "22"),
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": status_body,
    }
    media = MediaFileUpload(data["video_file"], chunksize=1024 * 1024 * 8, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = resumable_upload(request)
    video_id = response["id"]
    print(f"Uploaded video ID: {video_id}")

    # Thumbnail/captions are best-effort finishing touches on a video that's already live —
    # a quota error here (common right after a big cleanup/delete batch) must not look like the
    # whole upload failed, and must not crash a 24x7 scheduled run.
    if upload_cfg.get("upload_thumbnail", True) and data.get("thumbnail_file") and Path(data["thumbnail_file"]).exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(data["thumbnail_file"], mimetype="image/jpeg"),
            ).execute()
            print("Thumbnail uploaded.")
        except Exception as e:
            print(f"Thumbnail upload skipped (video is still live): {e}")

    if upload_cfg.get("upload_captions", True) and data.get("srt_file") and Path(data["srt_file"]).exists():
        try:
            body = {"snippet": {"videoId": video_id, "language": "hi", "name": "Hindi", "isDraft": False}}
            youtube.captions().insert(
                part="snippet",
                body=body,
                media_body=MediaFileUpload(data["srt_file"], mimetype="application/octet-stream"),
            ).execute()
            print("Captions uploaded.")
        except Exception as e:
            print(f"Captions upload skipped (video is still live): {e}")

    return video_id


def main() -> None:
    import argparse
    from utils import load_yaml

    parser = argparse.ArgumentParser(description="Upload a generated video via the official YouTube Data API.")
    parser.add_argument("metadata_json", help="output/.../metadata.json")
    parser.add_argument("--config", default="config.example.yaml")
    args = parser.parse_args()
    root = project_root()
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    cfg = load_yaml(cfg_path)
    upload_from_metadata(args.metadata_json, cfg)


if __name__ == "__main__":
    main()

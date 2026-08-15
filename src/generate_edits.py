from __future__ import annotations

import argparse
import csv
import json
import os
import random
from pathlib import Path

from dotenv import load_dotenv

from beat_sync import build_beat_synced_background, detect_beats
from captions import Segment, write_ass
from pexels import fetch_videos_for_queries as fetch_pexels_videos
from pixabay import fetch_by_ids as fetch_pixabay_by_ids
from pixabay import fetch_videos_for_queries as fetch_pixabay_videos
from utils import (
    dedupe_paths,
    ensure_dir,
    ffprobe_duration,
    has_command,
    list_media,
    load_yaml,
    now_stamp,
    project_root,
    resolve_path,
    slugify,
)
from video_builder import build_final_video, make_thumbnail


def load_scenes(csv_path: str | Path) -> list[dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


CAR_KEYWORDS = [
    "car", "cars", "vehicle", "automobile", "auto", "race", "racing", "drift", "drifting",
    "drive", "driving", "engine", "wheel", "tire", "tyre", "road", "motor", "supercar",
    "muscle car", "jdm", "rally", "motorsport", "truck", "suv", "4x4", "off-road", "offroad",
    "headlight", "taillight", "garage", "exhaust", "speedometer", "dashboard", "steering",
    "burnout", "tuner", "sports car", "sportscar", "hypercar",
]


def is_car_relevant(text: str) -> bool:
    """Positive keyword check for Pixabay tags / Pexels URL slugs. Without this, a query like
    "drift car smoke tires" can still surface loosely-matched, totally unrelated results (e.g. a
    plain sky/clouds clip) from a keyword-search API -- this catches those before they end up as
    a random unrelated shot in the middle of a car edit."""
    t = (text or "").lower()
    if not t.strip():
        return True  # no metadata to judge by
    return any(k in t for k in CAR_KEYWORDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a beat-synced car-edit Short (no narration).")
    parser.add_argument("--config", default="config.car_edits.yaml", help="YAML config path")
    parser.add_argument("--scene", default=None, help="Force a data/car_scenes.csv label instead of picking randomly")
    parser.add_argument("--dry-run", action="store_true", help="Fetch footage/pick music but skip the ffmpeg render")
    args = parser.parse_args()

    root = project_root()
    load_dotenv(root / ".env")

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    cfg = load_yaml(cfg_path)

    if not has_command("ffmpeg") or not has_command("ffprobe"):
        raise RuntimeError("ffmpeg/ffprobe not found. Install FFmpeg first (see README).")

    fmt = cfg.get("format", {})
    visuals_cfg = cfg.get("visuals", {})
    music_cfg = cfg.get("music", {})
    beat_cfg = cfg.get("beat_sync", {})
    meta_cfg = cfg.get("metadata", {})

    width = int(fmt.get("width", 1080))
    height = int(fmt.get("height", 1920))
    fps = int(fmt.get("fps", 30))
    target_duration = float(fmt.get("target_seconds", 22))

    scenes = load_scenes(resolve_path(root, visuals_cfg.get("scenes_csv", "data/car_scenes.csv")))
    if not scenes:
        raise RuntimeError("data/car_scenes.csv has no rows.")
    scene = next((s for s in scenes if s["label"] == args.scene), None) if args.scene else random.choice(scenes)
    if scene is None:
        raise ValueError(f"No scene labeled {args.scene!r} in car_scenes.csv")

    music_dir = resolve_path(root, music_cfg.get("dir", "assets/music/car_edits"))
    tracks = list_media(music_dir, [".mp3", ".wav", ".m4a"]) if music_dir else []
    if not tracks and not args.dry_run:
        raise RuntimeError(
            f"No music found in {music_dir}. Drop 2-5 royalty-free / explicitly free-to-use tracks "
            "there first (e.g. from YouTube Audio Library) -- this pipeline doesn't source audio "
            "on its own, since track licensing needs a human check, not an automated download."
        )
    music_path = random.choice(tracks) if tracks else None
    music_credit = None
    if music_path:
        credits_map_path = music_dir / "credits.json"
        if credits_map_path.exists():
            credits_map = json.loads(credits_map_path.read_text(encoding="utf-8"))
            music_credit = credits_map.get(Path(music_path).name)
        if not music_credit:
            raise RuntimeError(
                f"{music_path.name} has no entry in {credits_map_path}. Every track needs a recorded "
                "license/attribution line before it can be used in a real upload -- add one rather "
                "than publishing music with no license record."
            )

    job_name = f"{now_stamp()}_{slugify(scene['label'])}"
    out_root = resolve_path(root, cfg.get("project", {}).get("output_dir", "output_edits")) or root / "output_edits"
    job_dir = ensure_dir(out_root / job_name)
    work_dir = ensure_dir(job_dir / "work")

    # 1) Footage -- no India/location relevance constraint here, just the scene's own queries.
    video_paths: list[Path] = []
    credits: list[str] = []
    queries = [t.strip() for t in (scene.get("search_terms") or "").split(",") if t.strip()]
    max_downloads = int(visuals_cfg.get("max_downloads", 10))
    orientation = visuals_cfg.get("orientation", "portrait")

    if os.getenv("PIXABAY_API_KEY"):
        downloads_dir = resolve_path(root, visuals_cfg.get("pixabay_downloads_dir", "assets/videos/car_edits/pixabay"))
        # Hand-picked pool first: reviewed against real Pixabay views/downloads/likes (a genuine
        # human-approval signal), then manually filtered for actual on-theme relevance -- blind
        # keyword search alone let generic high-view clips (traffic, city lights, even a jigsaw
        # puzzle that matched on stray keyword overlap) slip in as "car footage."
        curated = [i.strip() for i in (scene.get("curated_pixabay_ids") or "").split(",") if i.strip()]
        downloaded = fetch_pixabay_by_ids(curated, downloads_dir, orientation=orientation)
        video_paths.extend(d.path for d in downloaded)
        credits.extend(d.credit for d in downloaded)
        if len(video_paths) < max_downloads:
            downloaded = fetch_pixabay_videos(
                queries,
                downloads_dir,
                max_downloads=max_downloads - len(video_paths),
                orientation=orientation,
                relevance_check=is_car_relevant,
            )
            video_paths.extend(d.path for d in downloaded)
            credits.extend(d.credit for d in downloaded)

    if os.getenv("PEXELS_API_KEY") and len(video_paths) < max_downloads:
        downloads_dir = resolve_path(root, visuals_cfg.get("pexels_downloads_dir", "assets/videos/car_edits/pexels"))
        downloaded = fetch_pexels_videos(
            queries,
            downloads_dir,
            max_downloads=max_downloads - len(video_paths),
            orientation=orientation,
            # fetch_pexels_videos' blocklist_check rejects on True, so invert the positive check.
            blocklist_check=lambda url: not is_car_relevant(url),
        )
        video_paths.extend(d.path for d in downloaded)
        credits.extend(d.credit for d in downloaded)

    video_paths = dedupe_paths(video_paths)
    if not video_paths:
        raise RuntimeError("No footage found. Set PIXABAY_API_KEY / PEXELS_API_KEY in .env.")

    if args.dry_run:
        print(f"Dry run: scene={scene['label']} music={music_path} clips={len(video_paths)}")
        return

    # 2) Beat-sync the cuts to the chosen track.
    beats = detect_beats(music_path, duration=target_duration + 1.0)
    background = build_beat_synced_background(
        video_paths,
        beats,
        work_dir,
        total_duration=target_duration,
        width=width,
        height=height,
        fps=fps,
        cut_every_n_beats=int(beat_cfg.get("cut_every_n_beats", 1)),
        min_cut_seconds=float(beat_cfg.get("min_cut_seconds", 0.35)),
        max_cut_seconds=float(beat_cfg.get("max_cut_seconds", 3.0)),
        flash_every_n_cuts=int(beat_cfg.get("flash_every_n_cuts", 4)),
    )

    # 3) Title card -- one short punchy phrase for the first ~1.6s, then nothing (edits are
    # visual/music-led, not caption-led).
    ass_path = job_dir / "title_card.ass"
    write_ass(
        [Segment(0.25, 1.9, scene.get("title_card") or scene["label"].upper())],
        ass_path,
        width=width,
        height=height,
        duration=target_duration,
        use_location_pin=False,
        caption_font=cfg.get("style", {}).get("caption_font", "Arial"),
        caption_font_size=int(cfg.get("style", {}).get("caption_font_size", 100)),
    )

    final_mp4 = job_dir / "final_video.mp4"
    build_final_video(
        background,
        None,
        ass_path,
        final_mp4,
        duration=target_duration,
        music_path=music_path,
        music_volume=float(music_cfg.get("volume", 1.0)),
    )

    thumb = job_dir / "thumbnail.jpg"
    make_thumbnail(
        final_mp4,
        thumb,
        title=scene.get("title_card") or scene["label"],
        location_label=scene["label"],
        width=width,
        height=height,
        # Past the title card's fade-out (ends at 1.9s in the ASS above) so the extracted frame
        # doesn't already have "CONTROLLED CHAOS" burned in from the video itself, which
        # otherwise doubles up with make_thumbnail's own title text drawn on top of it.
        at_seconds=3.0,
    )

    hashtags = list(meta_cfg.get("hashtags", ["#caredit", "#shorts"]))
    scene_tag = scene.get("hashtag") or ""
    if scene_tag and scene_tag not in hashtags:
        hashtags.append(scene_tag)
    title = f"{scene.get('title_card') or scene['label']} 🔥 {scene_tag or hashtags[0]}"[:100]
    description = f"""{title}

{scene['label']} edit. {' '.join(hashtags)}

Credits:
{chr(10).join(f'- {c}' for c in credits) if credits else '- Own/licensed footage.'}
- {music_credit}
""".strip()

    metadata = {
        "title": title,
        "description": description,
        "tags": [h.lstrip("#") for h in hashtags],
        "categoryId": str(meta_cfg.get("category_id", "2")),
        "madeForKids": bool(meta_cfg.get("made_for_kids", False)),
        "video_file": str(final_mp4),
        "thumbnail_file": str(thumb),
        "duration_seconds": target_duration,
        "location_label": scene["label"],
        "credits": credits,
    }
    (job_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "description.txt").write_text(description, encoding="utf-8")

    print("\nDONE")
    print(f"Video: {final_mp4}")
    print(f"Metadata: {job_dir / 'metadata.json'}")

    upload_cfg = cfg.get("upload", {})
    if upload_cfg.get("enabled"):
        from uploader import upload_to_all_channels

        video_ids = upload_to_all_channels(job_dir / "metadata.json", cfg)
        metadata["youtube_video_ids"] = video_ids
        (job_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

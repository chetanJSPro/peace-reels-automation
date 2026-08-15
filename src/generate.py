from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from captions import distribute_segments, write_ass, write_srt
from content import (
    build_description,
    choose_idea,
    current_slot,
    load_ideas,
    package_from_idea,
    package_with_ollama,
    topic_indices_for_slot,
    topic_visual_style,
)
from pexels import fetch_videos_for_queries as fetch_pexels_videos
from pixabay import fetch_videos_for_queries as fetch_pixabay_videos
from tts import synthesize_kokoro
from utils import (
    dedupe_paths,
    ensure_dir,
    ffprobe_duration,
    has_command,
    ist_now_minutes,
    list_media,
    load_yaml,
    next_rotating_index_for_key,
    now_stamp,
    project_root,
    record_clip_ids,
    recent_clip_ids,
    resolve_path,
    slugify,
)
from video_builder import build_background, build_final_video, make_thumbnail


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a peaceful India/spiritual Shorts-style video legally.")
    parser.add_argument("--config", default="config.example.yaml", help="YAML config path")
    parser.add_argument("--topic-index", type=int, default=None, help="Row index in data/ideas.csv")
    parser.add_argument(
        "--time-slot",
        default=None,
        choices=["morning", "midday", "evening"],
        help="Force a content slot (see content.SLOT_TOPICS) instead of deriving it from the "
        "current IST clock. Used by the desktop dashboard's multi-video batch runs so 3 videos "
        "triggered back-to-back still span all three themes instead of all landing in whichever "
        "single slot the click happened to fall in.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Create scripts/metadata only; do not render")
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
    style = cfg.get("style", {})
    content_cfg = cfg.get("content", {})
    visuals_cfg = cfg.get("visuals", {})
    voice_cfg = cfg.get("voice", {})
    music_cfg = cfg.get("music", {})
    meta_cfg = cfg.get("metadata", {})

    width = int(fmt.get("width", 1080))
    height = int(fmt.get("height", 1920))
    fps = int(fmt.get("fps", 30))
    clip_seconds = float(fmt.get("clip_seconds", 5))
    ideas_csv = resolve_path(root, content_cfg.get("ideas_csv", "data/ideas.csv"))
    state_path = resolve_path(root, content_cfg.get("state_file", "data/state.json"))

    if args.topic_index is not None:
        topic_index = args.topic_index
    elif content_cfg.get("rotate_topics", True):
        # Auto-advance through data/ideas.csv on every run so an unattended 24x7 scheduler
        # never generates the same topic (and therefore near-identical video) twice in a row.
        # On top of that, match the topic's theme to *why* someone is scrolling Shorts right
        # now (morning intention-setting, lunch-break reflection, evening wind-down) instead of
        # picking from the full list regardless of time — each slot rotates its own pool
        # independently via next_rotating_index_for_key, so it doesn't repeat within itself.
        ideas = load_ideas(ideas_csv)
        slot = args.time_slot or current_slot(content_cfg.get("time_slots") or {}, ist_now_minutes())
        candidate_indices = topic_indices_for_slot(ideas, slot)
        pos = next_rotating_index_for_key(state_path, slot or "all", len(candidate_indices))
        topic_index = candidate_indices[pos]
    else:
        topic_index = int(content_cfg.get("topic_index", 0))

    idea = choose_idea(ideas_csv, topic_index=topic_index)

    # Resolve the visual/location *before* packaging the script, so the title/tags can carry the
    # hashtag that actually matches what's on screen (e.g. #kedarnath when Kedarnath footage was
    # picked) instead of a fixed config-wide tag that used to say "#rishikesh" on every video
    # regardless of location -- a real relevance mismatch between the tag and the content.
    locations_csv = resolve_path(root, content_cfg.get("locations_csv", "data/locations.csv"))
    visual_style = topic_visual_style(
        idea.get("topic", "inner_peace"),
        locations_csv,
        fallback_label=style.get("location_label", "RISHIKESH | UTTARAKHAND"),
        fallback_pixabay_queries=visuals_cfg.get("pixabay_queries") or ["Rishikesh Ganga India"],
        fallback_pexels_queries=visuals_cfg.get("pexels_queries") or ["Rishikesh Ganga river India vertical"],
    )
    location_label = visual_style["location_label"]

    base_hashtags = meta_cfg.get("hashtags", ["#meditation", "#innerpeace", "#shorts"])
    location_hashtag = visual_style["hashtag"]
    hashtags = base_hashtags + ([location_hashtag] if location_hashtag and location_hashtag not in base_hashtags else [])
    title_tag = location_hashtag or (base_hashtags[0] if base_hashtags else "")

    if content_cfg.get("use_ollama"):
        pkg = package_with_ollama(
            idea, hashtags, lines_per_short=int(content_cfg.get("lines_per_short", 5)), title_tag=title_tag
        )
    else:
        pkg = package_from_idea(
            idea, hashtags, lines_per_short=int(content_cfg.get("lines_per_short", 5)), title_tag=title_tag
        )

    job_name = f"{now_stamp()}_{slugify(pkg.topic)}"
    out_root = resolve_path(root, cfg.get("project", {}).get("output_dir", "output")) or root / "output"
    job_dir = ensure_dir(out_root / job_name)
    work_dir = ensure_dir(job_dir / "work")
    script_txt = job_dir / "script.txt"
    script_txt.write_text(pkg.narration_text, encoding="utf-8")

    if args.dry_run:
        print(f"Dry run created: {script_txt}")
        return

    # 1) Voiceover
    narration_wav = job_dir / "narration.wav"
    if voice_cfg.get("engine", "kokoro") != "kokoro":
        raise ValueError("Only Kokoro engine is implemented in this free/local template.")
    synthesize_kokoro(
        pkg.narration_text,
        narration_wav,
        lang_code=voice_cfg.get("lang_code", "h"),
        voice_id=voice_cfg.get("voice_id", "hm_omega"),
        speed=float(voice_cfg.get("speed", 0.94)),
        pause_seconds=float(voice_cfg.get("pause_seconds", 0.55)),
    )
    duration = ffprobe_duration(narration_wav) + 0.45
    if fmt.get("mode", "short") == "short" and duration > 58:
        print(f"WARNING: narration is {duration:.1f}s. For Shorts, keep final under 60s.")

    # 2) Captions
    segments = distribute_segments(pkg.subtitle_lines, duration)
    ass_path = job_dir / "captions_burnin.ass"
    srt_path = job_dir / "captions_upload.srt"
    write_ass(
        segments,
        ass_path,
        width=width,
        height=height,
        location_label=location_label,
        duration=duration,
        caption_font=style.get("caption_font", "Noto Sans Devanagari"),
        location_font=style.get("location_font", "Noto Sans Devanagari"),
        caption_font_size=int(style.get("caption_font_size", 76)),
        location_font_size=int(style.get("location_font_size", 54)),
        caption_margin_bottom=int(style.get("caption_margin_bottom", 240)),
        location_margin_top=int(style.get("location_margin_top", 95)),
        use_location_pin=bool(style.get("use_location_pin", True)),
    )
    write_srt(segments, srt_path)

    # 3) Visuals: local first, then stock APIs (Pixabay first — better India coverage — then Pexels).
    video_paths: list[Path] = []
    credits: list[str] = []
    local_dir = resolve_path(root, visuals_cfg.get("local_video_dir", "assets/videos"))
    if local_dir:
        locals_found = list_media(local_dir, [".mp4", ".mov", ".mkv", ".webm"])
        # Avoid reusing already-normalized work files if output is inside assets by mistake.
        video_paths.extend(locals_found)
        if locals_found:
            credits.append("Local/own footage in assets/videos (verify rights before publishing).")

    source = visuals_cfg.get("source", "auto")
    use_stock = source in ("auto", "pexels_or_local", "pixabay_or_local")
    max_downloads = int(visuals_cfg.get("max_downloads", 6))
    orientation = visuals_cfg.get("orientation", "portrait")

    if use_stock and os.getenv("PIXABAY_API_KEY"):
        downloads_dir = resolve_path(root, visuals_cfg.get("pixabay_downloads_dir", "assets/videos/pixabay")) or root / "assets/videos/pixabay"
        queries = visual_style["pixabay_queries"]
        downloaded = fetch_pixabay_videos(
            queries,
            downloads_dir,
            max_downloads=max_downloads,
            orientation=orientation,
            # Steer away from clips used in recent runs so the same handful of top-ranked
            # results for a niche query don't end up in nearly every video regardless of topic.
            exclude_ids=recent_clip_ids(state_path, "pixabay"),
        )
        video_paths = [d.path for d in downloaded] + video_paths
        credits.extend(d.credit for d in downloaded)
        record_clip_ids(state_path, "pixabay", [d.clip_id for d in downloaded])

    if use_stock and os.getenv("PEXELS_API_KEY") and len(video_paths) < max_downloads:
        downloads_dir = resolve_path(root, visuals_cfg.get("downloads_dir", "assets/videos/pexels")) or root / "assets/videos/pexels"
        downloaded = fetch_pexels_videos(
            visual_style["pexels_queries"],
            downloads_dir,
            max_downloads=max_downloads - len(video_paths),
            orientation=orientation,
            exclude_ids=recent_clip_ids(state_path, "pexels"),
        )
        video_paths = [d.path for d in downloaded] + video_paths
        credits.extend(d.credit for d in downloaded)
        record_clip_ids(state_path, "pexels", [d.clip_id for d in downloaded])

    # The local scan (rglob) can also pick up files inside the pixabay/pexels download
    # subfolders, and re-running the same queries can re-resolve the same cached file — dedupe
    # by resolved path so one clip doesn't silently dominate the round-robin below.
    video_paths = dedupe_paths(video_paths)

    if not video_paths:
        raise RuntimeError(
            "No videos found. Either put .mp4 files in assets/videos, or set PIXABAY_API_KEY / PEXELS_API_KEY in .env."
        )

    # 4) Build video — unseeded RNG so the clip order/offsets differ from every other job.
    background = build_background(
        video_paths,
        work_dir,
        total_duration=duration,
        width=width,
        height=height,
        fps=fps,
        clip_seconds=clip_seconds,
    )

    music_path = resolve_path(root, music_cfg.get("path")) if music_cfg.get("path") else None
    if music_path and music_path.exists():
        credits.append("Music: local background track. Add exact source/artist/license in description before publishing.")
    else:
        music_path = None

    final_mp4 = job_dir / "final_video.mp4"
    build_final_video(
        background,
        narration_wav,
        ass_path,
        final_mp4,
        duration=duration,
        music_path=music_path,
        music_volume=float(music_cfg.get("volume", 0.11)),
    )

    thumb = job_dir / "thumbnail.jpg"
    make_thumbnail(final_mp4, thumb, title=pkg.title, location_label=location_label, width=width, height=height)

    disclosure = "Voiceover generated with local/open TTS; script is original; visuals are own/licensed stock."
    description = build_description(pkg, location_label, credits, disclosure=disclosure)
    metadata = {
        "title": pkg.title[:100],
        "description": description,
        "tags": [h.lstrip("#") for h in pkg.hashtags],
        "categoryId": str(meta_cfg.get("category_id", "22")),
        "madeForKids": bool(meta_cfg.get("made_for_kids", False)),
        "containsSyntheticMedia": bool(meta_cfg.get("contains_synthetic_media", False)),
        "video_file": str(final_mp4),
        "thumbnail_file": str(thumb),
        "srt_file": str(srt_path),
        "duration_seconds": duration,
        "location_label": location_label,
        "credits": credits,
    }
    (job_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "description.txt").write_text(description, encoding="utf-8")

    print("\nDONE")
    print(f"Video: {final_mp4}")
    print(f"Thumbnail: {thumb}")
    print(f"Metadata: {job_dir / 'metadata.json'}")
    print("Review manually before upload. Do not publish unreviewed mass-produced content.")

    upload_cfg = cfg.get("upload", {})
    if upload_cfg.get("enabled"):
        from uploader import upload_to_all_channels

        youtube_video_ids = upload_to_all_channels(job_dir / "metadata.json", cfg)
        metadata["youtube_video_ids"] = youtube_video_ids
        (job_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    # Instagram publishing needs the rendered video pushed to the public repo first (the Graph
    # API only fetches video from a public HTTPS URL), so it can't happen inline here — the
    # GitHub Actions workflow does: generate -> git push -> instagram_uploader.py as separate
    # steps. Surface the paths it needs via $GITHUB_OUTPUT when running in that environment.
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"job_dir={job_dir}\n")
            f.write(f"metadata_json={job_dir / 'metadata.json'}\n")
            f.write(f"video_file={final_mp4}\n")


if __name__ == "__main__":
    main()

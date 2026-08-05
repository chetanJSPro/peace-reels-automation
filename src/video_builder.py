from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from utils import ensure_dir, ffprobe_duration, run


def _ass_filter_path(path: str | Path) -> str:
    # FFmpeg filter escaping. Wrap in single quotes so the drive-letter colon
    # (e.g. C:) isn't parsed as a filter option separator on Windows.
    p = str(Path(path).resolve()).replace("\\", "/")
    p = p.replace(":", r"\:").replace("'", r"\'")
    return f"'{p}'"


def normalize_clip(
    src: str | Path,
    out: str | Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: int = 30,
    offset: float = 0.0,
) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps},"
        "eq=contrast=1.04:saturation=1.08,format=yuv420p"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{offset:.2f}",
        "-i",
        str(src),
        "-t",
        f"{duration:.2f}",
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        str(out),
    ]
    run(cmd)
    return out


def build_background(
    video_paths: list[str | Path],
    workdir: str | Path,
    *,
    total_duration: float,
    width: int,
    height: int,
    fps: int = 30,
    clip_seconds: float = 5.0,
    seed: int | None = None,
) -> Path:
    if not video_paths:
        raise ValueError("No source videos available. Add local .mp4 files or set PIXABAY_API_KEY/PEXELS_API_KEY.")
    work = ensure_dir(workdir)
    normalized: list[Path] = []
    needed = math.ceil(total_duration / clip_seconds) + 1
    # A fresh, unseeded (or per-job-seeded) RNG so every generated video draws a different
    # shuffle order and different in-clip offsets. A fixed seed here previously made every
    # single render reuse the exact same clip sequence, regardless of topic/config.
    rng = random.Random(seed)
    pool = list(video_paths)
    rng.shuffle(pool)
    # Cycle through a shuffled pool instead of always starting at video_paths[0], reshuffling
    # each time the pool is exhausted so long videos don't just repeat the same order twice.
    sequence: list[Path] = []
    while len(sequence) < needed:
        rng.shuffle(pool)
        sequence.extend(Path(p) for p in pool)
    for i in range(needed):
        src = sequence[i]
        try:
            src_dur = ffprobe_duration(src)
        except Exception:
            src_dur = clip_seconds
        offset = 0.0
        if src_dur > clip_seconds + 1:
            offset = rng.uniform(0, max(0.0, src_dur - clip_seconds - 0.5))
        out = work / f"norm_{i:03d}.mp4"
        normalize_clip(src, out, duration=clip_seconds, width=width, height=height, fps=fps, offset=offset)
        normalized.append(out)

    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in normalized), encoding="utf-8")
    bg = work / "background.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(bg)])
    return bg


def build_final_video(
    background: str | Path,
    narration: str | Path,
    ass_file: str | Path,
    out_path: str | Path,
    *,
    duration: float,
    music_path: str | Path | None = None,
    music_volume: float = 0.10,
) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"ass={_ass_filter_path(ass_file)}"
    if music_path and Path(music_path).exists():
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(background),
            "-i",
            str(narration),
            "-stream_loop",
            "-1",
            "-i",
            str(music_path),
            "-filter_complex",
            f"[2:a]volume={music_volume},atrim=0:{duration:.2f},asetpts=N/SR/TB[m];"
            f"[1:a]volume=1.0[n];[n][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-vf",
            vf,
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-t",
            f"{duration:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(out),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(background),
            "-i",
            str(narration),
            "-vf",
            vf,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{duration:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(out),
        ]
    run(cmd)
    return out


def extract_frame(video_path: str | Path, out_png: str | Path, *, at_seconds: float = 1.0) -> Path:
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-ss", str(at_seconds), "-i", str(video_path), "-frames:v", "1", str(out)])
    return out


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "C:/Windows/Fonts/NirmalaB.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = w if not cur else cur + " " + w
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_thumbnail(
    video_path: str | Path,
    out_path: str | Path,
    *,
    title: str,
    location_label: str,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    work = Path(out_path).parent
    frame = work / "thumb_frame.png"
    extract_frame(video_path, frame, at_seconds=1.0)
    img = Image.open(frame).convert("RGB").resize((width, height))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # Dark bottom gradient
    for y in range(height):
        if y > height * 0.35:
            alpha = int(170 * ((y - height * 0.35) / (height * 0.65)))
            draw.line((0, y, width, y), fill=(0, 0, 0, min(190, alpha)))
    # Top pill
    top_font = _font(48)
    label = f"📍 {location_label}"
    bbox = draw.textbbox((0, 0), label, font=top_font)
    pill_w = bbox[2] - bbox[0] + 70
    pill_h = bbox[3] - bbox[1] + 34
    x = (width - pill_w) // 2
    y = 90
    draw.rounded_rectangle((x, y, x + pill_w, y + pill_h), radius=28, fill=(0, 0, 0, 150))
    draw.text((x + 35, y + 12), label, font=top_font, fill=(255, 255, 255, 255))

    title_font = _font(92)
    lines = _wrap(draw, title.split("#")[0].strip(), title_font, width - 140)[:3]
    total_h = len(lines) * 108
    y0 = height - 430 - total_h // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        tx = (width - (bbox[2] - bbox[0])) // 2
        # Stroke-like shadow
        for dx, dy in [(-4, 0), (4, 0), (0, -4), (0, 4), (3, 3)]:
            draw.text((tx + dx, y0 + dy), line, font=title_font, fill=(0, 0, 0, 220))
        draw.text((tx, y0), line, font=title_font, fill=(255, 255, 255, 255))
        y0 += 108

    final = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    final.save(out, quality=92)
    return out

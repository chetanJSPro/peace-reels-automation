from __future__ import annotations

import math
import random
from pathlib import Path

import librosa

from utils import ensure_dir, ffprobe_duration
from video_builder import run


def _grade_and_punch_clip(
    src: str | Path,
    out: str | Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: int = 30,
    offset: float = 0.0,
    zoom_amount: float = 0.09,
) -> Path:
    """Like video_builder.normalize_clip, but with the visual treatment that separates a real
    "edit" from a slideshow of raw stock clips: a slow punch-in zoom over the shot (the biggest,
    cheapest signal of intentional pacing -- static clips read as amateur regardless of cut
    timing) plus a cinematic contrast/saturation/vignette grade. Kept as a separate function from
    normalize_clip so Peace Reels' look is untouched."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = max(duration, 0.05)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,"
        f"scale=w='iw*(1+{zoom_amount}*t/{dur:.3f})':h='ih*(1+{zoom_amount}*t/{dur:.3f})':eval=frame,"
        f"crop={width}:{height},"
        "eq=contrast=1.12:saturation=1.28:brightness=0.01,"
        "colorbalance=rs=-0.04:bs=0.05:rm=0.02:bm=-0.02,"
        "vignette=PI/5,"
        f"fps={fps},format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-ss", f"{offset:.2f}", "-i", str(src), "-t", f"{duration:.2f}",
        "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out),
    ]
    run(cmd)
    return out


def _make_flash_clip(out: str | Path, *, width: int, height: int, fps: int) -> Path:
    """A ~2-frame white flash, pre-rendered once and reused, encoded with matching params so it
    concatenates cleanly via stream copy alongside the graded clips. Cut punctuation on strong
    beats -- the flash/whoosh hit is the other cheap, high-impact signal real edits use that a
    plain hard-cut timeline doesn't."""
    out = Path(out)
    frames = 2
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=white:s={width}x{height}:d={frames / fps:.3f}:r={fps}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out),
    ]
    run(cmd)
    return out


def detect_beats(audio_path: str | Path, *, start_offset: float = 0.0, duration: float | None = None) -> list[float]:
    """Return beat timestamps (seconds, relative to `start_offset`) for the audio starting at
    `start_offset`. Edit-style cuts land on these -- this is what makes the pacing feel like a
    real edit instead of clips changing on an arbitrary fixed timer."""
    y, sr = librosa.load(str(audio_path), sr=None, offset=start_offset, duration=duration)
    _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    return [float(t) for t in librosa.frames_to_time(beat_frames, sr=sr)]


def build_beat_synced_background(
    video_paths: list[str | Path],
    beats: list[float],
    workdir: str | Path,
    *,
    total_duration: float,
    width: int,
    height: int,
    fps: int = 30,
    cut_every_n_beats: int = 1,
    min_cut_seconds: float = 0.35,
    max_cut_seconds: float = 3.0,
    flash_every_n_cuts: int = 4,
    seed: int | None = None,
) -> Path:
    """Concatenate clips cut to the intervals between beats (every `cut_every_n_beats`-th beat,
    so e.g. 2 = a cut on every other beat for a slightly less frantic pace). Falls back to
    `min_cut_seconds` for any beat gap shorter than that, so double-detected beats don't produce
    unusably short/glitchy segments.

    `max_cut_seconds` caps how long any single segment can be, splitting it into equal
    sub-segments if needed -- sparse/weak beat detection (a quiet intro, an ambient stretch, or
    just a track with few strong onsets) can otherwise collapse into one giant multi-second gap,
    which defeats the point of an "edit" (one shot for the whole video) and, worse, can leave the
    final render shorter than requested if that one source clip is shorter than the gap."""
    if not video_paths:
        raise ValueError("No source videos available.")
    work = ensure_dir(workdir)
    rng = random.Random(seed)
    pool = list(video_paths)
    rng.shuffle(pool)

    cut_points = [0.0] + [b for i, b in enumerate(beats) if (i + 1) % cut_every_n_beats == 0]
    cut_points = [t for t in cut_points if t < total_duration] + [total_duration]
    segments = []
    for i in range(len(cut_points) - 1):
        gap = cut_points[i + 1] - cut_points[i]
        if gap < min_cut_seconds:
            continue
        n_parts = max(1, math.ceil(gap / max_cut_seconds))
        segments.extend([gap / n_parts] * n_parts)
    if not segments:
        n_parts = max(1, math.ceil(total_duration / max_cut_seconds))
        segments = [total_duration / n_parts] * n_parts

    flash_clip = None
    if flash_every_n_cuts and flash_every_n_cuts > 0:
        flash_clip = _make_flash_clip(work / "flash.mp4", width=width, height=height, fps=fps)

    normalized: list[Path] = []
    for i, seg_dur in enumerate(segments):
        if not pool:
            rng.shuffle(pool)
            pool = list(video_paths)
        src = pool.pop()
        try:
            src_dur = ffprobe_duration(src)
        except Exception:
            src_dur = seg_dur
        offset = 0.0
        if src_dur > seg_dur + 0.5:
            offset = rng.uniform(0, max(0.0, src_dur - seg_dur - 0.2))
        out = work / f"beat_{i:03d}.mp4"
        _grade_and_punch_clip(src, out, duration=seg_dur, width=width, height=height, fps=fps, offset=offset)
        normalized.append(out)
        is_last = i == len(segments) - 1
        if flash_clip and not is_last and (i + 1) % flash_every_n_cuts == 0:
            normalized.append(flash_clip)

    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in normalized), encoding="utf-8")
    bg = work / "background.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(bg)])
    return bg

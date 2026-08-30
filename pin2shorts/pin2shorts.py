#!/usr/bin/env python3
"""
pin2shorts.py — download public Pinterest video pins and render them into
YouTube-Shorts-ready vertical MP4s (1080x1920, <=59s, audio always present).

Public pins only. No login walls, no private boards, no DRM circumvention.
Respect takedowns: if an owner asks you to remove a clip, remove it.

Usage
-----
  python pin2shorts.py "https://www.pinterest.com/pin/1234567890/"
  python pin2shorts.py "https://pin.it/abc123" --mode blur --watermark "@bhagtivideotop"
  python pin2shorts.py --batch links.txt --mode crop --max-duration 45
  python pin2shorts.py --board "https://www.pinterest.com/user/board/" --limit 20
  python pin2shorts.py --list                      # show the manifest of everything downloaded

Outputs land in ./downloads (raw) and ./shorts (rendered), with manifest.csv
tracking every pin so you never download the same one twice.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    # Windows consoles default to cp1252; pin titles routinely carry emoji/Devanagari.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
RAW_DIR = BASE / "downloads"
OUT_DIR = BASE / "shorts"
MANIFEST = BASE / "manifest.csv"

TARGET_W, TARGET_H = 1080, 1920
TARGET_FPS = 30
MAX_DURATION = 59.0  # Shorts treats >60s as a regular video in some surfaces

FIELDS = [
    "downloaded_at", "pin_id", "url", "title", "uploader",
    "duration", "raw_file", "shorts_file", "mode", "status", "note",
]


# --------------------------------------------------------------------------
# ffmpeg discovery
# --------------------------------------------------------------------------
def _candidates() -> list[str]:
    cands: list[str] = []
    try:  # full static build, includes drawtext/freetype — best option
        from static_ffmpeg import run  # pip install static-ffmpeg
        cands.append(run.get_or_fetch_platform_executables_else_raise()[0])
    except Exception:
        pass
    exe = shutil.which("ffmpeg")  # system build
    if exe:
        cands.append(exe)
    try:
        import imageio_ffmpeg  # pip install imageio-ffmpeg (fallback, no drawtext)
        cands.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    return cands


def find_ffmpeg() -> str:
    for exe in _candidates():
        if exe and Path(exe).exists():
            return exe
    raise SystemExit(
        "ffmpeg not found. Install one of:\n"
        "  pip install static-ffmpeg      (full build, recommended)\n"
        "  sudo apt install ffmpeg        (Linux)\n"
        "  pip install imageio-ffmpeg     (light build, no text watermark)"
    )


FFMPEG = find_ffmpeg()


def has_filter(name: str) -> bool:
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    except Exception:
        return False
    return bool(re.search(rf"\b{name}\b", out))


def text_png(text: str, dst: Path, fontsize: int = 64, pad: int = 18) -> Path | None:
    """Render a watermark PNG with Pillow (fallback when ffmpeg lacks drawtext)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    font = None
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, fontsize)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()
    tmp = Image.new("RGBA", (8, 8))
    d0 = ImageDraw.Draw(tmp)
    box = d0.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 90))
    ImageDraw.Draw(img).text((pad - box[0], pad - box[1]), text, font=font, fill=(255, 255, 255, 190))
    img.save(dst)
    return dst


def probe(path: Path) -> dict:
    """Minimal probe by parsing ffmpeg stderr (no ffprobe binary required)."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, errors="replace",
    )
    out = proc.stderr
    info: dict = {"width": None, "height": None, "duration": None, "has_audio": False}
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    if m:
        h, mi, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)
    m = re.search(r"Stream #\d+:\d+.*?Video:.*? (\d{2,5})x(\d{2,5})", out)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    info["has_audio"] = bool(re.search(r"Stream #\d+:\d+.*?Audio:", out))
    return info


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------
def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_manifest(row: dict) -> None:
    new = not MANIFEST.exists()
    with MANIFEST.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def seen_ids() -> set[str]:
    return {r["pin_id"] for r in load_manifest() if r.get("pin_id")}


def content_hash(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------
@dataclass
class Clip:
    pin_id: str
    url: str
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    raw_file: str = ""
    shorts_file: str = ""
    mode: str = ""
    status: str = ""
    note: str = ""
    downloaded_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def _ydl_opts(outdir: Path, cookies: str | None, limit: int | None, log=print):
    def hook(d):
        if d.get("status") == "downloading":
            pct = (d.get("_percent_str") or "").strip()
            log(f"    downloading {pct}")
        elif d.get("status") == "finished":
            log("    download complete, merging")

    opts = {
        "outtmpl": str(outdir / "%(id)s.%(ext)s"),
        # Pinterest "story pin" videos (the AI-art/idea-pin format) split video and audio
        # into separate HLS streams with no combined format; "best[ext=mp4]/best" matches
        # nothing for those and errors out. bv*+ba lets yt-dlp merge them (falls back to
        # "best" for older single-stream video pins that don't split).
        "format": "bv*+ba/best",
        "merge_output_format": "mp4",
        "noplaylist": False,
        "writethumbnail": True,
        "writedescription": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "retries": 3,
        "concurrent_fragment_downloads": 4,
        "overwrites": False,
    }
    if cookies:
        opts["cookiefile"] = cookies
    if limit:
        opts["playlistend"] = limit
    return opts


def download(url: str, cookies: str | None = None, log=print) -> list[Clip]:
    """Download one pin or a whole board/collection. Returns a list of Clips."""
    import yt_dlp

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    opts = _ydl_opts(RAW_DIR, cookies, None, log)
    clips: list[Clip] = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        entries = info.get("entries") or [info]
        for e in entries:
            if not e:
                continue
            path = Path(ydl.prepare_filename(e))
            for cand in (path, path.with_suffix(".mp4")):
                if cand.exists():
                    path = cand
                    break
            clip = Clip(
                pin_id=str(e.get("id") or path.stem),
                url=e.get("webpage_url") or url,
                title=(e.get("title") or e.get("description") or "")[:200].replace("\n", " ").strip(),
                uploader=(e.get("uploader") or e.get("channel") or e.get("uploader_id") or "").strip(),
                duration=float(e.get("duration") or 0),
                raw_file=str(path) if path.exists() else "",
            )
            clips.append(clip)
    return clips


# --------------------------------------------------------------------------
# render to shorts
# --------------------------------------------------------------------------
def build_filter(mode: str, watermark: str | None, fade: bool, duration: float,
                 wm_as_image: bool = False) -> str:
    if mode == "crop":
        vf = f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,crop={TARGET_W}:{TARGET_H}"
    elif mode == "stretch":
        vf = f"scale={TARGET_W}:{TARGET_H}"
    else:  # blur (default) — keeps the whole frame, fills the rest with a blurred copy
        vf = (
            f"split=2[bg][fg];"
            f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},boxblur=25:6[bg];"
            f"[fg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    vf += f",fps={TARGET_FPS},format=yuv420p"
    if fade:
        fd = min(0.5, max(0.1, duration / 20))
        vf += f",fade=t=in:st=0:d={fd:.2f},fade=t=out:st={max(0, duration - fd):.2f}:d={fd:.2f}"
    if watermark and not wm_as_image:
        safe = watermark.replace(":", r"\:").replace("'", "").replace("%", "")
        vf += (
            f",drawtext=text='{safe}':fontcolor=white@0.65:fontsize=42:"
            f"x=(w-text_w)-48:y=h-text_h-120:box=1:boxcolor=black@0.35:boxborderw=14"
        )
    return vf


def render(clip: Clip, mode: str = "blur", max_duration: float = MAX_DURATION,
           watermark: str | None = None, fade: bool = True, log=print) -> Path | None:
    src = Path(clip.raw_file)
    if not src.exists():
        clip.status, clip.note = "failed", "raw file missing"
        return None

    info = probe(src)
    duration = info["duration"] or clip.duration or 0
    if duration <= 0:
        clip.status, clip.note = "failed", "could not read duration"
        return None
    keep = min(duration, float(max_duration))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUT_DIR / f"{clip.pin_id}_{mode}.mp4"

    # watermark: use drawtext when available, else composite a PNG we render with Pillow
    wm_png = None
    if watermark and not has_filter("drawtext"):
        wm_png = text_png(watermark, OUT_DIR / f".wm_{clip.pin_id}.png")
        if wm_png is None:
            log("  (no drawtext filter and no Pillow — skipping watermark)")

    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", "0", "-i", str(src), "-t", f"{keep:.2f}"]
    vf = build_filter(mode, watermark, fade, keep, wm_as_image=wm_png is not None)

    wm_idx = None
    if wm_png:
        cmd += ["-i", str(wm_png)]
        wm_idx = 1
        vf += f"[{wm_idx}:v]overlay=W-w-48:H-h-120"

    audio_idx = None
    if not info["has_audio"]:
        # Shorts expects an audio track; add silence so the file is never "muted by format".
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_idx = 2 if wm_idx == 1 else 1
        if fade:
            af = (f"[{audio_idx}:a]atrim=0:{keep:.2f},afade=t=in:st=0:d=0.2,"
                  f"afade=t=out:st={max(0, keep-0.4):.2f}:d=0.4[a]")
        else:
            af = f"[{audio_idx}:a]atrim=0:{keep:.2f}[a]"

    if wm_idx is not None or audio_idx is not None:
        fc = f"[0:v]{vf}[v]"
        if audio_idx is not None:
            fc += ";" + af
        cmd += ["-filter_complex", fc, "-map", "[v]"]
        if audio_idx is not None:
            cmd += ["-map", "[a]"]
        else:
            cmd += ["-map", "0:a?"]  # keep the source audio when we only composited a watermark
    else:
        cmd += ["-vf", vf]
        if fade:
            cmd += ["-af", f"afade=t=in:st=0:d=0.2,afade=t=out:st={max(0, keep-0.4):.2f}:d=0.4"]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M",
        "-r", str(TARGET_FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", "-shortest", str(dst),
    ]

    log(f"  rendering -> {dst.name}  ({keep:.1f}s, {TARGET_W}x{TARGET_H}, mode={mode})")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists():
        clip.status, clip.note = "failed", (proc.stderr or "")[-300:].replace("\n", " ")
        log(f"  FAILED: {clip.note}")
        return None

    clip.shorts_file, clip.mode, clip.status = str(dst), mode, "rendered"
    clip.note = f"{keep:.1f}s of {duration:.1f}s"
    return dst


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
def process(urls: Iterable[str], mode="blur", max_duration=MAX_DURATION,
            watermark=None, fade=True, cookies=None, limit=None, skip_seen=True,
            log=print) -> list[dict]:
    done = seen_ids() if skip_seen else set()
    rows: list[dict] = []
    for i, raw in enumerate(urls, 1):
        url = raw.strip()
        if not url or url.startswith("#"):
            continue
        log(f"[{i}] {url}")
        try:
            clips = download(url, cookies=cookies, log=log)
        except Exception as exc:
            err = str(exc).split("\n")[0][:250]
            log(f"  download error: {err}")
            m = re.search(r"/pin/(?:[^/]*?--)?(\d{6,})", url)
            append_manifest({"downloaded_at": datetime.now().isoformat(timespec="seconds"),
                             "pin_id": m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:10],
                             "url": url, "status": "failed", "note": err})
            rows.append({"url": url, "status": "failed", "note": err})
            continue

        for clip in clips[: limit or len(clips)]:
            if clip.pin_id in done:
                log(f"  already in manifest, skipping ({clip.pin_id})")
                rows.append({"url": url, "pin_id": clip.pin_id, "status": "skipped", "note": "duplicate"})
                continue
            if not clip.raw_file:
                clip.status, clip.note = "failed", "no media (image-only pin?)"
                log("  no video file — image-only pin or unsupported link")
            else:
                log(f"  pin {clip.pin_id} · {clip.title[:60] or '(no title)'}")
                render(clip, mode=mode, max_duration=max_duration, watermark=watermark,
                       fade=fade, log=log)
                done.add(clip.pin_id)
            row = asdict(clip)
            rows.append(row)
            append_manifest(row)
    return rows


def read_links(path: Path) -> list[str]:
    return [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


SELFTEST_PINS = [
    "https://www.pinterest.com/pin/1084663891475263837/",  # short public video pin (~15s)
    "https://www.pinterest.com/pin/664281013778109217/",   # fallback (~58s)
]


def selftest() -> int:
    """End-to-end check: download a public pin and render it. Verifies ffmpeg + network."""
    print(f"ffmpeg: {FFMPEG}")
    print(f"drawtext filter: {'yes' if has_filter('drawtext') else 'no (falls back to PNG watermark)'}")
    for url in SELFTEST_PINS:
        print(f"\ntrying {url}")
        try:
            rows = process([url], mode="blur", max_duration=15, watermark="@handle",
                           skip_seen=False, log=lambda m: print("  " + str(m)))
        except Exception as exc:
            print(f"  error: {str(exc)[:200]}")
            continue
        if any(r.get("status") == "rendered" for r in rows):
            print("\nSELFTEST OK — downloads and renders fine.")
            return 0
    print("\nSELFTEST FAILED — check network access to pinterest.com and that ffmpeg works.")
    print("If only the pin is dead, test with your own pin URL:")
    print('  python3 pin2shorts.py "https://www.pinterest.com/pin/<id>/"')
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Pinterest -> YouTube Shorts toolkit")
    p.add_argument("urls", nargs="*", help="pin / pin.it / board URLs")
    p.add_argument("--batch", type=Path, help="text file with one URL per line")
    p.add_argument("--board", help="board or collection URL (downloads every video pin on it)")
    p.add_argument("--limit", type=int, help="max clips to take from a board")
    p.add_argument("--mode", choices=["blur", "crop", "stretch"], default="blur",
                   help="how to fit non-vertical video: blurred backdrop (default), hard crop, stretch")
    p.add_argument("--max-duration", type=float, default=MAX_DURATION, help="trim to N seconds (default 59)")
    p.add_argument("--watermark", default=None, help="e.g. '@bhagtivideotop'")
    p.add_argument("--no-fade", action="store_true", help="disable fade in/out + audio fade")
    p.add_argument("--cookies", default=None, help="path to cookies.txt (only for your own logged-in boards)")
    p.add_argument("--redo", action="store_true", help="re-download even if the pin id is in the manifest")
    p.add_argument("--list", action="store_true", help="print the manifest and exit")
    p.add_argument("--selftest", action="store_true", help="download a known public pin and render it")
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.list:
        rows = load_manifest()
        if not rows:
            print("manifest is empty — nothing downloaded yet")
            return 0
        for r in rows:
            print(f"{r['downloaded_at']}  {r['status']:<9} {r['pin_id']:<22} {r.get('shorts_file') or r.get('raw_file','')}")
        return 0

    urls = list(args.urls)
    if args.batch:
        urls += read_links(args.batch)
    if args.board:
        urls.append(args.board)
    if not urls:
        p.print_help()
        return 1

    rows = process(urls, mode=args.mode, max_duration=args.max_duration,
                   watermark=args.watermark, fade=not args.no_fade,
                   cookies=args.cookies, limit=args.limit, skip_seen=not args.redo)

    ok = [r for r in rows if r.get("status") == "rendered"]
    print(f"\nDone. {len(ok)}/{len(rows)} rendered -> {OUT_DIR}")
    for r in ok:
        print("  ", r.get("shorts_file"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

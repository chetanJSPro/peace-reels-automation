from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def run(cmd: list[str], *, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    print("$", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")
    return proc


def ffprobe_duration(path: str | Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    proc = run(cmd)
    data = json.loads(proc.stdout)
    return float(data["format"]["duration"])


def has_command(name: str) -> bool:
    try:
        subprocess.run([name, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False


def slugify(text: str, max_len: int = 70) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s\-]+", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "video"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_path(root: Path, value: str | Path | None) -> Path | None:
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.is_absolute() else root / p


def list_media(folder: str | Path, exts: Iterable[str]) -> list[Path]:
    p = Path(folder)
    if not p.exists():
        return []
    exts = {e.lower() for e in exts}
    return sorted([x for x in p.rglob("*") if x.suffix.lower() in exts])


def dedupe_paths(paths: Iterable[str | Path]) -> list[Path]:
    """Drop duplicate files (e.g. a stock clip picked up by both the local-folder scan and a
    fresh API fetch of the same file) while preserving first-seen order."""
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        rp = Path(p).resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(Path(p))
    return out


def read_json(path: str | Path, default: Any) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def next_rotating_index(state_path: str | Path, count: int) -> int:
    """Advance a persisted round-robin index (used to auto-rotate script topics between
    unattended scheduled runs so they don't all reuse topic 0)."""
    if count <= 0:
        return 0
    state = read_json(state_path, {"last_topic_index": -1})
    nxt = (int(state.get("last_topic_index", -1)) + 1) % count
    write_json(state_path, {"last_topic_index": nxt})
    return nxt

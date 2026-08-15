from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta
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


# A blocklist alone doesn't scale (can't enumerate every wrong country) — two real published
# videos already slipped through with a narrow place-name query ("Lakshman Jhula bridge",
# "Himalaya clouds peaks") that isn't well-tagged on stock sites, so search fell back to loosely
# related results: one RISHIKESH-labeled video used the Golden Gate Bridge (San Francisco), one
# HIMALAYAN-labeled video used the Matterhorn (Switzerland). So this now *requires* positive
# evidence of Indian content in the tags, and additionally rejects known-wrong places as a
# backstop for the (rare) case of sparse/empty tags.
INDIA_ALLOWLIST = [
    "india", "indian", "bharat", "ganga", "ganges", "himalaya", "himalayan",
    "rishikesh", "varanasi", "kashi", "haridwar", "uttarakhand", "uttar pradesh",
    "ashram", "ghat", "sadhu", "yogi", "yoga", "hindu", "hinduism", "mumbai", "delhi",
    "kerala", "rajasthan", "punjab", "goa", "monsoon", "himachal",
]
FOREIGN_LOCATION_BLOCKLIST = [
    "usa", "america", "united states", "san francisco", "golden gate", "new york",
    "california", "texas", "england", "britain", "uk", "london", "bristol", "scotland",
    "wales", "ireland", "europe", "france", "paris", "germany", "italy", "spain",
    "switzerland", "alps", "matterhorn", "zermatt", "austria", "portugal", "greece",
    "greenland", "iceland", "norway", "sweden", "denmark", "netherlands",
    "canada", "australia", "china", "japan", "korea", "nepal", "bhutan", "tibet",
    "africa", "mexico", "russia", "brazil", "thailand", "vietnam", "indonesia",
    "philippines", "turkey", "egypt", "dubai", "uae",
]
# Clips that are genuinely "India" but not the calm nature/temple/river visual this channel is
# built on -- these were slipping through because the old filter only checked country, not
# theme (a "india, wildlife, safari" or "india, street food" clip mentions India and passed).
# That produced mismatched b-roll like Taj Mahal tourism shots, wildlife/safari clips, and
# cooking footage cut in under a peaceful meditation narration.
OFF_THEME_BLOCKLIST = [
    "wildlife", "safari", "tiger", "lion", "elephant", "zoo", "animal", "bird sanctuary",
    "cooking", "recipe", "kitchen", "restaurant", "street food", "food stall", "chicken",
    "curry", "spice market",
    "traffic", "highway", "car", "motorbike", "auto rickshaw", "railway station", "airport",
    "cricket", "bollywood", "wedding", "festival crowd", "nightclub", "party",
    "stock market", "office", "corporate", "factory", "construction",
    # Real, commonly-tagged Indian landmarks/scenes that aren't in data/locations.csv's mapped
    # set and don't fit the calm nature/temple/river visual.
    "taj mahal", "agra", "monument", "fort", "palace", "city skyline", "metro city",
]


def is_relevant_india_content(tags: str) -> bool:
    """True only when the candidate's own tags positively indicate Indian content (or provide
    no tags at all to judge by), don't mention a known-wrong country/landmark, and aren't an
    off-theme category (wildlife, food, traffic, etc.) that happens to also mention India. Use
    this when the source gives rich descriptive tags (e.g. Pixabay)."""
    t = (tags or "").lower()
    if any(bad in t for bad in FOREIGN_LOCATION_BLOCKLIST):
        return False
    if any(bad in t for bad in OFF_THEME_BLOCKLIST):
        return False
    if not t.strip():
        return True  # no metadata to judge by — can't penalize missing tags
    return any(good in t for good in INDIA_ALLOWLIST)


def mentions_foreign_place(text: str) -> bool:
    """Blocklist-only check for sources that only give sparse text (e.g. a Pexels page-URL
    slug) — too little text to fairly require positive India evidence, but still worth
    rejecting an obvious wrong-country match or an off-theme category (wildlife/food/traffic/
    etc.) that the URL slug happens to describe."""
    t = (text or "").lower()
    return any(bad in t for bad in FOREIGN_LOCATION_BLOCKLIST) or any(bad in t for bad in OFF_THEME_BLOCKLIST)


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


def ist_now_minutes() -> int:
    """Current minute-of-day in IST (UTC+5:30, no DST), without needing a system tz database —
    GitHub's ubuntu-latest runners have one, but a bare Windows Python often doesn't."""
    now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    return now.hour * 60 + now.minute


def recent_clip_ids(state_path: str | Path, source: str) -> set[str]:
    """IDs of stock clips downloaded in recent runs for `source` (e.g. "pixabay"/"pexels"),
    so a fresh run can steer away from them instead of re-picking the same top-ranked
    results from a small niche query pool every time (was the main cause of the same
    handful of clips appearing in nearly every generated video regardless of topic)."""
    state = read_json(state_path, {})
    return set(state.get("used_clip_ids", {}).get(source, []))


def record_clip_ids(state_path: str | Path, source: str, ids: Iterable[str], cap: int = 400) -> None:
    """Append newly-used clip IDs to the persisted history for `source`, most-recent last,
    capped to the last `cap` so old exclusions age out and a finite query pool can still be
    reused eventually rather than permanently blacklisted."""
    state = read_json(state_path, {})
    used = state.get("used_clip_ids") or {}
    lst = used.get(source, [])
    for i in ids:
        if i in lst:
            lst.remove(i)
        lst.append(i)
    used[source] = lst[-cap:]
    state["used_clip_ids"] = used
    write_json(state_path, state)


def next_rotating_index_for_key(state_path: str | Path, key: str, count: int) -> int:
    """Like next_rotating_index, but keeps an independent round-robin pointer per `key` (e.g. a
    time-of-day content slot) under state["slot_index"][key], so each slot's topic pool cycles
    on its own instead of sharing one global pointer with the other slots."""
    if count <= 0:
        return 0
    state = read_json(state_path, {})
    slots = state.get("slot_index") or {}
    nxt = (int(slots.get(key, -1)) + 1) % count
    slots[key] = nxt
    state["slot_index"] = slots
    write_json(state_path, state)
    return nxt

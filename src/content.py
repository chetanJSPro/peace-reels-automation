from __future__ import annotations

import csv
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class ScriptPackage:
    topic: str
    title: str
    hook: str
    lines: list[str]
    cta: str
    hashtags: list[str]

    @property
    def narration_text(self) -> str:
        # Line breaks split into per-line chunks; tts.py inserts real silence between them.
        return "\n".join([self.hook, *self.lines, self.cta])

    @property
    def subtitle_lines(self) -> list[str]:
        return [self.hook, *self.lines, self.cta]


def load_ideas(csv_path: str | Path) -> list[dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# Ties each script topic to real places in data/locations.csv, so the on-screen pin label and
# the stock-footage search queries actually match what the narration is talking about (this is
# how the reference channel does it: a Rishikesh script shows Rishikesh, not generic waves).
# Each topic maps to a *list* of 2-4 places instead of one fixed place: topic_visual_style()
# picks a different one at random per generation run, so the same topic doesn't pull the same
# footage/pin label every time it comes up in rotation (this was the main cause of "same clips,
# new text" — one topic, one place, forever).
TOPIC_LOCATION = {
    "rishikesh_sukoon": ["Rishikesh", "Ganga Ghat"],
    "kashi_ghat": ["Kashi", "Ganga Ghat"],
    "mountain_mind": ["Himalaya", "Ladakh", "Kedarnath"],
    "brahma_muhurta": ["Temple", "Rishikesh", "Pushkar"],
    "silence_peace": ["Forest", "Himalaya", "Ladakh"],
    "detachment": ["Ganga Ghat", "Kashi", "Forest"],
    "karma": ["Temple", "Forest", "Kashi"],
    "inner_home": ["Forest", "Ganga Ghat", "Rishikesh"],
    "gratitude": ["Temple", "Pushkar", "Amritsar"],
    "patience": ["Himalaya", "Forest", "Kedarnath"],
    "self_love": ["Rishikesh", "Forest", "Kerala Backwaters"],
    "present_moment": ["Ganga Ghat", "Coorg", "Forest"],
    "forgiveness": ["Kashi", "Ganga Ghat", "Temple"],
    "simplicity": ["Coorg", "Kerala Backwaters", "Forest"],
    "devotion": ["Amritsar", "Temple", "Pushkar", "Kashi"],
    "night_stillness": ["Kashi", "Ganga Ghat", "Himalaya"],
    "river_wisdom": ["Ganga Ghat", "Rishikesh", "Kashi"],
    "sacred_journey": ["Kedarnath", "Pushkar", "Himalaya", "Amritsar"],
    "inner_light": ["Temple", "Amritsar", "Kashi"],
    "ego_letting_go": ["Himalaya", "Ladakh", "Forest"],
    "solitude_strength": ["Ladakh", "Himalaya", "Kedarnath"],
    "change_acceptance": ["Coorg", "Kerala Backwaters", "Ganga Ghat"],
}


# Groups topics by which daily posting slot they fit best, so the scheduler can post the theme
# that matches *why* someone is opening Shorts at that moment instead of whatever the flat
# rotation lands on — a morning-intention topic at 07:15, a practical/detachment topic on the
# lunch scroll at 12:45, a deep wind-down topic in the strongest evening window at 20:15. This is
# the "viral timing" lever: matching content to the moment, not just picking a good posting time.
SLOT_TOPICS = {
    "morning": [
        "brahma_muhurta", "gratitude", "present_moment", "self_love", "patience",
        "devotion", "sacred_journey",
    ],
    "midday": [
        "karma", "detachment", "simplicity", "ego_letting_go", "solitude_strength",
        "change_acceptance", "forgiveness",
    ],
    "evening": [
        "silence_peace", "rishikesh_sukoon", "kashi_ghat", "mountain_mind", "inner_home",
        "river_wisdom", "inner_light", "night_stillness",
    ],
}


def _parse_hm(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def current_slot(time_slots: dict[str, dict[str, str]], now_ist_minutes: int) -> str | None:
    """Return the configured slot name (e.g. "morning") whose start/end window in
    config.yaml's content.time_slots contains the given minute-of-day (IST), or None if no
    window is configured/matches — callers should fall back to unfiltered rotation in that case.
    """
    for slot, window in (time_slots or {}).items():
        try:
            start, end = _parse_hm(window["start"]), _parse_hm(window["end"])
        except (KeyError, ValueError):
            continue
        if start <= end:
            if start <= now_ist_minutes < end:
                return slot
        elif now_ist_minutes >= start or now_ist_minutes < end:  # window wraps past midnight
            return slot
    return None


def topic_indices_for_slot(ideas: list[dict[str, str]], slot: str | None) -> list[int]:
    """Row indices in data/ideas.csv whose topic belongs to `slot`, interleaved round-robin
    across topics (rather than left in raw CSV order). data/ideas.csv is written in per-topic
    blocks, so raw order would give the slot's rotation pointer several picks of the same topic
    in a row before ever reaching the next one — the same "same content repeated" problem this
    slot system exists to avoid, just rediscovered inside a single slot. Falls back to every row
    (still interleaved) when the slot is unknown or none of its topics are present."""
    topics = SLOT_TOPICS.get(slot, []) if slot else []
    topic_filter = set(topics) if topics else None
    by_topic: dict[str, list[int]] = {}
    for i, row in enumerate(ideas):
        t = row.get("topic")
        if topic_filter is None or t in topic_filter:
            by_topic.setdefault(t, []).append(i)
    if not by_topic:
        return list(range(len(ideas)))
    order = [t for t in topics if t in by_topic] or sorted(by_topic)
    interleaved: list[int] = []
    while any(by_topic[t] for t in order):
        for t in order:
            if by_topic[t]:
                interleaved.append(by_topic[t].pop(0))
    return interleaved


def load_locations(csv_path: str | Path) -> dict[str, dict[str, str]]:
    p = Path(csv_path)
    if not p.exists():
        return {}
    with open(p, newline="", encoding="utf-8") as f:
        return {row["label"]: row for row in csv.DictReader(f)}


def topic_visual_style(
    topic: str,
    locations_csv: str | Path,
    *,
    fallback_label: str,
    fallback_pixabay_queries: list[str],
    fallback_pexels_queries: list[str],
) -> dict[str, Any]:
    """Return the location pin label + search queries for a topic's mapped place.

    Falls back to the config-wide defaults when the topic has no mapping or locations.csv
    is missing/empty, so this degrades gracefully instead of breaking generation.
    """
    place_keys = TOPIC_LOCATION.get(topic)
    place_key = random.choice(place_keys) if place_keys else None
    locations = load_locations(locations_csv)
    place = locations.get(place_key) if place_key else None
    if not place:
        return {
            "location_label": fallback_label,
            "pixabay_queries": fallback_pixabay_queries,
            "pexels_queries": fallback_pexels_queries,
        }
    terms = [t.strip() for t in (place.get("search_terms") or "").split(",") if t.strip()]
    return {
        "location_label": place.get("safe_top_label") or fallback_label,
        # Keep one generic fallback query alongside the place-specific ones for extra pool variety.
        "pixabay_queries": terms + fallback_pixabay_queries[-1:],
        "pexels_queries": [f"{t} India vertical" for t in terms] + fallback_pexels_queries[-1:],
    }


def choose_idea(csv_path: str | Path, topic_index: int = 0) -> dict[str, str]:
    ideas = load_ideas(csv_path)
    if not ideas:
        raise ValueError(f"No ideas found in {csv_path}")
    return ideas[topic_index % len(ideas)]


def package_from_idea(idea: dict[str, str], hashtags: list[str], lines_per_short: int = 5) -> ScriptPackage:
    lines = []
    for i in range(1, 8):
        v = (idea.get(f"line{i}") or "").strip()
        if v:
            lines.append(v)
    lines = lines[:lines_per_short]
    title_base = idea.get("title_base") or idea.get("hook") or idea.get("topic") or "Inner Peace"
    clean_hash = " ".join(hashtags[:4])
    title = f"{title_base} 🕊 {clean_hash}".strip()
    return ScriptPackage(
        topic=idea.get("topic", "inner_peace"),
        title=title,
        hook=idea.get("hook", title_base).strip(),
        lines=lines,
        cta=(idea.get("cta") or "Follow for daily inner peace.").strip(),
        hashtags=hashtags,
    )


def _ollama_generate(prompt: str, model: str, base_url: str) -> str:
    url = base_url.rstrip("/") + "/api/generate"
    r = requests.post(url, json={"model": model, "prompt": prompt, "stream": False}, timeout=120)
    r.raise_for_status()
    return r.json().get("response", "")


def package_with_ollama(idea: dict[str, str], hashtags: list[str], lines_per_short: int = 5) -> ScriptPackage:
    """Optional fully-free local LLM script generator via Ollama.

    Use this only if you have Ollama installed and a local model downloaded. The prompt explicitly
    requires original wording and avoids copying the researched channel.
    """
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    seed = package_from_idea(idea, hashtags, lines_per_short)
    prompt = f"""
Create an ORIGINAL Hinglish/Hindi YouTube Shorts meditation script.
Do NOT copy any known creator. Do NOT make medical, miracle, or guaranteed spiritual claims.
Style: calm, poetic, simple, Indian nature/travel vibe, 5 short lines plus a short CTA.
Topic: {seed.topic}
Hook idea: {seed.hook}
Return ONLY valid JSON with keys: title_base, hook, lines (array), cta.
Each line max 75 characters. Make it human and emotionally true.
""".strip()
    raw = _ollama_generate(prompt, model=model, base_url=base)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Ollama did not return JSON: {raw[:300]}")
    data = json.loads(raw[start : end + 1])
    idea2 = {
        "topic": seed.topic,
        "hook": data.get("hook", seed.hook),
        "title_base": data.get("title_base", seed.title.split("#")[0].strip()),
        "cta": data.get("cta", seed.cta),
    }
    for i, line in enumerate(data.get("lines", [])[:lines_per_short], 1):
        idea2[f"line{i}"] = str(line)
    return package_from_idea(idea2, hashtags, lines_per_short)


def build_description(pkg: ScriptPackage, location_label: str, credits: list[str], disclosure: str = "") -> str:
    tags = " ".join(pkg.hashtags)
    credit_block = "\n".join(f"- {c}" for c in credits) if credits else "- Own/licensed footage and audio."
    disclosure_block = f"\n\nDisclosure: {disclosure}" if disclosure else ""
    return f"""{pkg.title}

A short peaceful thought from {location_label}. Pause, breathe, and return to yourself.

{tags}

Credits / rights log:
{credit_block}{disclosure_block}

Note: This is wellness/spiritual reflection content, not medical advice.
""".strip()

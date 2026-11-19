from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from automations.itch.api import ItchAPIClient

logger = logging.getLogger(__name__)

# Target technical categories for game jams
JAM_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "ai": [
        "ai", "artificial intelligence", "simulation", "neural", "procedural",
        "procgen", "autonomous", "agents", "llm", "cellular automata", "genetic",
    ],
    "systems": [
        "systems", "engine", "architecture", "performance", "low-level", "concurrency",
        "distributed", "networking", "multiplayer", "deterministic", "retro", "assembly",
        "bare-metal", "operating system", "os", "kernel", "embedded", "physics",
    ],
    "tools": [
        "tool", "tools", "utility", "generator", "editor", "framework", "library", "sdk",
        "devkit", "pipeline", "debug", "audio tool",
    ],
    "python": [
        "python", "pygame", "arcade", "godot-python", "numpy", "panda3d", "renpy",
    ],
    "golang": [
        "go", "golang", "ebiten", "raylib-go", "pixel",
    ],
    "rust": [
        "rust", "bevy", "macroquad", "wgpu", "ggez", "pixels", "bracket-lib",
    ],
}


@dataclass(frozen=True)
class JamOpportunity:
    """Represents a discovered game jam matching technical radar criteria."""
    jam_id: str
    title: str
    url: str
    joined_count: int
    summary: str
    matched_keywords: list[str] = field(default_factory=list)
    score: int = 0
    is_ai: bool = False
    is_systems: bool = False
    is_tools: bool = False
    is_python: bool = False
    is_golang: bool = False
    is_rust: bool = False

    @property
    def tags_label(self) -> str:
        """Returns active categories as comma-separated labels."""
        labels = []
        if self.is_rust:
            labels.append("🦀 Rust")
        if self.is_golang:
            labels.append("🐹 Go")
        if self.is_python:
            labels.append("🐍 Python")
        if self.is_ai:
            labels.append("🧠 AI/Sim")
        if self.is_systems:
            labels.append("⚙️ Systems")
        if self.is_tools:
            labels.append("🛠️ Tools")
        return ", ".join(labels) or "General Systems"


def score_jam(jam_data: dict[str, Any]) -> Optional[JamOpportunity]:
    """
    Evaluates a raw jam dictionary against radar keywords.
    Returns JamOpportunity if matching criteria, else None.
    """
    jam_id = str(jam_data.get("id") or "").strip()
    title = str(jam_data.get("title") or "").strip()
    url = str(jam_data.get("url") or "").strip()
    joined_count = int(jam_data.get("joined_count") or 0)
    summary = str(jam_data.get("summary") or "").strip()

    searchable = f"{title} {summary} {url}".lower()

    matched_keywords: list[str] = []
    category_matches: dict[str, bool] = {
        "ai": False,
        "systems": False,
        "tools": False,
        "python": False,
        "golang": False,
        "rust": False,
    }

    score = 0

    # Match each category using word boundaries
    for category, keywords in JAM_CATEGORY_KEYWORDS.items():
        for kw in keywords:
            # Word boundary regex pattern to avoid false substring positives (e.g. "go" in "good")
            pattern = rf"\b{re.escape(kw)}\b" if len(kw) <= 4 else re.escape(kw)
            if re.search(pattern, searchable):
                if kw not in matched_keywords:
                    matched_keywords.append(kw)
                category_matches[category] = True

    # Check if any target category matched
    if not any(category_matches.values()):
        return None

    # Calculate weighted score
    if category_matches["rust"]:
        score += 35
    if category_matches["golang"]:
        score += 35
    if category_matches["python"]:
        score += 30
    if category_matches["ai"]:
        score += 30
    if category_matches["systems"]:
        score += 25
    if category_matches["tools"]:
        score += 25

    # Participant traction bonus (up to 20 points)
    if joined_count > 0:
        score += min(joined_count // 20, 20)

    # Multi-category synergy bonus (e.g. AI + Rust, or Tools + Go)
    active_cats = sum(1 for v in category_matches.values() if v)
    if active_cats > 1:
        score += active_cats * 10

    return JamOpportunity(
        jam_id=jam_id,
        title=title,
        url=url,
        joined_count=joined_count,
        summary=summary,
        matched_keywords=matched_keywords[:6],
        score=score,
        is_ai=category_matches["ai"],
        is_systems=category_matches["systems"],
        is_tools=category_matches["tools"],
        is_python=category_matches["python"],
        is_golang=category_matches["golang"],
        is_rust=category_matches["rust"],
    )


def scan_game_jams(
    client: ItchAPIClient,
    limit: int = 50,
    min_score: int = 15,
) -> list[JamOpportunity]:
    """
    Scans itch.io public jams and filters for AI, systems, tools, Python, Go, and Rust.
    Returns ranked list of matching opportunities sorted by score descending.
    """
    try:
        raw_jams = client.get_jams(limit=limit)
    except Exception as exc:
        logger.error("Error fetching jams for radar scanner: %s", exc)
        return []

    opportunities: list[JamOpportunity] = []
    for raw in raw_jams:
        opp = score_jam(raw)
        if opp and opp.score >= min_score:
            opportunities.append(opp)

    # Rank by score descending, then joined count
    opportunities.sort(key=lambda j: (j.score, j.joined_count), reverse=True)
    return opportunities


def filter_unseen_jams(
    jams: list[JamOpportunity],
    seen_ids: set[str],
) -> list[JamOpportunity]:
    """Filters out game jams that have already been notified."""
    return [j for j in jams if j.jam_id not in seen_ids]


def format_jam_card(jam: JamOpportunity) -> str:
    """Formats a single game jam opportunity into Telegram HTML."""
    kw_str = ", ".join(f"<code>{k}</code>" for k in jam.matched_keywords) if jam.matched_keywords else "N/A"
    joined_str = f"{jam.joined_count:,} joined" if jam.joined_count else "Freshly Announced"

    lines = [
        f"🎯 <b>[JAM RADAR MATCH]</b> <b><a href=\"{jam.url}\">{jam.title}</a></b>",
        f"• 🏷️ <b>Domain:</b> {jam.tags_label}",
        f"• 👥 <b>Participants:</b> <code>{joined_str}</code>",
        f"• 🔑 <b>Keywords:</b> {kw_str}",
        f"• ⭐ <b>Match Score:</b> <code>{jam.score} pts</code>",
    ]
    if jam.summary:
        clean_sum = jam.summary[:140] + "..." if len(jam.summary) > 140 else jam.summary
        lines.append(f"• ℹ️ <i>{clean_sum}</i>")

    return "\n".join(lines)


def format_jams_digest(jams: list[JamOpportunity], max_items: int = 3) -> str:
    """Formats top matched game jams into a consolidated block for digests."""
    if not jams:
        return "<i>No new AI/systems/tools game jams detected in this scan.</i>"

    cards = []
    for j in jams[:max_items]:
        cards.append(format_jam_card(j))

    return "\n\n".join(cards)

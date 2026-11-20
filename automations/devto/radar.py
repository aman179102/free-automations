from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from automations.devto.api import DevtoAPIClient

logger = logging.getLogger(__name__)

DEFAULT_RADAR_TAGS = ["python", "architecture", "webdev", "ai", "devops"]


@dataclass(frozen=True)
class DevtoRadarItem:
    """Represents a discovered article from the Dev.to Tech Trend Radar."""
    item_id: int
    title: str
    url: str
    reactions_count: int
    comments_count: int
    author_username: str
    tags: list[str] = field(default_factory=list)
    published_at: str = ""
    matched_tag: str = ""

    @property
    def tags_label(self) -> str:
        """Returns comma-separated tags string."""
        return ", ".join(f"#{t}" for t in self.tags) if self.tags else "General"


def parse_radar_item(raw_item: dict[str, Any], matched_tag: str = "") -> Optional[DevtoRadarItem]:
    """Parses raw Dev.to API article dictionary into DevtoRadarItem."""
    try:
        raw_id = raw_item.get("id")
        if raw_id is None:
            return None
        item_id = int(raw_id)
        title = str(raw_item.get("title") or "").strip()
        url = str(raw_item.get("url") or "").strip()
        if not title or not url:
            return None

        # Reactions (public or positive reactions)
        reactions = int(
            raw_item.get("public_reactions_count")
            or raw_item.get("positive_reactions_count")
            or 0
        )
        comments = int(raw_item.get("comments_count") or 0)

        user_obj = raw_item.get("user") or {}
        author_username = str(user_obj.get("username") or "unknown").strip()

        # Tags parsing (can be list or comma string)
        tags_raw = raw_item.get("tag_list") or raw_item.get("tags") or []
        if isinstance(tags_raw, str):
            tags = [t.strip().lstrip("#") for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = [str(t).strip().lstrip("#") for t in tags_raw if str(t).strip()]
        else:
            tags = []

        published_at = str(
            raw_item.get("published_at")
            or raw_item.get("published_timestamp")
            or ""
        )

        return DevtoRadarItem(
            item_id=item_id,
            title=title,
            url=url,
            reactions_count=reactions,
            comments_count=comments,
            author_username=author_username,
            tags=tags,
            published_at=published_at,
            matched_tag=matched_tag,
        )
    except Exception as exc:
        logger.warning("Error parsing Dev.to radar item: %s", exc)
        return None


def scan_trend_radar(
    client: DevtoAPIClient,
    tags: Optional[list[str]] = None,
    limit_per_tag: int = 10,
    state: Optional[str] = "rising",
) -> list[DevtoRadarItem]:
    """
    Scans Dev.to Tech Trend Radar across designated tags (#python, #architecture, #webdev, #ai, #devops).
    Ranks discovered items by reactions descending.
    """
    tags_to_scan = tags or DEFAULT_RADAR_TAGS
    discovered: list[DevtoRadarItem] = []
    seen_in_batch: set[int] = set()

    for tag in tags_to_scan:
        clean_tag = tag.strip().lstrip("#")
        try:
            logger.info("Scanning Dev.to rising radar for tag #%s...", clean_tag)
            raw_items = client.get_articles(
                tag=clean_tag,
                state=state,
                per_page=limit_per_tag,
            )
            for raw in raw_items:
                parsed = parse_radar_item(raw, matched_tag=clean_tag)
                if parsed and parsed.item_id not in seen_in_batch:
                    seen_in_batch.add(parsed.item_id)
                    discovered.append(parsed)
        except Exception as exc:
            logger.warning("Failed scanning Dev.to tag #%s: %s", clean_tag, exc)

    # Rank discovered articles by reactions count descending
    discovered.sort(key=lambda x: (x.reactions_count, x.comments_count), reverse=True)
    return discovered


def filter_unseen_items(
    items: list[DevtoRadarItem],
    seen_ids: set[int | str],
) -> list[DevtoRadarItem]:
    """Filters radar items to only those not present in seen_ids."""
    normalized_seen = {int(x) if str(x).isdigit() else x for x in seen_ids}
    return [it for it in items if it.item_id not in normalized_seen]


def format_radar_digest(items: list[DevtoRadarItem], limit: int = 5) -> str:
    """Formats discovered Dev.to radar highlights into an HTML message for Telegram."""
    if not items:
        return "🎯 <b>[DEV.TO TECH TREND RADAR]</b>\n<i>No new rising articles found.</i>"

    lines = [
        "🎯 <b>[DEV.TO TECH TREND RADAR HIGHLIGHTS]</b>",
        f"Scanning: <code>#python, #architecture, #webdev, #ai, #devops</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for it in items[:limit]:
        safe_title = html.escape(it.title)
        tags_str = html.escape(it.tags_label)
        lines.append(
            f"• 📝 <b><a href=\"{it.url}\">{safe_title}</a></b>\n"
            f"   ❤️ {it.reactions_count} reactions | 💬 {it.comments_count} comments | 👤 @{it.author_username}\n"
            f"   🏷️ <code>{tags_str}</code>"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

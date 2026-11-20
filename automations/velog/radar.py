from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from automations.velog.api import VelogAPIClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VelogRadarItem:
    """Represents a discovered article from the Velog Tech Trend Radar."""
    item_id: str
    title: str
    url: str
    url_slug: str
    likes: int
    comments_count: int
    author_username: str
    released_at: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def tags_label(self) -> str:
        """Returns comma-separated tags string."""
        return ", ".join(self.tags) if self.tags else "General"


def parse_radar_item(raw_item: dict[str, Any]) -> Optional[VelogRadarItem]:
    """Parses raw Velog GraphQL item response into VelogRadarItem."""
    try:
        item_id = str(raw_item.get("id") or "").strip()
        title = str(raw_item.get("title") or "").strip()
        url_slug = str(raw_item.get("url_slug") or "").strip()
        if not item_id or not title:
            return None

        likes = int(raw_item.get("likes") or 0)
        comments_count = int(raw_item.get("comments_count") or 0)

        user_obj = raw_item.get("user") or {}
        author_username = str(user_obj.get("username") or "unknown").strip()

        # Build canonical Velog URL
        if author_username and url_slug:
            url = f"https://velog.io/@{author_username}/{url_slug}"
        else:
            url = f"https://velog.io/@{author_username}"

        tags_raw = raw_item.get("tags") or []
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        released_at = str(raw_item.get("released_at") or "")

        return VelogRadarItem(
            item_id=item_id,
            title=title,
            url=url,
            url_slug=url_slug,
            likes=likes,
            comments_count=comments_count,
            author_username=author_username,
            released_at=released_at,
            tags=tags,
        )
    except Exception as exc:
        logger.warning("Error parsing Velog radar item: %s", exc)
        return None


def scan_trending_radar(
    client: VelogAPIClient,
    limit: int = 20,
    timeframe: str = "week",
) -> list[VelogRadarItem]:
    """
    Scans Velog Tech Trend Radar using trendingPosts(input: {limit: 20, timeframe: 'week'}).
    Ranks items descending by likes (하트).
    """
    try:
        logger.info("Scanning Velog Tech Trend Radar (limit=%d, timeframe='%s')...", limit, timeframe)
        raw_items = client.get_trending_posts(limit=limit, timeframe=timeframe)
        discovered: list[VelogRadarItem] = []
        seen_in_batch: set[str] = set()

        for raw in raw_items:
            parsed = parse_radar_item(raw)
            if parsed and parsed.item_id not in seen_in_batch:
                seen_in_batch.add(parsed.item_id)
                discovered.append(parsed)

        # Rank items by likes descending
        discovered.sort(key=lambda x: x.likes, reverse=True)
        return discovered
    except Exception as exc:
        logger.warning("Failed scanning Velog Tech Trend Radar: %s", exc)
        return []


def filter_unseen_items(
    items: list[VelogRadarItem],
    seen_ids: set[str],
) -> list[VelogRadarItem]:
    """Filters discovered items to only return those not yet seen in persistent state."""
    return [it for it in items if it.item_id not in seen_ids]


def format_radar_digest(items: list[VelogRadarItem], limit: int = 5) -> str:
    """Formats top trending Velog posts into an HTML message for Telegram."""
    if not items:
        return "🎯 <b>[Velog Tech Radar]</b> No trending articles detected."

    lines = [
        "🎯 <b>[Velog Tech Trend Radar]</b> Top Trending Articles:",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for idx, item in enumerate(items[:limit], 1):
        safe_title = html.escape(item.title)
        tags_str = f" #{item.tags[0]}" if item.tags else ""
        lines.append(
            f"{idx}. <b><a href=\"{item.url}\">{safe_title}</a></b>\n"
            f"   ❤️ <b>{item.likes}</b> likes | 💬 <b>{item.comments_count}</b> comments | 👤 @{item.author_username}{tags_str}"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

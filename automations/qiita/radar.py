from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from automations.qiita.api import QiitaAPIClient

logger = logging.getLogger(__name__)

DEFAULT_RADAR_TAGS = ["Python", "AI", "FastAPI", "Go", "Rust"]


@dataclass(frozen=True)
class QiitaRadarItem:
    """Represents a discovered article from Qiita tag radar."""
    item_id: str
    title: str
    url: str
    likes_count: int
    stocks_count: int
    views_count: int
    author_id: str
    author_name: str
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    matched_tag: str = ""

    @property
    def tags_label(self) -> str:
        """Returns comma-separated tags string."""
        return ", ".join(self.tags) if self.tags else "General"


def parse_radar_item(raw_item: dict[str, Any], matched_tag: str = "") -> Optional[QiitaRadarItem]:
    """Parses raw Qiita API item response into QiitaRadarItem."""
    try:
        item_id = str(raw_item.get("id") or "").strip()
        title = str(raw_item.get("title") or "").strip()
        url = str(raw_item.get("url") or "").strip()
        if not item_id or not title:
            return None

        likes_count = int(raw_item.get("likes_count") or 0)
        stocks_count = int(raw_item.get("stocks_count") or 0)
        views_raw = raw_item.get("page_views_count")
        views_count = int(views_raw) if views_raw is not None else 0

        user_obj = raw_item.get("user") or {}
        author_id = str(user_obj.get("id") or "unknown").strip()
        author_name = str(user_obj.get("name") or author_id).strip()

        tags_raw = raw_item.get("tags") or []
        tags = [str(t.get("name", "")).strip() for t in tags_raw if isinstance(t, dict) and t.get("name")]

        created_at = str(raw_item.get("created_at") or "")

        return QiitaRadarItem(
            item_id=item_id,
            title=title,
            url=url,
            likes_count=likes_count,
            stocks_count=stocks_count,
            views_count=views_count,
            author_id=author_id,
            author_name=author_name,
            tags=tags,
            created_at=created_at,
            matched_tag=matched_tag,
        )
    except Exception as exc:
        logger.warning("Error parsing Qiita radar item: %s", exc)
        return None


def scan_tag_radar(
    client: QiitaAPIClient,
    tags: Optional[list[str]] = None,
    limit_per_tag: int = 5,
) -> list[QiitaRadarItem]:
    """
    Scans Qiita articles under specified tags (default: #Python, #AI, #FastAPI, #Go, #Rust).
    Returns deduplicated list of discovered radar items.
    """
    tags_to_scan = tags or DEFAULT_RADAR_TAGS
    discovered: list[QiitaRadarItem] = []
    seen_in_batch: set[str] = set()

    for tag in tags_to_scan:
        try:
            logger.info("Scanning Qiita tag radar for #%s...", tag)
            raw_items = client.get_tag_items(tag, page=1, per_page=limit_per_tag)
            for raw in raw_items:
                parsed = parse_radar_item(raw, matched_tag=tag)
                if parsed and parsed.item_id not in seen_in_batch:
                    seen_in_batch.add(parsed.item_id)
                    discovered.append(parsed)
        except Exception as exc:
            logger.warning("Failed scanning Qiita tag #%s: %s", tag, exc)

    # Sort discovered items by likes_count (LGTM) descending
    discovered.sort(key=lambda x: (x.likes_count, x.stocks_count), reverse=True)
    return discovered


def filter_unseen_items(
    items: list[QiitaRadarItem],
    seen_ids: set[str] | list[str],
) -> list[QiitaRadarItem]:
    """
    Filters out articles that have already been tracked in state (seen_qiita_item_ids).
    """
    seen_set = set(seen_ids)
    return [item for item in items if item.item_id not in seen_set]


def format_radar_digest(items: list[QiitaRadarItem], max_items: int = 5) -> str:
    """
    Formats discovered radar items into clean HTML summary for Telegram.
    """
    if not items:
        return "🔍 <b>Qiita Tech Radar:</b> <i>No new trending articles discovered.</i>"

    lines = [
        f"🔍 <b>Qiita Tech Radar: Top Trending Articles ({len(items[:max_items])})</b>\n",
    ]
    for idx, item in enumerate(items[:max_items], 1):
        safe_title = html.escape(item.title)
        lines.append(
            f"{idx}. <b><a href=\"{item.url}\">{safe_title}</a></b>\n"
            f"   👍 <b>LGTM:</b> {item.likes_count} | 📑 <b>Stocks:</b> {item.stocks_count} | 🏷️ #{item.matched_tag}\n"
            f"   👤 <i>@{item.author_id} ({html.escape(item.author_name)})</i>"
        )

    return "\n\n".join(lines)

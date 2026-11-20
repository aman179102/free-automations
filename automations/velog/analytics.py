from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from automations.velog.api import VelogAPIClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VelogArticleMetrics:
    """Metrics for an individual published Velog article."""
    item_id: str
    title: str
    url: str
    url_slug: str
    likes: int
    comments_count: int
    views: int
    released_at: str = ""


@dataclass(frozen=True)
class VelogAnalyticsReport:
    """Consolidated creator analytics report across all author articles with deltas."""
    total_likes: int
    total_comments: int
    total_views: int
    posts_count: int
    delta_likes: int = 0
    delta_comments: int = 0
    delta_views: int = 0
    articles: list[VelogArticleMetrics] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Converts report to dictionary for state persistence."""
        return {
            "total_likes": self.total_likes,
            "total_comments": self.total_comments,
            "total_views": self.total_views,
            "posts_count": self.posts_count,
            "delta_likes": self.delta_likes,
            "delta_comments": self.delta_comments,
            "delta_views": self.delta_views,
            "timestamp": self.timestamp,
        }


def track_analytics(
    client: VelogAPIClient,
    username: Optional[str] = None,
    prior_state: Optional[dict[str, Any]] = None,
) -> VelogAnalyticsReport:
    """
    Fetches all author articles from Velog GraphQL API, aggregates creator metrics
    (Likes, Comments, Views), and computes deltas against prior saved state.
    """
    target_username = (username or client.username or "aman179102").strip()
    articles: list[VelogArticleMetrics] = []

    if client.is_configured():
        try:
            raw_items = client.get_posts(username=target_username, limit=50, temp_only=False)
            for raw in raw_items:
                item_id = str(raw.get("id") or "").strip()
                title = str(raw.get("title") or "").strip()
                url_slug = str(raw.get("url_slug") or "").strip()
                likes = int(raw.get("likes") or 0)
                comments_count = int(raw.get("comments_count") or 0)
                views = int(raw.get("views") or 0)
                released_at = str(raw.get("released_at") or "")

                url = f"https://velog.io/@{target_username}/{url_slug}" if url_slug else f"https://velog.io/@{target_username}"

                articles.append(VelogArticleMetrics(
                    item_id=item_id,
                    title=title,
                    url=url,
                    url_slug=url_slug,
                    likes=likes,
                    comments_count=comments_count,
                    views=views,
                    released_at=released_at,
                ))
        except Exception as exc:
            logger.warning("Failed fetching user articles for Velog analytics: %s", exc)

    total_likes = sum(a.likes for a in articles)
    total_comments = sum(a.comments_count for a in articles)
    total_views = sum(a.views for a in articles)
    posts_count = len(articles)

    # Compute deltas against prior saved state
    prev_analytics = (prior_state.get("analytics") if prior_state else None) or {}
    prev_likes = int(prev_analytics.get("total_likes", 0)) if "total_likes" in prev_analytics else total_likes
    prev_comments = int(prev_analytics.get("total_comments", 0)) if "total_comments" in prev_analytics else total_comments
    prev_views = int(prev_analytics.get("total_views", 0)) if "total_views" in prev_analytics else total_views

    delta_likes = max(0, total_likes - prev_likes)
    delta_comments = max(0, total_comments - prev_comments)
    delta_views = max(0, total_views - prev_views)

    now_iso = datetime.now(timezone.utc).isoformat()

    return VelogAnalyticsReport(
        total_likes=total_likes,
        total_comments=total_comments,
        total_views=total_views,
        posts_count=posts_count,
        delta_likes=delta_likes,
        delta_comments=delta_comments,
        delta_views=delta_views,
        articles=articles,
        timestamp=now_iso,
    )


def format_analytics_digest(report: VelogAnalyticsReport, username: str = "aman179102") -> str:
    """Formats creator analytics metrics into an HTML message for Telegram."""
    likes_delta = f" (+{report.delta_likes})" if report.delta_likes > 0 else ""
    comments_delta = f" (+{report.delta_comments})" if report.delta_comments > 0 else ""
    views_delta = f" (+{report.delta_views})" if report.delta_views > 0 else ""

    lines = [
        f"📊 <b>[Velog Creator Analytics]</b> @{username}:",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• 📝 <b>Total Posts:</b> <code>{report.posts_count}</code>",
        f"• ❤️ <b>Likes (하트):</b> <code>{report.total_likes:,}</code>{likes_delta}",
        f"• 💬 <b>Comments:</b> <code>{report.total_comments:,}</code>{comments_delta}",
        f"• 👁️ <b>Views:</b> <code>{report.total_views:,}</code>{views_delta}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if report.articles:
        lines.append("🔝 <b>Top Articles:</b>")
        sorted_articles = sorted(report.articles, key=lambda a: (a.likes, a.views), reverse=True)
        for art in sorted_articles[:3]:
            safe_title = html.escape(art.title)
            lines.append(
                f"• <b><a href=\"{art.url}\">{safe_title}</a></b>\n"
                f"  ❤️ {art.likes} likes | 💬 {art.comments_count} | 👁️ {art.views} views"
            )

    return "\n".join(lines)

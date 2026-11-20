from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from automations.devto.api import DevtoAPIClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DevtoArticleMetrics:
    """Metrics for an individual published Dev.to article."""
    item_id: int
    title: str
    url: str
    page_views: int
    reactions: int
    comments_count: int
    published_at: str = ""


@dataclass(frozen=True)
class DevtoAnalyticsReport:
    """Consolidated creator analytics report across all author articles with deltas."""
    total_views: int
    total_reactions: int
    total_comments: int
    posts_count: int
    delta_views: int = 0
    delta_reactions: int = 0
    delta_comments: int = 0
    articles: list[DevtoArticleMetrics] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Converts report to dictionary for state persistence."""
        return {
            "total_views": self.total_views,
            "total_reactions": self.total_reactions,
            "total_comments": self.total_comments,
            "posts_count": self.posts_count,
            "delta_views": self.delta_views,
            "delta_reactions": self.delta_reactions,
            "delta_comments": self.delta_comments,
            "timestamp": self.timestamp,
        }


def track_analytics(
    client: DevtoAPIClient,
    username: Optional[str] = None,
    prior_state: Optional[dict[str, Any]] = None,
) -> DevtoAnalyticsReport:
    """
    Fetches all author articles from GET /api/articles/me, aggregates creator metrics
    (Page Views, Reactions / Hearts, and Comments), and computes deltas against prior saved state.
    """
    articles: list[DevtoArticleMetrics] = []

    if client.is_configured():
        try:
            raw_items = client.get_my_articles(page=1, per_page=100)
            for raw in raw_items:
                raw_id = raw.get("id")
                if raw_id is None:
                    continue
                item_id = int(raw_id)
                title = str(raw.get("title") or "").strip()
                url = str(raw.get("url") or "").strip()

                views = int(raw.get("page_views_count") or 0)
                reactions = int(
                    raw.get("public_reactions_count")
                    or raw.get("positive_reactions_count")
                    or 0
                )
                comments = int(raw.get("comments_count") or 0)
                published_at = str(
                    raw.get("published_at")
                    or raw.get("published_timestamp")
                    or ""
                )

                articles.append(DevtoArticleMetrics(
                    item_id=item_id,
                    title=title,
                    url=url,
                    page_views=views,
                    reactions=reactions,
                    comments_count=comments,
                    published_at=published_at,
                ))
        except Exception as exc:
            logger.warning("Failed fetching author articles for Dev.to analytics: %s", exc)

    total_views = sum(a.page_views for a in articles)
    total_reactions = sum(a.reactions for a in articles)
    total_comments = sum(a.comments_count for a in articles)
    posts_count = len(articles)

    # Compute deltas against prior saved state
    prev_analytics = (prior_state.get("analytics") if prior_state else None) or {}
    prev_views = int(prev_analytics.get("total_views", 0)) if "total_views" in prev_analytics else total_views
    prev_reactions = int(prev_analytics.get("total_reactions", 0)) if "total_reactions" in prev_analytics else total_reactions
    prev_comments = int(prev_analytics.get("total_comments", 0)) if "total_comments" in prev_analytics else total_comments

    delta_views = max(0, total_views - prev_views)
    delta_reactions = max(0, total_reactions - prev_reactions)
    delta_comments = max(0, total_comments - prev_comments)

    now_iso = datetime.now(timezone.utc).isoformat()

    return DevtoAnalyticsReport(
        total_views=total_views,
        total_reactions=total_reactions,
        total_comments=total_comments,
        posts_count=posts_count,
        delta_views=delta_views,
        delta_reactions=delta_reactions,
        delta_comments=delta_comments,
        articles=articles,
        timestamp=now_iso,
    )


def format_analytics_digest(
    report: DevtoAnalyticsReport,
    username: str = "aman179102",
) -> str:
    """Formats Dev.to creator metrics report into an HTML Telegram digest."""
    views_delta = f" (+{report.delta_views})" if report.delta_views > 0 else ""
    reactions_delta = f" (+{report.delta_reactions})" if report.delta_reactions > 0 else ""
    comments_delta = f" (+{report.delta_comments})" if report.delta_comments > 0 else ""

    lines = [
        "📊 <b>[DEV.TO CREATOR METRICS]</b>",
        f"👤 <b>Author:</b> @{username}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• 📝 <b>Published Articles:</b> <code>{report.posts_count}</code>",
        f"• 👁️ <b>Page Views:</b> <code>{report.total_views:,}</code>{views_delta}",
        f"• ❤️ <b>Reactions:</b> <code>{report.total_reactions:,}</code>{reactions_delta}",
        f"• 💬 <b>Comments:</b> <code>{report.total_comments:,}</code>{comments_delta}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if report.articles:
        lines.append("🌟 <b>Top Performing Articles:</b>")
        top_articles = sorted(report.articles, key=lambda a: (a.reactions, a.page_views), reverse=True)[:3]
        for a in top_articles:
            safe_title = html.escape(a.title)
            lines.append(
                f"• <a href=\"{a.url}\">{safe_title}</a>\n"
                f"   👁️ {a.page_views} views | ❤️ {a.reactions} reactions | 💬 {a.comments_count} comments"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)

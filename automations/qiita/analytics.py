from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from automations.qiita.api import QiitaAPIClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QiitaArticleMetrics:
    """Metrics for an individual Qiita article."""
    item_id: str
    title: str
    url: str
    likes_count: int
    stocks_count: int
    views_count: int
    created_at: str = ""


@dataclass(frozen=True)
class QiitaAnalyticsReport:
    """Consolidated analytics report across all author articles with deltas."""
    total_likes: int
    total_stocks: int
    total_views: int
    items_count: int
    delta_likes: int = 0
    delta_stocks: int = 0
    delta_views: int = 0
    articles: list[QiitaArticleMetrics] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Converts report to dictionary for state persistence."""
        return {
            "total_likes": self.total_likes,
            "total_stocks": self.total_stocks,
            "total_views": self.total_views,
            "items_count": self.items_count,
            "delta_likes": self.delta_likes,
            "delta_stocks": self.delta_stocks,
            "delta_views": self.delta_views,
            "timestamp": self.timestamp,
        }


def track_analytics(
    client: QiitaAPIClient,
    prior_state: Optional[dict[str, Any]] = None,
) -> QiitaAnalyticsReport:
    """
    Fetches all authenticated user articles from Qiita API, aggregates metrics
    (LGTM likes, stocks, page views), and computes deltas against prior state.
    """
    articles: list[QiitaArticleMetrics] = []

    if client.is_configured():
        try:
            raw_items = client.get_authenticated_user_items(page=1, per_page=100)
            for raw in raw_items:
                item_id = str(raw.get("id") or "").strip()
                title = str(raw.get("title") or "").strip()
                url = str(raw.get("url") or "").strip()
                likes = int(raw.get("likes_count") or 0)
                stocks = int(raw.get("stocks_count") or 0)
                views_raw = raw.get("page_views_count")
                views = int(views_raw) if views_raw is not None else 0
                created_at = str(raw.get("created_at") or "")

                articles.append(QiitaArticleMetrics(
                    item_id=item_id,
                    title=title,
                    url=url,
                    likes_count=likes,
                    stocks_count=stocks,
                    views_count=views,
                    created_at=created_at,
                ))
        except Exception as exc:
            logger.warning("Failed fetching user articles for Qiita analytics: %s", exc)

    total_likes = sum(a.likes_count for a in articles)
    total_stocks = sum(a.stocks_count for a in articles)
    total_views = sum(a.views_count for a in articles)
    items_count = len(articles)

    # Compute deltas against prior saved state
    prev_analytics = (prior_state.get("analytics") if prior_state else None) or {}
    prev_likes = int(prev_analytics.get("total_likes", 0)) if "total_likes" in prev_analytics else total_likes
    prev_stocks = int(prev_analytics.get("total_stocks", 0)) if "total_stocks" in prev_analytics else total_stocks
    prev_views = int(prev_analytics.get("total_views", 0)) if "total_views" in prev_analytics else total_views

    delta_likes = max(0, total_likes - prev_likes)
    delta_stocks = max(0, total_stocks - prev_stocks)
    delta_views = max(0, total_views - prev_views)

    now_iso = datetime.now(timezone.utc).isoformat()

    return QiitaAnalyticsReport(
        total_likes=total_likes,
        total_stocks=total_stocks,
        total_views=total_views,
        items_count=items_count,
        delta_likes=delta_likes,
        delta_stocks=delta_stocks,
        delta_views=delta_views,
        articles=articles,
        timestamp=now_iso,
    )


def format_analytics_digest(report: QiitaAnalyticsReport) -> str:
    """
    Formats the analytics report into a clean HTML message for Telegram.
    """
    likes_delta_str = f" (+{report.delta_likes})" if report.delta_likes > 0 else ""
    stocks_delta_str = f" (+{report.delta_stocks})" if report.delta_stocks > 0 else ""
    views_delta_str = f" (+{report.delta_views})" if report.delta_views > 0 else ""

    lines = [
        "📊 <b>Qiita Creator Analytics & Growth</b>\n",
        f"• 📝 <b>Articles:</b> <code>{report.items_count}</code>",
        f"• 👍 <b>LGTM (Likes):</b> <code>{report.total_likes:,}</code>{likes_delta_str}",
        f"• 📑 <b>Stocks (Bookmarks):</b> <code>{report.total_stocks:,}</code>{stocks_delta_str}",
        f"• 👁️ <b>Page Views:</b> <code>{report.total_views:,}</code>{views_delta_str}",
    ]

    if report.articles:
        lines.append("\n<b>Top Articles:</b>")
        for a in report.articles[:3]:
            safe_title = html.escape(a.title)
            lines.append(
                f"• <a href=\"{a.url}\">{safe_title}</a> "
                f"(👍 {a.likes_count} | 📑 {a.stocks_count} | 👁️ {a.views_count})"
            )

    return "\n".join(lines)

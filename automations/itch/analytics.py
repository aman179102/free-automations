from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from automations.itch.api import ItchAPIClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameMetrics:
    """Performance metrics for an individual itch.io game or tool."""
    game_id: int | str
    title: str
    url: str
    views: int
    downloads: int
    purchases: int
    views_delta: int = 0
    downloads_delta: int = 0
    purchases_delta: int = 0
    published: bool = True


@dataclass(frozen=True)
class ItchAnalyticsReport:
    """Consolidated analytics report across all creator projects."""
    total_views: int
    total_downloads: int
    total_purchases: int
    delta_views: int
    delta_downloads: int
    delta_purchases: int
    games: list[GameMetrics]
    timestamp: str


def track_analytics(
    client: ItchAPIClient,
    state: dict[str, Any],
) -> tuple[ItchAnalyticsReport, dict[str, Any]]:
    """
    Fetches latest project performance from itch.io, compares against prior state,
    calculates deltas (views, downloads, purchases), and updates state.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    analytics_state: dict[str, Any] = state.setdefault("itch_analytics", {})
    prior_games: dict[str, Any] = analytics_state.get("games", {})

    games_data: list[dict[str, Any]] = []
    if client.is_configured():
        try:
            games_data = client.get_my_games()
        except Exception as exc:
            logger.warning("Failed to fetch games for analytics: %s", exc)
            games_data = []

    game_metrics: list[GameMetrics] = []
    total_views = 0
    total_downloads = 0
    total_purchases = 0
    total_delta_views = 0
    total_delta_downloads = 0
    total_delta_purchases = 0

    new_games_state: dict[str, Any] = {}

    for g in games_data:
        gid = str(g.get("id") or "")
        title = str(g.get("title") or f"Project #{gid}")
        url = str(g.get("url") or f"https://itch.io/game/{gid}")
        published = bool(g.get("published", True))

        views = int(g.get("views_count") or 0)
        downloads = int(g.get("downloads_count") or 0)
        purchases = int(g.get("purchases_count") or 0)

        prior = prior_games.get(gid, {})
        prior_views = int(prior.get("views") or 0)
        prior_downloads = int(prior.get("downloads") or 0)
        prior_purchases = int(prior.get("purchases") or 0)

        # Calculate positive deltas since previous run
        delta_v = max(0, views - prior_views) if prior_views > 0 else 0
        delta_d = max(0, downloads - prior_downloads) if prior_downloads > 0 else 0
        delta_p = max(0, purchases - prior_purchases) if prior_purchases > 0 else 0

        total_views += views
        total_downloads += downloads
        total_purchases += purchases
        total_delta_views += delta_v
        total_delta_downloads += delta_d
        total_delta_purchases += delta_p

        metric = GameMetrics(
            game_id=gid,
            title=title,
            url=url,
            views=views,
            downloads=downloads,
            purchases=purchases,
            views_delta=delta_v,
            downloads_delta=delta_d,
            purchases_delta=delta_p,
            published=published,
        )
        game_metrics.append(metric)

        new_games_state[gid] = {
            "title": title,
            "url": url,
            "views": views,
            "downloads": downloads,
            "purchases": purchases,
            "last_updated": now_iso,
        }

    # If no live games were returned but prior state exists, preserve prior totals
    if not games_data and prior_games:
        total_views = int(analytics_state.get("total_views", 0))
        total_downloads = int(analytics_state.get("total_downloads", 0))
        total_purchases = int(analytics_state.get("total_purchases", 0))
        new_games_state = prior_games

    # Update state persistence
    analytics_state["last_updated_at"] = now_iso
    analytics_state["total_views"] = total_views
    analytics_state["total_downloads"] = total_downloads
    analytics_state["total_purchases"] = total_purchases
    analytics_state["games"] = new_games_state
    state["itch_analytics"] = analytics_state

    report = ItchAnalyticsReport(
        total_views=total_views,
        total_downloads=total_downloads,
        total_purchases=total_purchases,
        delta_views=total_delta_views,
        delta_downloads=total_delta_downloads,
        delta_purchases=total_delta_purchases,
        games=game_metrics,
        timestamp=now_iso,
    )
    return report, state


def format_analytics_digest(report: ItchAnalyticsReport) -> str:
    """Formats an analytics report into clean Telegram HTML."""
    delta_v_str = f" (+{report.delta_views:,} new)" if report.delta_views > 0 else ""
    delta_d_str = f" (+{report.delta_downloads:,} new)" if report.delta_downloads > 0 else ""
    delta_p_str = f" (+{report.delta_purchases:,} new)" if report.delta_purchases > 0 else ""

    lines = [
        "📊 <b>[PROJECT ANALYTICS]</b>",
        f"• 👁️ <b>Total Views:</b> <code>{report.total_views:,}</code>{delta_v_str}",
        f"• 💾 <b>Total Downloads:</b> <code>{report.total_downloads:,}</code>{delta_d_str}",
        f"• 💰 <b>Total Purchases:</b> <code>{report.total_purchases:,}</code>{delta_p_str}",
        f"• 📦 <b>Tracked Projects:</b> <code>{len(report.games)}</code>",
    ]

    # Show top 3 individual project breakdown if available
    if report.games:
        lines.append("• 🏆 <b>Top Projects:</b>")
        sorted_games = sorted(report.games, key=lambda g: (g.downloads, g.views), reverse=True)
        for g in sorted_games[:3]:
            lines.append(
                f"   - <a href=\"{g.url}\">{g.title}</a>: "
                f"<code>{g.views:,} views</code>, <code>{g.downloads:,} downloads</code>"
            )

    return "\n".join(lines)

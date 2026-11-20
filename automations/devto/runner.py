from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from automations.devto.analytics import (
    DevtoAnalyticsReport,
    format_analytics_digest,
    track_analytics,
)
from automations.devto.api import DevtoAPIClient
from automations.devto.articles import (
    DevtoArticleCandidate,
    generate_devto_article,
    validate_article_content,
)
from automations.devto.publisher import DevtoPublisher
from automations.devto.radar import (
    DevtoRadarItem,
    filter_unseen_items,
    format_radar_digest,
    scan_trend_radar,
)
from common.config import Settings
from common.state import load_state, record_action, save_state

try:
    from common.telegram import send_telegram_message
except ImportError:
    send_telegram_message = None  # type: ignore

logger = logging.getLogger(__name__)

# Minimum cooldown interval between published articles: 24 hours (86,400 seconds)
MIN_ARTICLE_COOLDOWN_SECONDS = 86400.0


class DevtoRunner:
    """
    Autonomous Dev.to Engine Orchestrator.
    Manages:
      - 24-hour publication cooldown interval
      - Dev.to Tech Trend Radar scanning (#python, #architecture, #webdev, #ai, #devops)
      - Page Views, Reactions, and Comments creator analytics tracking with deltas
      - Persistent state tracking in state/devto_state.json
      - Single clean consolidated Telegram digest (zero-spam guarantee)
    """

    def __init__(
        self,
        settings: Settings,
        publisher: Optional[DevtoPublisher] = None,
        client: Optional[DevtoAPIClient] = None,
    ):
        self.settings = settings
        self.client = client or DevtoAPIClient(settings)
        self.publisher = publisher or DevtoPublisher(settings, client=self.client)

    def _get_state_file(self) -> Path:
        """Determines location for state/devto_state.json."""
        raw_dir = getattr(self.settings, "state_dir", "state") if self.settings else "state"
        p = Path(raw_dir)
        if p.is_dir() or not p.suffix:
            return p / "devto_state.json"
        return p

    def _load_state(self) -> dict[str, Any]:
        return load_state(self._get_state_file())

    def _save_state(self, state: dict[str, Any]) -> None:
        save_state(self._get_state_file(), state)

    def _dispatch_telegram(self, text: str) -> bool:
        """Dispatches an HTML message to Telegram if configured."""
        if send_telegram_message and getattr(self.settings, "has_telegram", False):
            try:
                send_telegram_message(self.settings, text)
                return True
            except Exception as exc:
                logger.error("Failed sending Telegram message for Dev.to action: %s", exc)
        return False

    def run_article(
        self,
        force: bool = False,
        dry_run: bool = False,
        category: str = "random",
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Executes article generation and publication cycle with strict 24-hour cooldown enforcement.
        """
        state = self._load_state()
        now_ts = time.time()
        last_pub_ts = float(state.get("last_published_ts", 0.0))
        elapsed = now_ts - last_pub_ts

        # 1. 24-Hour Cooldown Enforcement
        if not force and last_pub_ts > 0.0 and elapsed < MIN_ARTICLE_COOLDOWN_SECONDS:
            remaining_hours = round((MIN_ARTICLE_COOLDOWN_SECONDS - elapsed) / 3600.0, 1)
            msg = (
                f"Dev.to publication skipped: 24h cooldown active. "
                f"{remaining_hours}h remaining until next allowed publication."
            )
            logger.info(msg)
            return {
                "status": "cooldown",
                "message": msg,
                "remaining_hours": remaining_hours,
            }

        # 2. Generate deep-dive technical article candidate
        logger.info("Generating Dev.to technical article candidate (category='%s')...", category)
        candidate = generate_devto_article(self.settings, category=category)

        # 3. Publish to Dev.to
        pub_res = self.publisher.publish_article(
            candidate,
            published=True,
            dry_run=dry_run,
        )

        # 4. Record state on success
        if pub_res.get("status") == "published" and not dry_run:
            now_iso = datetime.now(timezone.utc).isoformat()
            state["last_published_at"] = now_iso
            state["last_published_ts"] = now_ts

            published_list = state.get("published_posts", [])
            published_list.append({
                "id": pub_res.get("id"),
                "title": candidate.title,
                "url": pub_res.get("url"),
                "slug": pub_res.get("slug"),
                "category": candidate.category,
                "published_at": now_iso,
            })
            state["published_posts"] = published_list[-50:]  # Retain latest 50
            record_action(state, "devto_publish", {"url": pub_res.get("url")})
            self._save_state(state)

            if notify_telegram:
                post_url = pub_res.get("url", "")
                tags_str = ", ".join(f"#{t}" for t in candidate.tags)
                msg = (
                    f"🚀 <b>[DEV.TO ARTICLE PUBLISHED]</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📝 <b>Title:</b> <a href=\"{post_url}\">{html.escape(candidate.title)}</a>\n"
                    f"🏷️ <b>Tags:</b> <code>{tags_str}</code>\n"
                    f"👤 <b>Author:</b> @{self.client.username}\n"
                    f"⏱️ <b>Review Jitter:</b> {pub_res.get('jitter', 0)}s\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                self._dispatch_telegram(msg)

        return pub_res

    def run_radar(
        self,
        dry_run: bool = False,
        notify_telegram: bool = True,
        limit_per_tag: int = 10,
    ) -> dict[str, Any]:
        """
        Scans Dev.to Tech Trend Radar, deduplicates against seen IDs, and updates state.
        """
        state = self._load_state()
        seen_ids = set(state.get("seen_devto_article_ids", []))

        items = scan_trend_radar(self.client, limit_per_tag=limit_per_tag, state="rising")
        fresh_items = filter_unseen_items(items, seen_ids)

        logger.info(
            "Dev.to Radar scan complete: %d rising found, %d fresh unseen.",
            len(items),
            len(fresh_items),
        )

        # Update persistent seen IDs
        if fresh_items and not dry_run:
            new_seen = seen_ids.union({it.item_id for it in fresh_items})
            state["seen_devto_article_ids"] = list(new_seen)[-500:]  # Retain last 500
            self._save_state(state)

        if notify_telegram and not dry_run and fresh_items:
            digest = format_radar_digest(fresh_items, limit=5)
            self._dispatch_telegram(digest)

        return {
            "status": "success",
            "total_scanned": len(items),
            "fresh_count": len(fresh_items),
            "fresh_items": fresh_items,
            "all_scanned": items,
        }

    def run_analytics(
        self,
        dry_run: bool = False,
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Tracks Dev.to creator Page Views, Reactions, and Comments with delta computation against prior state.
        """
        state = self._load_state()
        report = track_analytics(self.client, username=self.client.username, prior_state=state)

        state["analytics"] = report.to_dict()
        self._save_state(state)

        if notify_telegram and not dry_run:
            digest = format_analytics_digest(report, username=self.client.username)
            self._dispatch_telegram(digest)

        return {
            "status": "success",
            "report": report,
        }

    def run_all(
        self,
        force: bool = False,
        dry_run: bool = False,
        category: str = "random",
    ) -> dict[str, Any]:
        """
        Orchestrates full autonomous Dev.to cycle:
          1. Creator analytics tracking (Page Views, Reactions, Comments)
          2. Tech Trend Radar scanning (rising articles across #python, #architecture, #webdev, #ai, #devops)
          3. Technical article generation & publication (24-hour cooldown enforcement)
          4. Single consolidated Telegram digest (zero spam guarantee: suppressed when no deltas/posts)
        """
        state = self._load_state()

        # Step 1: Analytics (silent)
        analytics_res = self.run_analytics(dry_run=dry_run, notify_telegram=False)
        report: Optional[DevtoAnalyticsReport] = analytics_res.get("report")

        # Step 2: Tech Trend Radar (silent)
        radar_res = self.run_radar(dry_run=dry_run, notify_telegram=False)
        fresh_radar: list[DevtoRadarItem] = radar_res.get("fresh_items", [])
        all_radar: list[DevtoRadarItem] = radar_res.get("all_scanned", [])

        # Step 3: Article Publication (silent)
        article_res = self.run_article(
            force=force,
            dry_run=dry_run,
            category=category,
            notify_telegram=False,
        )

        user_handle = self.client.username or "aman179102"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Zero-spam guarantee check:
        has_new_pub = article_res.get("status") == "published"
        has_analytics_deltas = report and (report.delta_views > 0 or report.delta_reactions > 0 or report.delta_comments > 0)
        has_fresh_radar = bool(fresh_radar)

        should_send_digest = force or dry_run or has_new_pub or has_analytics_deltas or has_fresh_radar

        if not should_send_digest:
            logger.info("Dev.to run_all: No new deltas or publications. Telegram digest suppressed (zero-spam guarantee).")
            return {
                "status": "success",
                "analytics": analytics_res,
                "radar": radar_res,
                "article": article_res,
                "digest_suppressed": True,
            }

        # Build single consolidated Telegram digest
        digest_lines = [
            "🟢 <b>[DEV.TO AUTONOMOUS ENGINE DIGEST]</b>",
            f"📅 <i>{now_str}</i> | 👤 <b>@{user_handle}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        # Section 1: Creator Analytics
        if report:
            views_delta = f" (+{report.delta_views})" if report.delta_views > 0 else ""
            reactions_delta = f" (+{report.delta_reactions})" if report.delta_reactions > 0 else ""
            comments_delta = f" (+{report.delta_comments})" if report.delta_comments > 0 else ""
            digest_lines.extend([
                "📊 <b>CREATOR METRICS:</b>",
                f"• 📝 <b>Articles:</b> <code>{report.posts_count}</code>",
                f"• 👁️ <b>Page Views:</b> <code>{report.total_views:,}</code>{views_delta}",
                f"• ❤️ <b>Reactions:</b> <code>{report.total_reactions:,}</code>{reactions_delta}",
                f"• 💬 <b>Comments:</b> <code>{report.total_comments:,}</code>{comments_delta}",
            ])
        else:
            digest_lines.extend([
                "📊 <b>CREATOR METRICS:</b>",
                "• <i>No active analytics data available.</i>",
            ])

        digest_lines.append("─────────────────────────────")

        # Section 2: Tech Trend Radar
        radar_highlights = fresh_radar if fresh_radar else (all_radar[:2] if force else [])
        if radar_highlights:
            digest_lines.append(f"🎯 <b>TECH TREND RADAR ({len(radar_highlights)}):</b>")
            for item in radar_highlights[:2]:
                safe_title = html.escape(item.title)
                digest_lines.append(
                    f"• 📝 <b><a href=\"{item.url}\">{safe_title}</a></b>\n"
                    f"   ❤️ {item.reactions_count} reactions | 💬 {item.comments_count} | 👤 @{item.author_username}"
                )
        else:
            digest_lines.append("🎯 <b>TECH TREND RADAR:</b> <i>No new rising articles since last scan.</i>")

        digest_lines.append("─────────────────────────────")

        # Section 3: Article Publication Status
        if article_res.get("status") == "published":
            pub_title = html.escape(article_res.get("title", ""))
            pub_url = article_res.get("url", "")
            digest_lines.extend([
                "🚀 <b>PUBLICATION STATUS:</b>",
                f"• ✅ <b>Published:</b> <a href=\"{pub_url}\">{pub_title}</a>",
                f"• ⏱️ Review Jitter: {article_res.get('jitter')}s",
            ])
        elif article_res.get("status") == "dry_run":
            dry_title = html.escape(article_res.get("title", ""))
            digest_lines.extend([
                "🚀 <b>PUBLICATION STATUS:</b>",
                f"• 🧪 <i>[Dry-Run] Simulated:</i> <b>{dry_title}</b>",
            ])
        elif article_res.get("status") == "cooldown":
            rem_h = article_res.get("remaining_hours", 0)
            digest_lines.extend([
                "🚀 <b>PUBLICATION STATUS:</b>",
                f"• ⏳ <i>24h Cooldown Active:</i> {rem_h}h remaining",
            ])
        else:
            err = html.escape(article_res.get("error", "Unknown status"))
            digest_lines.extend([
                "🚀 <b>PUBLICATION STATUS:</b>",
                f"• ⚠️ <i>Status:</i> {err}",
            ])

        digest_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        digest_html = "\n".join(digest_lines)

        self._dispatch_telegram(digest_html)

        return {
            "status": "success",
            "analytics": analytics_res,
            "radar": radar_res,
            "article": article_res,
            "digest_suppressed": False,
        }


# Module-level convenience functions matching platform conventions
def run_article(
    settings: Settings,
    force: bool = False,
    dry_run: bool = False,
    category: str = "random",
) -> dict[str, Any]:
    return DevtoRunner(settings).run_article(force=force, dry_run=dry_run, category=category)


def run_radar(
    settings: Settings,
    dry_run: bool = False,
) -> dict[str, Any]:
    return DevtoRunner(settings).run_radar(dry_run=dry_run)


def run_analytics(
    settings: Settings,
    dry_run: bool = False,
) -> dict[str, Any]:
    return DevtoRunner(settings).run_analytics(dry_run=dry_run)


def run_all(
    settings: Settings,
    force: bool = False,
    dry_run: bool = False,
    category: str = "random",
) -> dict[str, Any]:
    return DevtoRunner(settings).run_all(force=force, dry_run=dry_run, category=category)

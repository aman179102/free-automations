from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from automations.qiita.analytics import (
    QiitaAnalyticsReport,
    format_analytics_digest,
    track_analytics,
)
from automations.qiita.api import QiitaAPIClient
from automations.qiita.articles import (
    QiitaArticleCandidate,
    generate_qiita_article,
    validate_article_content,
)
from automations.qiita.publisher import QiitaPublisher
from automations.qiita.radar import (
    QiitaRadarItem,
    filter_unseen_items,
    scan_tag_radar,
)
from common.config import Settings
from common.state import load_state, record_action, save_state

try:
    from common.telegram import (
        build_inline_keyboard,
        send_telegram_message,
    )
except ImportError:
    build_inline_keyboard = None  # type: ignore
    send_telegram_message = None  # type: ignore

logger = logging.getLogger(__name__)

# Minimum cooldown interval between published articles: 24 hours (86,400 seconds)
MIN_ARTICLE_COOLDOWN_SECONDS = 86400.0


class QiitaRunner:
    """
    Autonomous Qiita Engine Orchestrator.
    Manages:
      - 24-hour cooldown intervals between published articles
      - Qiita tag radar scanning for #Python, #AI, #FastAPI, #Go, #Rust
      - LGTM, Stocks, and Views creator analytics tracking
      - Persistent state tracking in state/qiita_state.json
      - Single clean consolidated Telegram digest (zero spam guarantee)
    """

    def __init__(
        self,
        settings: Settings,
        publisher: Optional[QiitaPublisher] = None,
        client: Optional[QiitaAPIClient] = None,
    ):
        self.settings = settings
        self.client = client or QiitaAPIClient(settings)
        self.publisher = publisher or QiitaPublisher(settings, client=self.client)

    def _get_state_file(self) -> Path:
        """Determines location for state/qiita_state.json."""
        raw_dir = getattr(self.settings, "state_dir", "state") if self.settings else "state"
        p = Path(raw_dir)
        if p.is_dir() or not p.suffix:
            return p / "qiita_state.json"
        return p

    def _load_state(self) -> dict[str, Any]:
        return load_state(self._get_state_file())

    def _save_state(self, state: dict[str, Any]) -> None:
        save_state(self._get_state_file(), state)

    def _dispatch_telegram(self, text: str, reply_markup: dict[str, Any] | None = None) -> bool:
        """Dispatches an HTML message to Telegram if configured."""
        if send_telegram_message and getattr(self.settings, "has_telegram", False):
            try:
                send_telegram_message(self.settings, text, reply_markup=reply_markup)
                return True
            except Exception as exc:
                logger.error("Failed sending Telegram message for Qiita action: %s", exc)
        return False

    def run_article(
        self,
        force: bool = False,
        dry_run: bool = False,
        category: str = "random",
        private: bool = False,
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Executes article generation and publication cycle with strict 24-hour cooldown enforcement.
        """
        state = self._load_state()
        last_publish_ts = float(state.get("last_publish_ts", 0.0))
        now = time.time()
        elapsed = now - last_publish_ts

        if not force and last_publish_ts > 0 and elapsed < MIN_ARTICLE_COOLDOWN_SECONDS:
            rem_h = max(0.1, (MIN_ARTICLE_COOLDOWN_SECONDS - elapsed) / 3600.0)
            logger.info("Qiita article cooldown active. Next article ready in ~%.1f hours (24h cooldown).", rem_h)
            return {
                "status": "skipped",
                "message": f"Cooldown active. Next article ready in ~{rem_h:.1f}h.",
                "hours_remaining": round(rem_h, 1),
            }

        # Generate candidate
        candidate = generate_qiita_article(self.settings, category=category)
        is_valid, reason = validate_article_content(candidate)
        if not is_valid:
            logger.error("Generated article candidate failed validation: %s", reason)
            return {"status": "error", "error": f"Validation failed: {reason}"}

        # Publish via publisher engine
        pub_result = self.publisher.publish_article(candidate, private=private, dry_run=dry_run)

        if pub_result.get("status") == "success":
            state["last_publish_ts"] = now
            published_list = state.get("published_articles", [])
            published_list.append({
                "item_id": pub_result.get("item_id"),
                "title": candidate.title,
                "url": pub_result.get("url"),
                "category": candidate.category,
                "tags": candidate.tags,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            state["published_articles"] = published_list[-50:]
            record_action(state, "publish_article", {
                "item_id": pub_result.get("item_id"),
                "title": candidate.title,
                "url": pub_result.get("url"),
            })
            self._save_state(state)

            if notify_telegram and not dry_run:
                card_lines = [
                    "🚀 <b>[QIITA LIVE ARTICLE PUBLISHED]</b>\n",
                    f"📝 <b>Title:</b> <a href=\"{pub_result.get('url')}\">{html.escape(candidate.title)}</a>",
                    f"🏷️ <b>Tags:</b> <code>{html.escape(', '.join(candidate.tags))}</code>",
                    f"⚙️ <b>Category:</b> <code>{candidate.category}</code> (80% Tech Japanese + 0.1% Rule)",
                ]
                if candidate.ascii_diagram:
                    snippet = candidate.ascii_diagram.strip()[:240]
                    card_lines.append(f"\n<pre>{html.escape(snippet)}</pre>")

                reply_markup = None
                if build_inline_keyboard and pub_result.get("url"):
                    reply_markup = build_inline_keyboard([
                        [{"text": "📖 Read on Qiita", "url": pub_result["url"]}],
                        [{"text": "👤 Qiita Profile", "url": f"https://qiita.com/{self.client.username}"}],
                    ])
                self._dispatch_telegram("\n".join(card_lines), reply_markup=reply_markup)

        return {
            **pub_result,
            "candidate": candidate,
        }

    def run_radar(
        self,
        dry_run: bool = False,
        tags: Optional[list[str]] = None,
        limit_per_tag: int = 5,
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Executes Qiita Tech Radar scan across #Python, #AI, #FastAPI, #Go, #Rust
        with state deduplication (seen_qiita_item_ids).
        """
        state = self._load_state()
        seen_ids = set(state.get("seen_qiita_item_ids", []))

        scanned = scan_tag_radar(self.client, tags=tags, limit_per_tag=limit_per_tag)
        fresh_items = filter_unseen_items(scanned, seen_ids)

        # Update state with newly discovered IDs (capped at 500)
        new_seen = list(seen_ids.union({item.item_id for item in scanned}))[-500:]
        state["seen_qiita_item_ids"] = new_seen
        self._save_state(state)

        if notify_telegram and fresh_items and not dry_run:
            digest = format_radar_digest(fresh_items, max_items=5)
            self._dispatch_telegram(digest)

        return {
            "status": "success",
            "total_scanned": len(scanned),
            "fresh_count": len(fresh_items),
            "fresh_items": fresh_items,
            "all_scanned": scanned,
        }

    def run_analytics(
        self,
        dry_run: bool = False,
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Tracks Qiita LGTM, Stocks, and Views with delta computation against prior state.
        """
        state = self._load_state()
        report = track_analytics(self.client, prior_state=state)

        state["analytics"] = report.to_dict()
        self._save_state(state)

        if notify_telegram and not dry_run:
            digest = format_analytics_digest(report)
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
        Orchestrates full autonomous Qiita cycle:
          1. Analytics tracking (LGTM, Stocks, Views)
          2. Tag Radar scanning (#Python, #AI, #FastAPI, #Go, #Rust)
          3. Article generation & publication (24-hour cooldown enforcement)
          4. Single consolidated Telegram digest (zero spam guarantee)
        """
        state = self._load_state()

        # Step 1: Analytics (silent)
        analytics_res = self.run_analytics(dry_run=dry_run, notify_telegram=False)
        report: Optional[QiitaAnalyticsReport] = analytics_res.get("report")

        # Step 2: Tag Radar (silent)
        radar_res = self.run_radar(dry_run=dry_run, notify_telegram=False)
        fresh_radar: list[QiitaRadarItem] = radar_res.get("fresh_items", [])
        all_radar: list[QiitaRadarItem] = radar_res.get("all_scanned", [])

        # Step 3: Article Publication (silent)
        article_res = self.run_article(
            force=force,
            dry_run=dry_run,
            category=category,
            notify_telegram=False,
        )

        user_handle = self.client.username or "aman179102"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Build single consolidated Telegram digest
        digest_lines = [
            f"🟢 <b>[QIITA AUTONOMOUS ENGINE DIGEST]</b>",
            f"📅 <i>{now_str}</i> | 👤 <b>@{user_handle}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        # Section 1: Creator Analytics
        if report:
            likes_delta = f" (+{report.delta_likes})" if report.delta_likes > 0 else ""
            stocks_delta = f" (+{report.delta_stocks})" if report.delta_stocks > 0 else ""
            views_delta = f" (+{report.delta_views})" if report.delta_views > 0 else ""
            digest_lines.extend([
                "📊 <b>CREATOR METRICS:</b>",
                f"• 📝 <b>Articles:</b> <code>{report.items_count}</code>",
                f"• 👍 <b>LGTM:</b> <code>{report.total_likes:,}</code>{likes_delta}",
                f"• 📑 <b>Stocks:</b> <code>{report.total_stocks:,}</code>{stocks_delta}",
                f"• 👁️ <b>Views:</b> <code>{report.total_views:,}</code>{views_delta}",
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
            digest_lines.append(f"🎯 <b>TAG RADAR: TRENDING ({len(radar_highlights)}):</b>")
            for item in radar_highlights[:2]:
                safe_title = html.escape(item.title)
                digest_lines.append(
                    f"• 🏷️ <b><a href=\"{item.url}\">{safe_title}</a></b>\n"
                    f"   👍 {item.likes_count} LGTM | 📑 {item.stocks_count} Stocks | #{item.matched_tag}"
                )
        else:
            digest_lines.append("🎯 <b>TAG RADAR:</b> <i>No new articles since last scan.</i>")

        digest_lines.append("─────────────────────────────")

        # Section 3: Article Publication Status
        article_candidate = article_res.get("candidate")
        if article_res.get("status") == "skipped":
            rem_h = article_res.get("hours_remaining", 0)
            digest_lines.extend([
                "📝 <b>ARTICLE STATUS:</b>",
                f"• ⏳ <b>Cooldown Active:</b> Next article ready in <b>~{rem_h}h</b> (24h cooldown enforced).",
            ])
        elif article_candidate:
            pub_tag = "🚀 Published Live" if article_res.get("published") else "💡 Drafted / Generated"
            pub_url = article_res.get("url") or f"https://qiita.com/{user_handle}"
            digest_lines.extend([
                "📝 <b>ARTICLE STATUS:</b>",
                f"• 🛠️ <b>{pub_tag}:</b> <a href=\"{pub_url}\">{html.escape(article_candidate.title)}</a>",
                f"• 🏷️ <b>Tags:</b> <code>{html.escape(', '.join(article_candidate.tags))}</code> (80% Tech Japanese)",
            ])
            if article_candidate.ascii_diagram:
                preview = article_candidate.ascii_diagram.strip()[:240]
                digest_lines.append(f"<pre>{html.escape(preview)}</pre>")

        digest_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Buttons
        reply_markup = None
        buttons: list[dict[str, str]] = []
        if article_res.get("url"):
            buttons.append({"text": "📖 Read Article", "url": article_res["url"]})
        elif radar_highlights and radar_highlights[0].url:
            buttons.append({"text": f"Trending: {radar_highlights[0].title[:15]}...", "url": radar_highlights[0].url})
        buttons.append({"text": "Qiita Profile", "url": f"https://qiita.com/{user_handle}"})
        buttons.append({"text": "GitHub", "url": "https://github.com/aman179102"})

        if build_inline_keyboard:
            row1 = [buttons[0]] if len(buttons) >= 3 else []
            row2 = buttons[1:] if len(buttons) >= 3 else buttons
            keyboard_rows = [row1, row2] if row1 else [row2]
            reply_markup = build_inline_keyboard(keyboard_rows)

        consolidated_text = "\n".join(digest_lines)

        # Dispatch single consolidated message strictly when there is fresh activity or force=True
        has_new_activity = (
            (report and (report.delta_likes > 0 or report.delta_stocks > 0 or report.delta_views > 0))
            or bool(fresh_radar)
            or bool(article_res.get("published"))
            or force
        )

        dispatched = False
        if not dry_run and has_new_activity:
            self._dispatch_telegram(consolidated_text, reply_markup=reply_markup)
            dispatched = True
        else:
            logger.info(
                "Qiita run_all complete: no new deltas, items, or publications. Telegram dispatch suppressed (zero-spam guarantee)."
            )

        return {
            "status": "success",
            "analytics": analytics_res,
            "radar": radar_res,
            "article": article_res,
            "consolidated_digest": consolidated_text,
            "telegram_dispatched": dispatched,
        }


# Module-level convenience functions
def run_article(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return QiitaRunner(settings).run_article(**kwargs)


def run_radar(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return QiitaRunner(settings).run_radar(**kwargs)


def run_analytics(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return QiitaRunner(settings).run_analytics(**kwargs)


def run_all(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return QiitaRunner(settings).run_all(**kwargs)

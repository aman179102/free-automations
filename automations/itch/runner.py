from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from automations.itch.analytics import (
    ItchAnalyticsReport,
    format_analytics_digest,
    track_analytics,
)
from automations.itch.api import ItchAPIClient
from automations.itch.devlogs import (
    ItchDevlogCandidate,
    generate_itch_devlog,
    validate_devlog_content,
)
from automations.itch.publisher import ItchPublisher
from automations.itch.radar import (
    JamOpportunity,
    filter_unseen_jams,
    scan_game_jams,
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

# Minimum cooldown interval between devlogs: 2 days (172,800 seconds)
MIN_DEVLOG_COOLDOWN_SECONDS = 2 * 86400.0


class ItchRunner:
    """
    Autonomous itch.io Engine Orchestrator.
    Manages:
      - 2-day cooldown intervals between devlog publications
      - Game jam radar scanning for AI/systems/tools/Python/Go/Rust
      - Project views/downloads/purchases analytics tracking
      - Persistent state tracking in state/itch_state.json
      - Single clean consolidated Telegram digest (zero spam)
    """

    def __init__(
        self,
        settings: Settings,
        publisher: Optional[ItchPublisher] = None,
        client: Optional[ItchAPIClient] = None,
    ):
        self.settings = settings
        self.client = client or ItchAPIClient(settings)
        self.publisher = publisher or ItchPublisher(settings, client=self.client)

    def _get_state_file(self) -> Path:
        """Determines location for state/itch_state.json."""
        raw_dir = getattr(self.settings, "state_dir", "state") if self.settings else "state"
        p = Path(raw_dir)
        if p.is_dir() or not p.suffix:
            return p / "itch_state.json"
        return p

    def _load_state(self) -> dict[str, Any]:
        return load_state(self._get_state_file())

    def _save_state(self, state: dict[str, Any]) -> None:
        save_state(self._get_state_file(), state)

    def _dispatch_telegram(self, text: str, reply_markup: dict[str, Any] | None = None) -> bool:
        """Dispatches an HTML card to Telegram if configured."""
        if send_telegram_message and getattr(self.settings, "has_telegram", False):
            try:
                send_telegram_message(self.settings, text, reply_markup=reply_markup)
                return True
            except Exception as exc:
                logger.error("Failed to send Telegram message for itch.io action: %s", exc)
        return False

    def run_devlog(
        self,
        force: bool = False,
        dry_run: bool = False,
        category: str = "random",
        draft: bool = False,
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Executes devlog generation cycle with strict 2-day cooldown enforcement.
        """
        state = self._load_state()
        now = time.time()
        last_devlog_at = float(state.get("itch_last_devlog_at", 0) or 0)
        time_since_last = now - last_devlog_at

        # Check 2-day cooldown
        if not force and last_devlog_at > 0 and time_since_last < MIN_DEVLOG_COOLDOWN_SECONDS:
            rem_hours = (MIN_DEVLOG_COOLDOWN_SECONDS - time_since_last) / 3600.0
            logger.info("itch.io devlog cooldown active. Next devlog ready in ~%.1f hours.", rem_hours)
            return {
                "status": "skipped",
                "reason": f"cooldown_active_{rem_hours:.1f}h",
                "hours_remaining": round(rem_hours, 1),
            }

        candidate = generate_itch_devlog(self.settings, category=category)
        logger.info("Generated high-signal itch.io devlog: '%s'", candidate.title)

        is_auto = getattr(self.settings, "itch_auto_publish", False)
        published_live = False
        res: dict[str, Any] = {}

        if (is_auto or draft) and not dry_run and self.client.is_configured():
            res = self.publisher.publish_devlog(candidate, draft=draft, dry_run=False)
            if res.get("status") == "success":
                published_live = True
                state["itch_last_devlog_at"] = now
                state["itch_devlogs_count"] = int(state.get("itch_devlogs_count", 0)) + 1
                record_action(
                    state,
                    "devlog_published" if not draft else "devlog_drafted",
                    {"title": candidate.title, "url": str(res.get("url") or "")},
                )
                self._save_state(state)
        elif dry_run:
            res = self.publisher.publish_devlog(candidate, draft=draft, dry_run=True)
            state["itch_last_devlog_at"] = now
            state["itch_devlogs_count"] = int(state.get("itch_devlogs_count", 0)) + 1
            self._save_state(state)

        # Standalone Telegram dispatch if requested
        if notify_telegram and not dry_run:
            status_tag = "🚀 PUBLISHED LIVE" if published_live else ("📝 DRAFTED" if draft else "💡 GENERATED")
            lines = [
                f"👾 <b>[ITCH.IO DEVLOG {status_tag}]</b>",
                f"• 🛠️ <b>Title:</b> {html.escape(candidate.title)}",
                f"• 🏷️ <b>Category:</b> <code>{candidate.category}</code>",
                f"• 📊 <b>Words:</b> <code>{len(candidate.body.split())}</code> (0.1% rule verified)",
            ]
            if candidate.ascii_diagram:
                lines.append(f"<pre>{html.escape(candidate.ascii_diagram.strip()[:400])}</pre>")
            if res.get("url"):
                lines.append(f"🔗 <a href=\"{res['url']}\">Open on itch.io</a>")

            reply_markup = None
            if build_inline_keyboard and res.get("url"):
                reply_markup = build_inline_keyboard([[{"text": "Open on itch.io", "url": str(res["url"])}]])

            self._dispatch_telegram("\n".join(lines), reply_markup=reply_markup)

        return {
            "status": "success",
            "candidate": candidate,
            "published": published_live,
            "response": res,
        }

    def run_radar(
        self,
        limit: int = 50,
        min_score: int = 15,
        dry_run: bool = False,
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Scans game jams filtering for AI, systems, tools, Python, Go, and Rust.
        Deduplicates against seen jams in state.
        """
        state = self._load_state()
        seen_ids = set(state.get("seen_jam_ids", []))

        opportunities = scan_game_jams(self.client, limit=limit, min_score=min_score)
        fresh_jams = filter_unseen_jams(opportunities, seen_ids)

        logger.info(
            "Game jam radar scan complete: %d matched criteria, %d fresh unseen.",
            len(opportunities),
            len(fresh_jams),
        )

        now = time.time()
        state["itch_last_radar_at"] = now
        for j in fresh_jams:
            seen_ids.add(j.jam_id)
        state["seen_jam_ids"] = list(seen_ids)
        self._save_state(state)

        if notify_telegram and fresh_jams and not dry_run:
            top_jams = fresh_jams[:3]
            cards = []
            for j in top_jams:
                cards.append(
                    f"• 🎯 <b><a href=\"{j.url}\">{html.escape(j.title)}</a></b> "
                    f"({j.joined_count:,} joined)\n"
                    f"  🏷️ <i>{j.tags_label}</i> (Score: {j.score} pts)"
                )
            msg = (
                f"🎯 <b>[ITCH.IO GAME JAM RADAR: {len(fresh_jams)} NEW MATCHES]</b>\n"
                f"Filtered for AI, Systems, Tools, Go, Rust, Python:\n\n" + "\n\n".join(cards)
            )
            self._dispatch_telegram(msg)

        return {
            "status": "success",
            "total_matched": len(opportunities),
            "fresh_matched": len(fresh_jams),
            "fresh_jams": fresh_jams,
            "all_jams": opportunities,
        }

    def run_analytics(
        self,
        dry_run: bool = False,
        notify_telegram: bool = True,
    ) -> dict[str, Any]:
        """
        Tracks project views, downloads, and purchases.
        """
        state = self._load_state()
        report, updated_state = track_analytics(self.client, state)
        self._save_state(updated_state)

        logger.info(
            "Itch analytics updated: Views=%d (+%d), Downloads=%d (+%d), Purchases=%d (+%d)",
            report.total_views,
            report.delta_views,
            report.total_downloads,
            report.delta_downloads,
            report.total_purchases,
            report.delta_purchases,
        )

        if notify_telegram and not dry_run:
            msg = format_analytics_digest(report)
            self._dispatch_telegram(msg)

        return {
            "status": "success",
            "report": report,
        }

    def run_all(
        self,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Executes complete itch.io autonomous suite:
          1. Analytics tracking
          2. Jam radar scanning
          3. Devlog generation (with 2-day cooldown)
          4. Dispatches a SINGLE CONSOLIDATED Telegram digest (no spam)
        """
        logger.info("Executing comprehensive itch.io autonomous suite...")

        # 1. Analytics
        analytics_res = self.run_analytics(dry_run=dry_run, notify_telegram=False)
        report: ItchAnalyticsReport = analytics_res.get("report")

        # 2. Jam Radar
        radar_res = self.run_radar(limit=50, dry_run=dry_run, notify_telegram=False)
        fresh_jams: list[JamOpportunity] = radar_res.get("fresh_jams", [])
        all_jams: list[JamOpportunity] = radar_res.get("all_jams", [])

        # 3. Devlog cycle
        devlog_res = self.run_devlog(force=force, dry_run=dry_run, notify_telegram=False)

        # 4. Assemble Single Consolidated Digest (ZERO SPAM)
        user_handle = getattr(self.settings, "itch_username", "aman179102") or "aman179102"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        digest_lines = [
            "👾 <b>[ITCH.IO SYSTEMS & GAME ENGINE DIGEST]</b>",
            f"👤 Creator: <code>{user_handle}</code> | ⏰ <code>{now_str}</code>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        # Section A: Analytics
        if report:
            v_delta = f" (+{report.delta_views:,})" if report.delta_views > 0 else ""
            d_delta = f" (+{report.delta_downloads:,})" if report.delta_downloads > 0 else ""
            p_delta = f" (+{report.delta_purchases:,})" if report.delta_purchases > 0 else ""
            digest_lines.extend([
                "📊 <b>PROJECT ANALYTICS:</b>",
                f"• 👁️ <b>Views:</b> <code>{report.total_views:,}</code>{v_delta}",
                f"• 💾 <b>Downloads:</b> <code>{report.total_downloads:,}</code>{d_delta}",
                f"• 💰 <b>Purchases:</b> <code>{report.total_purchases:,}</code>{p_delta}",
                f"• 📦 <b>Tracked:</b> <code>{len(report.games)}</code> projects",
            ])
        else:
            digest_lines.extend([
                "📊 <b>PROJECT ANALYTICS:</b>",
                "• <i>No active analytics data available.</i>",
            ])

        digest_lines.append("─────────────────────────────")

        # Section B: Jam Radar
        highlight_jams = fresh_jams if fresh_jams else (all_jams if force else [])
        if highlight_jams:
            digest_lines.append(f"🎯 <b>RADAR: MATCHED JAMS ({len(highlight_jams)}):</b>")
            for j in highlight_jams[:2]:
                digest_lines.append(
                    f"• 🏆 <b><a href=\"{j.url}\">{html.escape(j.title)}</a></b> "
                    f"({j.joined_count:,} joined)\n"
                    f"   🏷️ <i>{j.tags_label}</i> (Score: {j.score} pts)"
                )
        else:
            digest_lines.append("🎯 <b>RADAR:</b> <i>No new tech jams since last scan.</i>")

        digest_lines.append("─────────────────────────────")

        # Section C: Devlog Status
        devlog_candidate = devlog_res.get("candidate")
        if devlog_res.get("status") == "skipped":
            rem_h = devlog_res.get("hours_remaining", 0)
            digest_lines.extend([
                "📝 <b>DEVLOG STATUS:</b>",
                f"• ⏳ <b>Cooldown Active:</b> Next devlog ready in <b>~{rem_h}h</b> (2-day interval enforced).",
            ])
        elif devlog_candidate:
            pub_tag = "🚀 Published Live" if devlog_res.get("published") else "💡 Drafted / Generated"
            digest_lines.extend([
                "📝 <b>DEVLOG STATUS:</b>",
                f"• 🛠️ <b>{pub_tag}:</b> <i>{html.escape(devlog_candidate.title)}</i>",
                f"• 🏷️ <b>Category:</b> <code>{devlog_candidate.category}</code> (0.1% rule verified)",
            ])
            if devlog_candidate.ascii_diagram:
                preview_diagram = devlog_candidate.ascii_diagram.strip()[:240]
                digest_lines.append(f"<pre>{html.escape(preview_diagram)}</pre>")

        digest_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Build inline keyboard buttons
        reply_markup = None
        buttons: list[dict[str, str]] = []
        if highlight_jams and highlight_jams[0].url:
            buttons.append({"text": f"Jam: {highlight_jams[0].title[:18]}...", "url": highlight_jams[0].url})
        buttons.append({"text": "itch.io Profile", "url": f"https://{user_handle}.itch.io"})
        buttons.append({"text": "GitHub", "url": "https://github.com/aman179102"})

        if build_inline_keyboard:
            row1 = [buttons[0]] if len(buttons) >= 3 else []
            row2 = buttons[1:] if len(buttons) >= 3 else buttons
            keyboard_rows = [row1, row2] if row1 else [row2]
            reply_markup = build_inline_keyboard(keyboard_rows)

        consolidated_text = "\n".join(digest_lines)

        # Dispatch single consolidated message strictly when there is fresh activity or force=True
        has_new_activity = (
            (report and (report.delta_views > 0 or report.delta_downloads > 0 or report.delta_purchases > 0))
            or bool(fresh_jams)
            or bool(devlog_res.get("published"))
            or force
        )

        dispatched = False
        if not dry_run and has_new_activity:
            self._dispatch_telegram(consolidated_text, reply_markup=reply_markup)
            dispatched = True
        else:
            logger.info(
                "itch.io run_all complete: no new deltas, jams, or publications. Telegram dispatch suppressed (zero-spam guarantee)."
            )

        return {
            "status": "success",
            "analytics": analytics_res,
            "radar": radar_res,
            "devlog": devlog_res,
            "consolidated_digest": consolidated_text,
            "telegram_dispatched": dispatched,
        }


# Convenience module-level router functions
def run_devlog(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return ItchRunner(settings).run_devlog(**kwargs)


def run_radar(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return ItchRunner(settings).run_radar(**kwargs)


def run_analytics(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return ItchRunner(settings).run_analytics(**kwargs)


def run_all(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    return ItchRunner(settings).run_all(**kwargs)

from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from automations.tumblr.api import TumblrAPIClient
from automations.tumblr.content import generate_tumblr_post
from automations.tumblr.engagement import discover_reblog_candidates
from automations.tumblr.publisher import TumblrPublisher
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


class TumblrRunner:
    """
    Autonomous Tumblr Engine Orchestrator.
    Manages daily rate caps (max 5/day), anti-ban cooldown intervals (min 2 hours),
    state persistence, and Telegram mobile logging.
    """

    def __init__(
        self,
        settings: Settings,
        publisher: Optional[TumblrPublisher] = None,
        client: Optional[TumblrAPIClient] = None,
    ):
        self.settings = settings
        self.client = client or TumblrAPIClient(settings)
        self.publisher = publisher or TumblrPublisher(settings, client=self.client)

    def _get_state_file(self) -> Path:
        raw_dir = getattr(self.settings, "state_dir", "state") if self.settings else "state"
        p = Path(raw_dir)
        if p.is_dir() or not p.suffix:
            return p / "tumblr_state.json"
        return p

    def _load_state(self) -> dict[str, Any]:
        return load_state(self._get_state_file())

    def _save_state(self, state: dict[str, Any]) -> None:
        save_state(self._get_state_file(), state)

    def _dispatch_telegram(self, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        if send_telegram_message and getattr(self.settings, "has_telegram", False):
            try:
                send_telegram_message(self.settings, text, reply_markup=reply_markup)
            except Exception as exc:
                logger.error("Failed to send Telegram message for Tumblr action: %s", exc)

    def run_post(
        self,
        category: str = "random",
        use_queue: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Executes an autonomous educational tech post cycle.
        """
        state = self._load_state()
        now = time.time()
        last_post_ts = float(state.get("tumblr_last_post_at", 0) or 0)
        min_interval = 7200  # 2 hours minimum cooldown

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_posts: dict[str, int] = state.setdefault("tumblr_daily_posts", {})
        daily_count = daily_posts.get(today_str, 0)
        max_daily = 5

        time_since_last = now - last_post_ts
        if not force:
            if daily_count >= max_daily:
                logger.info("Daily Tumblr post cap reached (%d/%d today). Skipping for account safety.", daily_count, max_daily)
                return {"status": "skipped", "reason": "daily_cap_reached"}
            if last_post_ts > 0 and time_since_last < min_interval:
                wait_mins = int((min_interval - time_since_last) / 60)
                logger.info("Tumblr cooldown active. Next post ready in ~%d minutes.", wait_mins)
                return {"status": "skipped", "reason": f"cooldown_active_{wait_mins}m"}

        candidate = generate_tumblr_post(self.settings, category=category)
        logger.info("Generated high-signal Tumblr post: '%s' (Category: %s)", candidate.title, candidate.category)

        is_auto = getattr(self.settings, "tumblr_auto_publish", False) or self.client.is_configured()
        posted_live = False
        post_id = None
        post_url = None

        if is_auto and self.client.is_configured():
            result = self.publisher.publish_post(candidate, use_queue=use_queue)
            if result.get("status") == "success":
                posted_live = True
                res_resp = result.get("response", {})
                post_id = res_resp.get("id")
                blog_id = self.client._normalize_blog_id()
                post_url = f"https://{blog_id}/post/{post_id}" if post_id else f"https://{blog_id}"

                state["tumblr_last_post_at"] = now
                daily_count += 1
                daily_posts[today_str] = daily_count
                state["tumblr_posts_count"] = int(state.get("tumblr_posts_count", 0)) + 1
                self._save_state(state)

        # Build informative Telegram notification
        title_escaped = html.escape(candidate.title)
        tags_str = ", ".join(f"#{t}" for t in candidate.tags[:5])
        blog_clean = self.client.blog_name or "My Tumblr Blog"

        status_badge = "🚀 <b>[AUTONOMOUSLY POSTED TO TUMBLR]</b>" if posted_live else "🎨 <b>TUMBLR TECH POST (DRY RUN)</b>"
        msg_parts = [
            status_badge,
            f"📌 <b>Title:</b> {title_escaped}",
            f"🏷️ <b>Tags:</b> <code>{html.escape(tags_str)}</code>",
            f"🏢 <b>Blog:</b> <code>{html.escape(blog_clean)}</code>",
        ]
        if post_url:
            msg_parts.append(f"🔗 <b>Direct Link:</b> {post_url}")
        msg_parts.append(f"\n📄 <b>Post Summary:</b>\n\n{html.escape(candidate.body[:350])}...")

        message_text = "\n".join(msg_parts)
        keyboard_rows = []
        if post_url:
            keyboard_rows.append([{"text": "🌐 View on Tumblr", "url": post_url}])
        reply_markup = build_inline_keyboard(keyboard_rows) if (build_inline_keyboard and keyboard_rows) else None

        self._dispatch_telegram(message_text, reply_markup=reply_markup)
        record_action(state, "tumblr_post", {"title": candidate.title, "live": posted_live, "post_id": post_id})
        self._save_state(state)

        return {"status": "success", "title": candidate.title, "live": posted_live, "post_id": post_id, "url": post_url}

    def run_reblog(self, use_queue: bool = False, force: bool = False) -> dict[str, Any]:
        """
        Discovers a trending technical community post and reblogs it with thoughtful commentary.
        """
        state = self._load_state()
        seen_reblog_ids = set(state.setdefault("seen_reblog_ids", []))

        candidates = discover_reblog_candidates(self.client, self.settings, limit=3, seen_ids=seen_reblog_ids)
        if not candidates:
            logger.info("No fresh reblog candidates found matching tech criteria.")
            return {"status": "skipped", "reason": "no_candidates"}

        candidate = candidates[0]
        state["seen_reblog_ids"].append(str(candidate.post_id))
        self._save_state(state)

        posted_live = False
        reblog_url = None
        if self.client.is_configured() and (getattr(self.settings, "tumblr_auto_publish", False) or force):
            res = self.publisher.publish_reblog(candidate, use_queue=use_queue)
            if res.get("status") == "success":
                posted_live = True
                blog_id = self.client._normalize_blog_id()
                reblog_url = f"https://{blog_id}"

        # Dispatch Telegram notification
        status_badge = "🔄 <b>[AUTONOMOUSLY REBLOGGED ON TUMBLR]</b>" if posted_live else "💡 <b>TUMBLR REBLOG MATCH (DRY RUN)</b>"
        msg_parts = [
            status_badge,
            f"🔗 <b>Original Post:</b> {candidate.post_url or f'Post #{candidate.post_id}'}",
            f"🏷️ <b>Tags:</b> <code>{html.escape(', '.join(candidate.tags))}</code>",
            f"\n💬 <b>Your Added Commentary:</b>\n<i>\"{html.escape(candidate.comment)}\"</i>",
        ]
        message_text = "\n".join(msg_parts)
        keyboard_rows = []
        if candidate.post_url:
            keyboard_rows.append([{"text": "🔗 View Original Post", "url": candidate.post_url}])
        reply_markup = build_inline_keyboard(keyboard_rows) if (build_inline_keyboard and keyboard_rows) else None

        self._dispatch_telegram(message_text, reply_markup=reply_markup)
        record_action(state, "tumblr_reblog", {"target_post_id": candidate.post_id, "live": posted_live})
        self._save_state(state)

        return {"status": "success", "post_id": candidate.post_id, "live": posted_live}

    def run_all(self, use_queue: bool = False) -> dict[str, Any]:
        """Executes full Tumblr autonomous cycle (rotates educational post & community curation)."""
        logger.info("Executing full Tumblr autonomous cycle...")
        post_res = self.run_post(use_queue=use_queue)
        reblog_res = self.run_reblog(use_queue=use_queue)
        return {"post": post_res, "reblog": reblog_res}


# Convenient helper functions for server & background scheduler
def run_post(settings: Settings, category: str = "random", use_queue: bool = False) -> dict[str, Any]:
    runner = TumblrRunner(settings)
    return runner.run_post(category=category, use_queue=use_queue)


def run_reblog(settings: Settings, use_queue: bool = False) -> dict[str, Any]:
    runner = TumblrRunner(settings)
    return runner.run_reblog(use_queue=use_queue)


def run_all(settings: Settings, use_queue: bool = False) -> dict[str, Any]:
    runner = TumblrRunner(settings)
    return runner.run_all(use_queue=use_queue)

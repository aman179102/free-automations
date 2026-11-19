"""Hacker News Automation Runner: Orchestrates Hiring Radar, Ask HN Discussion Engine,
and Show HN Showcase, integrating with common config, state, and Telegram 1-tap dispatch.
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from automations.hn.api import HNAPIClient
from automations.hn.discussion import DiscussionEngine
from automations.hn.hiring_radar import HiringRadar
from automations.hn.models import DiscussionTopic, HiringLead, ShowcaseProject
from automations.hn.showcase import ShowcaseManager

try:
    from common.config import Settings, load_settings
    from common.state import is_seen, load_state, mark_seen, record_action, save_state
    from common.telegram import (
        build_github_button,
        build_hn_button,
        build_inline_keyboard,
        build_mailto_button,
        send_telegram_message,
    )
except ImportError:
    # Graceful standalone fallback if common is not in PYTHONPATH
    Settings = None  # type: ignore
    load_settings = None  # type: ignore
    load_state = None  # type: ignore
    save_state = None  # type: ignore
    is_seen = None  # type: ignore
    mark_seen = None  # type: ignore
    record_action = None  # type: ignore
    send_telegram_message = None  # type: ignore
    build_inline_keyboard = None  # type: ignore
    build_hn_button = None  # type: ignore
    build_mailto_button = None  # type: ignore
    build_github_button = None  # type: ignore

logger = logging.getLogger(__name__)


class HNRunner:
    """Orchestrates Hacker News automations and Telegram dispatch."""

    def __init__(self, settings: Optional[Any] = None, state_file: Optional[Path | str] = None):
        if settings is None and load_settings:
            self.settings = load_settings()
        else:
            self.settings = settings

        # Determine state path
        if state_file:
            self.state_path = Path(state_file)
        elif self.settings and getattr(self.settings, "state_dir", None):
            self.state_path = Path(self.settings.state_dir) / "hn_state.json"
        else:
            self.state_path = Path(__file__).resolve().parent.parent.parent / "state" / "hn_state.json"

        self.api_client = HNAPIClient()
        self.hiring_radar = HiringRadar(api_client=self.api_client, settings=self.settings)
        self.discussion_engine = DiscussionEngine(api_client=self.api_client, settings=self.settings)
        self.showcase_manager = ShowcaseManager(settings=self.settings)

    def _load_state(self) -> dict[str, Any]:
        if load_state:
            return load_state(self.state_path)
        return {"seen_ids": [], "history": [], "timestamps": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        if save_state:
            save_state(self.state_path, state)

    def _is_seen(self, state: dict[str, Any], item_id: int | str, key: str = "seen_ids") -> bool:
        if is_seen:
            return is_seen(state, item_id, key=key)
        return str(item_id) in state.get(key, [])

    def _mark_seen(self, state: dict[str, Any], item_id: int | str, key: str = "seen_ids") -> None:
        if mark_seen:
            mark_seen(state, item_id, key=key)
        else:
            state.setdefault(key, []).append(str(item_id))

    def _dispatch_telegram(self, text: str, reply_markup: Optional[dict[str, Any]] = None) -> bool:
        if not self.settings or not getattr(self.settings, "has_telegram", False) or not send_telegram_message:
            logger.info("Telegram not configured or disabled; skipping dispatch.")
            return False
        return send_telegram_message(self.settings, text, reply_markup=reply_markup)

    # =========================================================================
    # 1. Hiring Radar Execution
    # =========================================================================

    def run_hiring_radar(self, max_leads: int = 5, send_telegram: bool = True) -> list[HiringLead]:
        """Scans for 100% remote roles, drafts pitches, and sends alerts to Telegram."""
        state = self._load_state()
        all_leads = self.hiring_radar.scan_latest_thread(max_comments=200)

        new_leads: list[HiringLead] = []
        for lead in all_leads:
            if self._is_seen(state, lead.id, key="seen_hiring_ids"):
                continue
            new_leads.append(lead)
            if len(new_leads) >= max_leads:
                break

        logger.info("Identified %d new remote hiring leads", len(new_leads))

        for lead in new_leads:
            self._mark_seen(state, lead.id, key="seen_hiring_ids")

        # Consolidate into a SINGLE digest message to avoid Telegram flood
        target_thread_id = getattr(new_leads[0], "story_id", None) if new_leads else None
        if send_telegram and new_leads and self.settings and getattr(self.settings, "has_telegram", False):
            msg_parts = [
                f"🎯 <b>[HN HIRING RADAR] {len(new_leads)} New Remote Lead(s) Found</b>\n",
            ]
            for i, lead in enumerate(new_leads[:4], 1):
                company_escaped = html.escape(lead.company or "Startup")
                role_escaped = html.escape(lead.role or "Software Engineer")
                tech_str = ", ".join(lead.matched_keywords[:3]) if lead.matched_keywords else "Systems/AI"
                hn_link = f"https://news.ycombinator.com/item?id={lead.id}"

                lead_line = f"<b>{i}. {company_escaped}</b> — {role_escaped}\n🛠️ <code>{html.escape(tech_str)}</code>"
                if lead.email:
                    lead_line += f" | <code>{html.escape(lead.email)}</code>"
                if lead.apply_url:
                    lead_line += f"\n🔗 <a href=\"{lead.apply_url}\">Apply Link</a>"
                lead_line += f" | <a href=\"{hn_link}\">HN Lead</a>\n"
                msg_parts.append(lead_line)

            message_text = "\n".join(msg_parts)
            keyboard_rows = [
                [
                    {"text": "🧡 View Hiring Thread", "url": f"https://news.ycombinator.com/item?id={target_thread_id}" if target_thread_id else "https://news.ycombinator.com"},
                    {"text": "🐙 Aman's GitHub", "url": "https://github.com/aman179102"},
                ]
            ]
            reply_markup = build_inline_keyboard(keyboard_rows) if build_inline_keyboard else None
            self._dispatch_telegram(message_text, reply_markup=reply_markup)

        if record_action:
            record_action(
                state,
                "hn_hiring_radar",
                {"dispatched_count": len(new_leads), "lead_ids": [l.id for l in new_leads]},
            )
        self._save_state(state)
        return new_leads

    # =========================================================================
    # 2. Ask HN & Tech Discussion Execution (Popular + Rising Autopilot)
    # =========================================================================

    def run_discussion(
        self,
        max_discussions: int = 3,
        mode: str = "all",
        send_telegram: bool = True,
        auto_publish: Optional[bool] = None,
    ) -> list[DiscussionTopic]:
        """
        Discovers popular top discussions or rising fresh threads on Hacker News,
        drafts senior engineering answers, and autonomously publishes them if enabled.
        """
        state = self._load_state()

        if mode == "popular":
            all_topics = self.discussion_engine.find_popular_discussions(limit=max_discussions * 3)
        elif mode == "rising":
            all_topics = self.discussion_engine.find_rising_and_new_discussions(limit=max_discussions * 3)
        elif mode == "targets" and hasattr(self.discussion_engine, "find_all_discussion_targets"):
            all_topics = self.discussion_engine.find_all_discussion_targets(limit=max_discussions * 3)
        else:
            all_topics = self.discussion_engine.find_architecture_discussions(limit=max_discussions * 3)

        new_topics: list[DiscussionTopic] = []
        for topic in all_topics:
            if self._is_seen(state, topic.story.id, key="seen_discussion_ids"):
                continue
            new_topics.append(topic)
            if len(new_topics) >= max_discussions:
                break

        logger.info("Found %d targeted discussions in mode '%s'", len(new_topics), mode)

        if auto_publish is None:
            raw_setting = getattr(self.settings, "hn_auto_publish", False) if self.settings else False
            is_auto = bool(raw_setting is True)
        else:
            is_auto = bool(auto_publish)

        # Ultra-safe anti-ban cooldown gate: minimum 45 minutes between automated comments
        min_comment_interval = 2700  # 45 minutes
        last_comment_ts = float(state.get("hn_last_comment_at", 0) or 0)
        now = time.time()

        # Check daily comments limit (max 4 per 24 hours to prevent spam heuristics)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_comments: dict[str, int] = state.setdefault("hn_daily_comments", {})
        daily_count = daily_comments.get(today_str, 0)
        max_daily_comments = 4

        for topic in new_topics:
            self._mark_seen(state, topic.story.id, key="seen_discussion_ids")

            posted_live = False
            if is_auto:
                time_since_last = now - last_comment_ts
                if daily_count >= max_daily_comments:
                    logger.info(
                        "Daily comment limit reached (%d/%d today). Skipping auto-comment on #%d to preserve account safety.",
                        daily_count,
                        max_daily_comments,
                        topic.story.id,
                    )
                elif last_comment_ts > 0 and time_since_last < min_comment_interval:
                    wait_mins = int((min_comment_interval - time_since_last) / 60)
                    logger.info(
                        "Comment cooldown active (last comment %ds ago). Skipping auto-comment on #%d to avoid rate-limits (ready in ~%dm).",
                        int(time_since_last),
                        topic.story.id,
                        wait_mins,
                    )
                else:
                    try:
                        from automations.hn.publisher import HNPublisher
                        pub = HNPublisher(self.settings)
                        posted_live = pub.post_comment(topic.story.id, topic.draft_response)
                        if posted_live:
                            last_comment_ts = now
                            state["hn_last_comment_at"] = now
                            daily_count += 1
                            daily_comments[today_str] = daily_count
                            state["hn_comments_posted_count"] = int(state.get("hn_comments_posted_count", 0)) + 1
                    except Exception as exc:
                        logger.warning("Auto-publish comment error on #%d: %s", topic.story.id, exc)

            # Only send Telegram message if an actual comment was posted live!
            # (In dry-run mode, send at most 1 example to prevent message spam)
            should_notify = posted_live or (not is_auto and send_telegram)
            if should_notify and self.settings and getattr(self.settings, "has_telegram", False):
                title_escaped = html.escape(topic.story.title)
                cat_escaped = html.escape(topic.category)
                reply_escaped = html.escape(topic.draft_response)
                hn_link = f"https://news.ycombinator.com/item?id={topic.story.id}"

                status_badge = "🚀 <b>[AUTONOMOUSLY POSTED TO HN]</b>" if posted_live else "🧠 <b>HN DISCUSSION MATCH (DRY RUN)</b>"
                msg_parts = [
                    status_badge,
                    f"📌 <b>Story:</b> <a href=\"{hn_link}\">{title_escaped}</a>",
                    f"🏷️ <b>Category:</b> <code>{cat_escaped}</code>",
                    f"⭐ <b>Score:</b> {topic.story.score} pts | 💬 {topic.story.descendants} comments",
                    f"🔗 <b>Direct Link:</b> {hn_link}",
                    f"\n💬 <b>Your Posted Comment:</b>\n\n{reply_escaped}",
                ]
                message_text = "\n".join(msg_parts)

                keyboard_rows = [
                    [
                        {"text": "🧡 View on Hacker News", "url": hn_link},
                        {"text": "🐙 Aman's GitHub", "url": "https://github.com/aman179102"},
                    ]
                ]
                reply_markup = build_inline_keyboard(keyboard_rows) if build_inline_keyboard else None
                self._dispatch_telegram(message_text, reply_markup=reply_markup)

                if not posted_live:
                    break  # In dry run mode, stop after 1 notification

        if record_action:
            record_action(
                state,
                "hn_discussion",
                {
                    "mode": mode,
                    "dispatched_count": len(new_topics),
                    "story_ids": [t.story.id for t in new_topics],
                    "auto_published": is_auto,
                },
            )
        self._save_state(state)
        return new_topics

    # =========================================================================
    # 3. Show HN Showcase Execution (Autonomous Project Publishing)
    # =========================================================================

    def run_showcase(
        self,
        project_slug: Optional[str] = None,
        send_telegram: bool = True,
        auto_publish: Optional[bool] = None,
        force: bool = False,
    ) -> list[ShowcaseProject]:
        """
        Drafts and autonomously submits 'Show HN' posts for Aman's open-source projects.
        Rotates projects systematically and adheres to safety cooldowns.
        """
        state = self._load_state()

        if auto_publish is None:
            raw_setting = getattr(self.settings, "hn_auto_publish", False) if self.settings else False
            is_auto = bool(raw_setting is True)
        else:
            is_auto = bool(auto_publish)

        # 1. Project selection: specific slug or round-robin rotation
        posted_history: dict[str, float] = state.get("posted_showcases", {})
        if project_slug:
            project = self.showcase_manager.get_project(project_slug)
            projects = [project] if project else []
        else:
            all_projs = self.showcase_manager.list_projects()
            # Sort projects so the least recently posted (or never posted) comes first
            all_projs.sort(key=lambda p: posted_history.get(p.slug, 0.0))
            projects = [all_projs[0]] if all_projs else []

        # Anti-spam cooldown gate for story submissions: at least 3 days between Show HN submissions
        min_story_interval = 3 * 86400  # 3 days
        last_story_ts = float(state.get("hn_last_story_at", 0) or 0)
        now = time.time()

        for proj in projects:
            submitted_url: Optional[str] = None
            if is_auto:
                time_since_last_story = now - last_story_ts
                if not force and last_story_ts > 0 and time_since_last_story < min_story_interval:
                    days_left = round((min_story_interval - time_since_last_story) / 86400, 1)
                    logger.info(
                        "Story submission cooldown active (last story %ds ago). Skipping Show HN '%s' (ready in ~%.1f days).",
                        int(time_since_last_story),
                        proj.title,
                        days_left,
                    )
                else:
                    try:
                        from automations.hn.publisher import HNPublisher
                        pub = HNPublisher(self.settings)
                        submitted_url = pub.submit_story(
                            title=proj.suggested_title,
                            text=proj.body_markdown,
                            url="",
                        )
                        if submitted_url:
                            last_story_ts = now
                            state["hn_last_story_at"] = now
                            posted_history[proj.slug] = now
                            state["posted_showcases"] = posted_history
                    except Exception as exc:
                        logger.warning("Auto-publish Show HN error: %s", exc)

            # Only notify if story was actually submitted live!
            # (In dry run mode, only notify if user manually requested it)
            should_notify = bool(submitted_url) or (not is_auto and send_telegram)
            if should_notify and self.settings and getattr(self.settings, "has_telegram", False):
                title_escaped = html.escape(proj.suggested_title)
                body_escaped = html.escape(proj.body_markdown)

                if submitted_url:
                    header = "🚀 <b>[AUTONOMOUSLY SUBMITTED SHOW HN]</b>"
                    link_info = f"🔗 <b>HN Post Link:</b> {submitted_url}"
                else:
                    header = "🚀 <b>SHOW HN LAUNCH DRAFT (DRY RUN)</b>"
                    link_info = f"🔗 <b>Submit Link:</b> https://news.ycombinator.com/submit"

                msg_parts = [
                    header,
                    f"📦 <b>Project:</b> {html.escape(proj.title)}",
                    f"📌 <b>Title:</b>\n<code>{title_escaped}</code>",
                    link_info,
                    f"🐙 <b>GitHub:</b> {proj.github_url}",
                    f"🛠️ <b>Tech:</b> <code>{', '.join(proj.tech_stack)}</code>",
                    f"\n📄 <b>Post Submission Text:</b>\n\n{body_escaped}",
                ]
                message_text = "\n".join(msg_parts)

                keyboard_rows = [
                    [
                        {"text": "🧡 View on Hacker News", "url": submitted_url or "https://news.ycombinator.com/show"},
                        {"text": "🐙 GitHub Repo", "url": proj.github_url},
                    ]
                ]
                reply_markup = build_inline_keyboard(keyboard_rows) if build_inline_keyboard else None
                self._dispatch_telegram(message_text, reply_markup=reply_markup)

                if not submitted_url:
                    break  # In dry run mode, only send 1 draft example

        if record_action:
            record_action(
                state,
                "hn_showcase",
                {
                    "projects": [p.slug for p in projects],
                    "auto_published": is_auto,
                },
            )
        self._save_state(state)
        return projects

    # =========================================================================
    # 4. Master Orchestrator
    # =========================================================================

    def run_all(self, send_telegram: bool = True, auto_publish: Optional[bool] = None) -> dict[str, Any]:
        """Runs hiring radar, popular & rising discussion engine, showcase, and profile tracker."""
        logger.info("Starting complete HN automation pipeline...")
        leads = self.run_hiring_radar(max_leads=5, send_telegram=send_telegram)
        discussions = self.run_discussion(max_discussions=3, mode="all", send_telegram=send_telegram, auto_publish=auto_publish)
        showcases = self.run_showcase(send_telegram=send_telegram, auto_publish=auto_publish)

        # Profile & karma status check
        karma_val = None
        hn_user = getattr(self.settings, "hn_username", "") if self.settings else ""
        if isinstance(hn_user, str) and hn_user:
            user_data = self.api_client.get_user(hn_user)
            if user_data:
                karma_val = user_data.get("karma", 0)
                logger.info("Hacker News user '%s' current karma: %d", hn_user, karma_val)

        return {
            "hiring_leads_count": len(leads),
            "discussions_count": len(discussions),
            "showcases_count": len(showcases),
            "karma": karma_val,
            "leads": [l.company for l in leads],
            "discussions": [d.story.title for d in discussions],
            "showcase": [s.title for s in showcases],
        }


# =============================================================================
# Module-level convenience functions (for main.py and server.py integration)
# =============================================================================

def run_hiring_radar(settings: Optional[Any] = None, dry_run: bool = False, max_leads: int = 5) -> list[HiringLead]:
    """Module-level entry point to scan Who is Hiring threads."""
    runner = HNRunner(settings=settings)
    return runner.run_hiring_radar(max_leads=max_leads, send_telegram=not dry_run)


def run_discussion(
    settings: Optional[Any] = None,
    dry_run: bool = False,
    max_discussions: int = 3,
    mode: Optional[str] = None,
    auto_publish: Optional[bool] = None,
) -> list[DiscussionTopic]:
    """Module-level entry point to find popular & rising threads and comment autonomously."""
    runner = HNRunner(settings=settings)
    kwargs: dict[str, Any] = {
        "max_discussions": max_discussions,
        "send_telegram": not dry_run,
    }
    if mode is not None:
        kwargs["mode"] = mode
    if auto_publish is not None:
        kwargs["auto_publish"] = auto_publish
    return runner.run_discussion(**kwargs)


def run_showcase(
    settings: Optional[Any] = None,
    dry_run: bool = False,
    project_slug: Optional[str] = None,
    auto_publish: Optional[bool] = None,
) -> list[ShowcaseProject]:
    """Module-level entry point to draft and autonomously submit Show HN posts."""
    runner = HNRunner(settings=settings)
    kwargs: dict[str, Any] = {
        "project_slug": project_slug,
        "send_telegram": not dry_run,
    }
    if auto_publish is not None:
        kwargs["auto_publish"] = auto_publish
    return runner.run_showcase(**kwargs)


def run_all(settings: Optional[Any] = None, dry_run: bool = False, auto_publish: Optional[bool] = None) -> dict[str, Any]:
    """Module-level entry point to run full Hacker News automation pipeline."""
    runner = HNRunner(settings=settings)
    kwargs: dict[str, Any] = {
        "send_telegram": not dry_run,
    }
    if auto_publish is not None:
        kwargs["auto_publish"] = auto_publish
    return runner.run_all(**kwargs)


def main() -> None:
    """CLI Entry Point for Hacker News automation runner."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Hacker News 100% Free Autonomous Growth & Radar Module")
    parser.add_argument(
        "--mode",
        choices=["hiring", "discussion", "showcase", "all"],
        default="all",
        help="Automation mode to execute",
    )
    parser.add_argument("--discussion-mode", choices=["all", "popular", "rising", "ask_hn"], default="all", help="Discussion target category")
    parser.add_argument("--limit", type=int, default=5, help="Maximum items to process")
    parser.add_argument("--project", type=str, default="", help="Specific project slug for showcase")
    parser.add_argument("--dry-run", action="store_true", help="Print outputs without dispatching to Telegram")
    parser.add_argument("--live", "--auto-publish", action="store_true", dest="live", help="Autonomously publish comments and Show HN posts to Hacker News")
    args = parser.parse_args()

    runner = HNRunner()
    send_telegram = not args.dry_run

    print(f"\n🚀 Running Hacker News automation in mode: '{args.mode}' (send_telegram={send_telegram}, live={args.live})\n")

    if args.mode == "hiring":
        leads = runner.run_hiring_radar(max_leads=args.limit, send_telegram=send_telegram)
        print(f"✅ Processed {len(leads)} remote hiring leads:")
        for l in leads:
            print(f"  • {l.company} - {l.role} (Contact: {l.founder_or_contact}, Email: {l.email})")

    elif args.mode == "discussion":
        topics = runner.run_discussion(
            max_discussions=args.limit,
            mode=args.discussion_mode,
            send_telegram=send_telegram,
            auto_publish=args.live if args.live else None,
        )
        print(f"✅ Processed {len(topics)} discussions ({args.discussion_mode}):")
        for t in topics:
            print(f"  • [{t.category}] {t.story.title} ({t.story.score} pts)")

    elif args.mode == "showcase":
        project_slug = args.project or None
        projects = runner.run_showcase(
            project_slug=project_slug,
            send_telegram=send_telegram,
            auto_publish=args.live if args.live else None,
        )
        print(f"✅ Processed {len(projects)} Show HN posts:")
        for p in projects:
            print(f"  • {p.suggested_title}")

    elif args.mode == "all":
        results = runner.run_all(
            send_telegram=send_telegram,
            auto_publish=args.live if args.live else None,
        )
        print("✅ Pipeline execution completed successfully:")
        print(f"  • Hiring Leads: {results['hiring_leads_count']}")
        print(f"  • Discussions: {results['discussions_count']}")
        print(f"  • Showcases: {results['showcases_count']}")
        if results.get("karma") is not None:
            print(f"  • Account Karma: {results['karma']}")


if __name__ == "__main__":
    main()

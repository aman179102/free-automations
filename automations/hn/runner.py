"""Hacker News Automation Runner: Orchestrates Hiring Radar, Ask HN Discussion Engine,
and Show HN Showcase, integrating with common config, state, and Telegram 1-tap dispatch.
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
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

            if send_telegram and self.settings and getattr(self.settings, "has_telegram", False):
                # Build rich HTML message
                company_escaped = html.escape(lead.company or "Startup")
                role_escaped = html.escape(lead.role or "Software Engineer")
                contact_escaped = html.escape(lead.founder_or_contact or "Founder/Lead")
                tech_str = ", ".join(lead.matched_keywords) or "Go, Python, FastAPI"
                pitch_escaped = html.escape(lead.pitch_draft or "")

                msg_parts = [
                    f"🎯 <b>HN 100% REMOTE HIRING LEAD</b>",
                    f"🏢 <b>Company:</b> {company_escaped}",
                    f"💼 <b>Role:</b> {role_escaped}",
                    f"🌐 <b>Location:</b> <code>100% Remote</code>",
                    f"🛠️ <b>Tech Stack:</b> <code>{tech_str}</code>",
                    f"👤 <b>Contact:</b> {contact_escaped}",
                ]
                if lead.email:
                    msg_parts.append(f"✉️ <b>Email:</b> <code>{html.escape(lead.email)}</code>")

                msg_parts.append(f"\n📝 <b>Tailored Outreach Pitch:</b>\n<i>{pitch_escaped}</i>")

                message_text = "\n".join(msg_parts)

                # Build buttons
                keyboard_rows = []
                # First row: HN comment link & mailto intent
                first_row = []
                if build_hn_button:
                    first_row.append(build_hn_button(lead.id, "🧡 Open on HN"))
                if lead.email and build_mailto_button:
                    first_row.append(
                        build_mailto_button(
                            email=lead.email,
                            subject=f"Application: {lead.role} - Aman Kumar",
                            body=lead.pitch_draft or "",
                            text="✉️ 1-Tap Email",
                        )
                    )
                if first_row:
                    keyboard_rows.append(first_row)

                # Second row: Apply URL / GitHub
                second_row = []
                if lead.apply_url:
                    second_row.append({"text": "🔗 Apply Page", "url": lead.apply_url})
                if build_github_button:
                    second_row.append(build_github_button(text="🐙 Aman's GitHub"))
                if second_row:
                    keyboard_rows.append(second_row)

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
    # 2. Ask HN Discussion Execution
    # =========================================================================

    def run_discussion(self, max_discussions: int = 3, send_telegram: bool = True) -> list[DiscussionTopic]:
        """Finds rising Ask HN architecture threads and drafts senior engineering answers."""
        state = self._load_state()
        all_topics = self.discussion_engine.find_architecture_discussions(limit=15)

        new_topics: list[DiscussionTopic] = []
        for topic in all_topics:
            if self._is_seen(state, topic.story.id, key="seen_discussion_ids"):
                continue
            new_topics.append(topic)
            if len(new_topics) >= max_discussions:
                break

        logger.info("Found %d rising Ask HN architecture discussions", len(new_topics))

        for topic in new_topics:
            self._mark_seen(state, topic.story.id, key="seen_discussion_ids")

            if send_telegram and self.settings and getattr(self.settings, "has_telegram", False):
                title_escaped = html.escape(topic.story.title)
                cat_escaped = html.escape(topic.category)
                reply_escaped = html.escape(topic.draft_response)

                msg_parts = [
                    f"🧠 <b>ASK HN ARCHITECTURE DISCUSSION</b>",
                    f"📌 <b>Title:</b> {title_escaped}",
                    f"🏷️ <b>Domain:</b> <code>{cat_escaped}</code>",
                    f"⭐ <b>Score:</b> {topic.story.score} pts | 💬 {topic.story.descendants} comments",
                    f"\n💡 <b>Senior Systems Architecture Response:</b>\n\n{reply_escaped}",
                ]
                message_text = "\n".join(msg_parts)

                keyboard_rows = []
                first_row = []
                if build_hn_button:
                    first_row.append(build_hn_button(topic.story.id, "🧡 Reply on HN"))
                if build_github_button:
                    first_row.append(build_github_button(text="🐙 Aman's GitHub"))
                if first_row:
                    keyboard_rows.append(first_row)

                reply_markup = build_inline_keyboard(keyboard_rows) if build_inline_keyboard else None
                self._dispatch_telegram(message_text, reply_markup=reply_markup)

        if record_action:
            record_action(
                state,
                "hn_discussion",
                {"dispatched_count": len(new_topics), "story_ids": [t.story.id for t in new_topics]},
            )
        self._save_state(state)
        return new_topics

    # =========================================================================
    # 3. Show HN Showcase Execution
    # =========================================================================

    def run_showcase(
        self,
        project_slug: Optional[str] = None,
        send_telegram: bool = True,
    ) -> list[ShowcaseProject]:
        """Drafts Show HN post for a specified project or all projects and previews in Telegram."""
        state = self._load_state()

        if project_slug:
            project = self.showcase_manager.get_project(project_slug)
            projects = [project] if project else []
        else:
            projects = self.showcase_manager.list_projects()

        for proj in projects:
            if send_telegram and self.settings and getattr(self.settings, "has_telegram", False):
                title_escaped = html.escape(proj.suggested_title)
                repo_url = proj.github_url
                body_escaped = html.escape(proj.body_markdown)

                # Telegram message
                msg_parts = [
                    f"🚀 <b>SHOW HN LAUNCH DRAFT</b>",
                    f"📦 <b>Project:</b> {html.escape(proj.title)}",
                    f"🔗 <b>Suggested Title:</b>\n<code>{title_escaped}</code>",
                    f"🛠️ <b>Tech:</b> <code>{', '.join(proj.tech_stack)}</code>",
                    f"\n📄 <b>Post Submission Draft:</b>\n\n{body_escaped}",
                ]
                message_text = "\n".join(msg_parts)

                keyboard_rows = [
                    [
                        {"text": "🧡 Submit to HN", "url": "https://news.ycombinator.com/submit"},
                        {"text": "🐙 GitHub Repo", "url": repo_url},
                    ]
                ]
                reply_markup = build_inline_keyboard(keyboard_rows) if build_inline_keyboard else None
                self._dispatch_telegram(message_text, reply_markup=reply_markup)

        if record_action:
            record_action(
                state,
                "hn_showcase",
                {"projects": [p.slug for p in projects]},
            )
        self._save_state(state)
        return projects

    # =========================================================================
    # 4. Master Orchestrator
    # =========================================================================

    def run_all(self, send_telegram: bool = True) -> dict[str, Any]:
        """Runs hiring radar, discussion engine, and showcase sequentially."""
        logger.info("Starting complete HN automation pipeline...")
        leads = self.run_hiring_radar(max_leads=5, send_telegram=send_telegram)
        discussions = self.run_discussion(max_discussions=3, send_telegram=send_telegram)
        showcases = self.run_showcase(project_slug="distributed-file-storage", send_telegram=send_telegram)

        return {
            "hiring_leads_count": len(leads),
            "discussions_count": len(discussions),
            "showcases_count": len(showcases),
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


def run_discussion(settings: Optional[Any] = None, dry_run: bool = False, max_discussions: int = 3) -> list[DiscussionTopic]:
    """Module-level entry point to find rising Ask HN architecture threads."""
    runner = HNRunner(settings=settings)
    return runner.run_discussion(max_discussions=max_discussions, send_telegram=not dry_run)


def run_showcase(settings: Optional[Any] = None, dry_run: bool = False, project_slug: Optional[str] = None) -> list[ShowcaseProject]:
    """Module-level entry point to draft Show HN posts."""
    runner = HNRunner(settings=settings)
    return runner.run_showcase(project_slug=project_slug, send_telegram=not dry_run)


def run_all(settings: Optional[Any] = None, dry_run: bool = False) -> dict[str, Any]:
    """Module-level entry point to run full Hacker News automation pipeline."""
    runner = HNRunner(settings=settings)
    return runner.run_all(send_telegram=not dry_run)


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
    parser.add_argument("--limit", type=int, default=5, help="Maximum items to process")
    parser.add_argument("--project", type=str, default="", help="Specific project slug for showcase")
    parser.add_argument("--dry-run", action="store_true", help="Print outputs without dispatching to Telegram")
    args = parser.parse_args()

    runner = HNRunner()
    send_telegram = not args.dry_run

    print(f"\n🚀 Running Hacker News automation in mode: '{args.mode}' (send_telegram={send_telegram})\n")

    if args.mode == "hiring":
        leads = runner.run_hiring_radar(max_leads=args.limit, send_telegram=send_telegram)
        print(f"✅ Processed {len(leads)} remote hiring leads:")
        for l in leads:
            print(f"  • {l.company} - {l.role} (Contact: {l.founder_or_contact}, Email: {l.email})")

    elif args.mode == "discussion":
        topics = runner.run_discussion(max_discussions=args.limit, send_telegram=send_telegram)
        print(f"✅ Processed {len(topics)} architecture discussions:")
        for t in topics:
            print(f"  • [{t.category}] {t.story.title} ({t.story.score} pts)")

    elif args.mode == "showcase":
        project_slug = args.project or None
        projects = runner.run_showcase(project_slug=project_slug, send_telegram=send_telegram)
        print(f"✅ Generated {len(projects)} Show HN drafts:")
        for p in projects:
            print(f"  • {p.suggested_title}")

    elif args.mode == "all":
        results = runner.run_all(send_telegram=send_telegram)
        print("✅ Pipeline execution completed successfully:")
        print(f"  • Hiring Leads: {results['hiring_leads_count']}")
        print(f"  • Discussions: {results['discussions_count']}")
        print(f"  • Showcases: {results['showcases_count']}")


if __name__ == "__main__":
    main()

"""Hacker News automation module for free-automations platform."""

from automations.hn.api import HNAPIClient
from automations.hn.discussion import DiscussionEngine, draft_architect_response, score_discussion_candidate
from automations.hn.hiring_radar import (
    HiringRadar,
    classify_tech_stack,
    draft_tailored_pitch,
    extract_apply_urls,
    extract_emails,
    is_remote_eligible,
    parse_header_tokens,
)
from automations.hn.models import (
    DiscussionTopic,
    HNComment,
    HNStory,
    HiringLead,
    ShowcaseProject,
    strip_html_tags,
)
from automations.hn.runner import (
    HNRunner,
    run_all,
    run_discussion,
    run_hiring_radar,
    run_showcase,
)
from automations.hn.showcase import ShowcaseManager

__all__ = [
    "HNAPIClient",
    "HNStory",
    "HNComment",
    "HiringLead",
    "DiscussionTopic",
    "ShowcaseProject",
    "strip_html_tags",
    "HiringRadar",
    "classify_tech_stack",
    "is_remote_eligible",
    "extract_emails",
    "extract_apply_urls",
    "parse_header_tokens",
    "draft_tailored_pitch",
    "DiscussionEngine",
    "score_discussion_candidate",
    "draft_architect_response",
    "ShowcaseManager",
    "HNRunner",
    "run_hiring_radar",
    "run_discussion",
    "run_showcase",
    "run_all",
]

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Optional

from automations.tumblr.api import TumblrAPIClient
from common.ai import generate_text
from common.config import Settings

logger = logging.getLogger(__name__)

TECH_TAGS = [
    "programming",
    "software engineering",
    "backend development",
    "systems architecture",
    "linux",
    "python",
    "golang",
    "databases",
    "computer science",
]

REBLOG_COMMENT_TEMPLATES = [
    "Spot-on breakdown. In distributed systems, this exact trade-off between consistency and latency is what catches teams off-guard during scale-up.",
    "Crucial insight here. Especially when scaling connection pools and managing tail latencies under burst traffic, this pattern is essential.",
    "Clean explanation. Many engineers overlook the memory overhead and context switching until they profile under load.",
    "Great architectural perspective. Designing with clean failure boundaries and idempotent operations makes production debugging 10x easier.",
    "Very well summarized. The distinction between asynchronous polling and event-driven interrupts is fundamental to modern high-throughput I/O.",
]


@dataclass(frozen=True)
class CuratedReblogCandidate:
    post_id: int | str
    reblog_key: str
    post_url: str
    summary: str
    comment: str
    tags: list[str]


def discover_reblog_candidates(
    client: TumblrAPIClient,
    settings: Settings,
    limit: int = 5,
    seen_ids: set[str] | None = None,
) -> list[CuratedReblogCandidate]:
    """
    Discovers trending technical posts from community tags and generates insightful commentary.
    """
    seen_ids = seen_ids or set()
    sampled_tags = random.sample(TECH_TAGS, min(3, len(TECH_TAGS)))
    candidates: list[CuratedReblogCandidate] = []

    for tag in sampled_tags:
        try:
            posts = client.get_tagged_posts(tag, limit=8)
            for p in posts:
                post_id = str(p.get("id", ""))
                reblog_key = str(p.get("reblog_key", ""))
                post_url = str(p.get("post_url", ""))
                summary = str(p.get("summary", "") or p.get("caption", "") or p.get("trail", [{}])[0].get("content_raw", "") if p.get("trail") else "")

                if not post_id or not reblog_key or post_id in seen_ids:
                    continue

                # Filter: must have reasonable text/technical summary
                if len(summary.strip()) < 30:
                    continue

                # Generate insightful commentary
                comment = _generate_reblog_comment(summary, settings)
                candidate = CuratedReblogCandidate(
                    post_id=post_id,
                    reblog_key=reblog_key,
                    post_url=post_url,
                    summary=summary[:150],
                    comment=comment,
                    tags=[tag, "tech community", "systems engineering"],
                )
                candidates.append(candidate)
                if len(candidates) >= limit:
                    return candidates
        except Exception as exc:
            logger.warning("Error discovering tagged posts for tag #%s: %s", tag, exc)

    return candidates


def _generate_reblog_comment(summary: str, settings: Settings) -> str:
    """Generates senior engineering commentary for reblogging."""
    if settings.has_ai:
        prompt = (
            f"Original Post Summary: \"{summary[:300]}\"\n\n"
            f"Write a 1 to 2 sentence insightful, constructive commentary to add to this post when reblogging it.\n"
            f"Add value from a systems/software engineering perspective.\n"
            f"DO NOT include links, hashtags, or promotional text. Plain insightful text only.\n"
        )
        ai_resp = generate_text(
            settings=settings,
            system="You are a Senior Systems & AI Architect reading a community tech blog post.",
            user=prompt,
            temperature=0.6,
        )
        if ai_resp and len(ai_resp.strip()) > 20:
            return ai_resp.strip()

    return random.choice(REBLOG_COMMENT_TEMPLATES)

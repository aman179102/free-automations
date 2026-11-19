from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

from automations.tumblr.api import TumblrAPIClient, TumblrAPIError
from automations.tumblr.content import TumblrPostCandidate
from automations.tumblr.engagement import CuratedReblogCandidate
from common.config import Settings

logger = logging.getLogger(__name__)


class TumblrPublisher:
    """
    Handles publishing and queuing posts/reblogs to Tumblr with anti-ban safety gates,
    natural human typing pauses, and tag rotation.
    """

    def __init__(self, settings: Settings, client: Optional[TumblrAPIClient] = None):
        self.settings = settings
        self.client = client or TumblrAPIClient(settings)

    def publish_post(
        self,
        candidate: TumblrPostCandidate,
        use_queue: bool = False,
    ) -> dict[str, Any]:
        """
        Publishes or queues an aesthetic technical post with human-like jitter delay.
        """
        if not self.client.is_configured():
            logger.warning("Tumblr API is not configured (missing consumer key or tokens).")
            return {"status": "unconfigured", "error": "Missing Tumblr credentials"}

        # Human-like delay (5-10 seconds) to avoid machine burst detection
        jitter = random.uniform(4.5, 9.5)
        logger.info("Natural delay (%.1fs) before publishing Tumblr post...", jitter)
        time.sleep(jitter)

        state = "queue" if use_queue else "published"
        tags = candidate.tags[:7]  # Enforce max 7 tags to avoid tag-stuffing filters

        try:
            res = self.client.create_text_post(
                title=candidate.title,
                body=candidate.body,
                tags=tags,
                state=state,
            )
            return {"status": "success", "response": res, "state": state}
        except TumblrAPIError as exc:
            logger.error("Failed to publish Tumblr post '%s': %s", candidate.title, exc)
            return {"status": "error", "error": str(exc)}

    def publish_reblog(
        self,
        candidate: CuratedReblogCandidate,
        use_queue: bool = False,
    ) -> dict[str, Any]:
        """
        Reblogs a community post with insightful engineering commentary.
        """
        if not self.client.is_configured():
            return {"status": "unconfigured", "error": "Missing Tumblr credentials"}

        jitter = random.uniform(4.0, 8.0)
        logger.info("Natural delay (%.1fs) before reblogging Tumblr post #%s...", jitter, candidate.post_id)
        time.sleep(jitter)

        state = "queue" if use_queue else "published"
        try:
            res = self.client.reblog_post(
                post_id=candidate.post_id,
                reblog_key=candidate.reblog_key,
                comment=candidate.comment,
                tags=candidate.tags[:5],
                state=state,
            )
            return {"status": "success", "response": res, "state": state}
        except TumblrAPIError as exc:
            logger.error("Failed to reblog Tumblr post #%s: %s", candidate.post_id, exc)
            return {"status": "error", "error": str(exc)}

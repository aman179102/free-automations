from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

from automations.devto.api import DevtoAPIClient, DevtoAPIError
from automations.devto.articles import DevtoArticleCandidate, validate_article_content
from common.config import Settings

logger = logging.getLogger(__name__)


class DevtoRateLimitError(RuntimeError):
    """Raised when Dev.to rate limits or publication cooldown bounds are exceeded."""
    pass


class DevtoPublisher:
    """
    Anti-ban safety and publication engine for Dev.to.
    Enforces:
      - Anti-ban natural human review jitter delays (5.0s to 12.0s)
      - Strict daily publication cap (maximum 1-2 daily posts)
      - Content safety, architectural code blocks, and 0.1% rule validation gates
      - Clean Forem API payload assembly
    """

    def __init__(
        self,
        settings: Settings,
        client: Optional[DevtoAPIClient] = None,
        min_cooldown_seconds: float = 300.0,
        max_daily_posts: int = 2,
    ):
        self.settings = settings
        self.client = client or DevtoAPIClient(settings)
        self.min_cooldown_seconds = min_cooldown_seconds
        self.max_daily_posts = max_daily_posts

        self._last_publish_ts: float = 0.0
        self._daily_counts: dict[str, int] = {}

    def _check_rate_limit(self) -> tuple[bool, str]:
        """Verifies if publishing is allowed under cooldown and daily caps."""
        now = time.time()
        elapsed = now - self._last_publish_ts
        if self._last_publish_ts > 0 and elapsed < self.min_cooldown_seconds:
            wait_rem = int(self.min_cooldown_seconds - elapsed)
            return False, f"Anti-spam rate limit active. Please wait {wait_rem}s."

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily = self._daily_counts.get(today, 0)
        if daily >= self.max_daily_posts:
            return False, f"Daily publish cap reached ({daily}/{self.max_daily_posts} today)."

        return True, "OK"

    def _apply_anti_ban_jitter(self, min_delay: float = 5.0, max_delay: float = 12.0) -> float:
        """Executes natural human typing and review jitter delay (5.0s - 12.0s)."""
        jitter = random.uniform(min_delay, max_delay)
        logger.info("Applying anti-ban human jitter pause (%.2fs) before Dev.to publication...", jitter)
        time.sleep(jitter)
        return jitter

    def publish_article(
        self,
        candidate: DevtoArticleCandidate,
        published: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Publishes a validated technical article to Dev.to.
        Applies anti-ban jitter pauses and strict rate limits.
        """
        # 1. Content validation gate (length, diagrams, code blocks, 0.1% rule)
        is_valid, validation_msg = validate_article_content(candidate)
        if not is_valid:
            logger.error("Dev.to article validation rejected: %s", validation_msg)
            return {"status": "error", "error": f"Content validation failed: {validation_msg}"}

        # 2. Dry-run gate
        if dry_run:
            logger.info("[Dry-Run] Simulating Dev.to article publish: '%s'", candidate.title)
            return {
                "status": "dry_run",
                "title": candidate.title,
                "url": f"https://dev.to/{self.client.username}/preview-simulation",
                "published": False,
                "category": candidate.category,
                "tags": candidate.tags,
            }

        # 3. Rate limit & cooldown gate
        can_pub, limit_msg = self._check_rate_limit()
        if not can_pub:
            logger.warning("Dev.to publish rate limit triggered: %s", limit_msg)
            return {"status": "rate_limited", "error": limit_msg}

        # 4. Anti-ban human review jitter pause (5.0s - 12.0s)
        jitter = self._apply_anti_ban_jitter(5.0, 12.0)

        # 5. Assemble API payload
        payload = {
            "title": candidate.title,
            "body_markdown": candidate.body,
            "published": published,
            "tags": candidate.tags[:4],
            "series": candidate.series,
            "description": candidate.description,
            "canonical_url": candidate.canonical_url,
        }

        # 6. Publish via API
        try:
            logger.info("Publishing article to Dev.to: '%s'...", candidate.title)
            res = self.client.create_article(payload)
            pub_id = res.get("id")
            pub_url = res.get("url") or f"https://dev.to/{self.client.username}/{res.get('slug', '')}"

            self._last_publish_ts = time.time()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._daily_counts[today] = self._daily_counts.get(today, 0) + 1

            logger.info("Successfully published Dev.to article: %s (ID: %s)", pub_url, pub_id)
            return {
                "status": "published",
                "id": pub_id,
                "title": candidate.title,
                "url": pub_url,
                "slug": res.get("slug"),
                "path": res.get("path"),
                "category": candidate.category,
                "tags": candidate.tags,
                "jitter": round(jitter, 2),
            }
        except DevtoAPIError as api_err:
            logger.error("Dev.to API publication failed: %s", api_err)
            return {"status": "error", "error": str(api_err)}
        except Exception as exc:
            logger.error("Unexpected error publishing to Dev.to: %s", exc)
            return {"status": "error", "error": str(exc)}

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

from automations.qiita.api import QiitaAPIClient, QiitaAPIError
from automations.qiita.articles import QiitaArticleCandidate, validate_article_content
from common.config import Settings

logger = logging.getLogger(__name__)


class QiitaRateLimitError(RuntimeError):
    """Raised when Qiita rate limits or publication cooldown bounds are exceeded."""
    pass


class QiitaPublisher:
    """
    Anti-ban safety and publication engine for Qiita.
    Enforces:
      - Anti-ban human typing and review jitter delays (5.0s to 12.0s)
      - Strict daily publication cap (maximum 1-2 daily posts)
      - Content safety, 80% Tech Japanese standards, and 0.1% rule validation gates
    """

    def __init__(
        self,
        settings: Settings,
        client: Optional[QiitaAPIClient] = None,
        min_cooldown_seconds: float = 300.0,
        max_daily_posts: int = 2,
    ):
        self.settings = settings
        self.client = client or QiitaAPIClient(settings)
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
        logger.info("Applying anti-ban human jitter pause (%.2fs) before Qiita action...", jitter)
        time.sleep(jitter)
        return jitter

    def publish_article(
        self,
        candidate: QiitaArticleCandidate,
        private: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Publishes a validated technical article to Qiita.
        Applies anti-ban jitter pauses and strict rate limits.
        """
        # 1. Content validation gate (length, Tech Japanese, diagram, code, 0.1% rule)
        is_valid, validation_msg = validate_article_content(candidate)
        if not is_valid:
            logger.error("Qiita article validation rejected: %s", validation_msg)
            return {"status": "error", "error": f"Content validation failed: {validation_msg}"}

        # 2. Dry run gate
        if dry_run:
            logger.info("[Dry-Run] Simulating Qiita article publish: '%s'", candidate.title)
            return {
                "status": "dry_run",
                "title": candidate.title,
                "category": candidate.category,
                "tags": candidate.tags,
                "private": private,
                "message": "Article simulated successfully (dry-run mode).",
            }

        # 3. Client configuration gate
        if not self.client.is_configured():
            logger.warning("Qiita API is not configured. Missing QIITA_ACCESS_TOKEN.")
            return {"status": "unconfigured", "error": "Missing Qiita access token"}

        # 4. Strict rate limit enforcement
        allowed, reason = self._check_rate_limit()
        if not allowed:
            logger.warning("Qiita rate limit blocked publication: %s", reason)
            return {"status": "rate_limited", "error": reason}

        # 5. Anti-ban human jitter delay (5.0s - 12.0s)
        self._apply_anti_ban_jitter(min_delay=5.0, max_delay=12.0)

        # 6. Execute live API publication
        try:
            res = self.client.create_item(
                title=candidate.title,
                body=candidate.body,
                tags=candidate.tags,
                private=private,
                tweet=False,
            )
            item_id = str(res.get("id") or "")
            item_url = str(res.get("url") or f"https://qiita.com/{self.client.username}/items/{item_id}")

            self._last_publish_ts = time.time()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._daily_counts[today] = self._daily_counts.get(today, 0) + 1

            logger.info("Successfully published live Qiita article! URL: %s", item_url)
            return {
                "status": "success",
                "item_id": item_id,
                "url": item_url,
                "title": res.get("title", candidate.title),
                "published": True,
                "private": private,
                "raw_response": res,
            }
        except QiitaAPIError as exc:
            logger.error("Qiita API error publishing article: %s", exc)
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            logger.error("Unexpected error publishing article to Qiita: %s", exc)
            return {"status": "error", "error": f"Unexpected error: {exc}"}

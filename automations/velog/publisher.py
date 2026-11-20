from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from automations.velog.api import VelogAPIClient, VelogAPIError
from automations.velog.articles import VelogArticleCandidate, validate_article_content
from common.config import Settings

logger = logging.getLogger(__name__)


class VelogRateLimitError(RuntimeError):
    """Raised when Velog rate limits or publication cooldown bounds are exceeded."""
    pass


class VelogPublisher:
    """
    Anti-ban safety and publication engine for Velog.
    Enforces:
      - Anti-ban human typing and review jitter delays (5.0s to 12.0s)
      - Strict daily publication cap (maximum 1-2 daily posts)
      - Content safety, 80% Tech Korean standards, and 0.1% rule validation gates
      - Clean URL slug generation and formatting
    """

    def __init__(
        self,
        settings: Settings,
        client: Optional[VelogAPIClient] = None,
        min_cooldown_seconds: float = 300.0,
        max_daily_posts: int = 2,
    ):
        self.settings = settings
        self.client = client or VelogAPIClient(settings)
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
        logger.info("Applying anti-ban human jitter pause (%.2fs) before Velog publication...", jitter)
        time.sleep(jitter)
        return jitter

    def _format_slug(self, raw_slug: str, title: str) -> str:
        """Formats or generates a URL-friendly slug."""
        base = raw_slug.strip().lower() if raw_slug else ""
        if not base:
            # Fallback: transliterate or sanitize title
            base = re.sub(r"[^a-zA-Z0-9\-]", "-", title.lower()).strip("-")
            base = re.sub(r"-+", "-", base)
        if not base:
            base = f"tech-post-{int(time.time())}"
        return base

    def publish_article(
        self,
        candidate: VelogArticleCandidate,
        is_temp: bool = False,
        is_private: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Publishes a validated technical article to Velog.
        Applies anti-ban jitter pauses and strict rate limits.
        """
        # 1. Content validation gate (length, Tech Korean, diagram, code, 0.1% rule)
        is_valid, validation_msg = validate_article_content(candidate)
        if not is_valid:
            logger.error("Velog article validation rejected: %s", validation_msg)
            return {"status": "error", "error": f"Content validation failed: {validation_msg}"}

        # 2. Dry run gate
        if dry_run:
            logger.info("[Dry-Run] Simulating Velog article publish: '%s'", candidate.title)
            return {
                "status": "dry_run",
                "title": candidate.title,
                "category": candidate.category,
                "tags": candidate.tags,
                "url_slug": candidate.url_slug,
                "is_temp": is_temp,
                "is_private": is_private,
                "message": "Article simulated successfully (dry-run mode).",
            }

        # 3. Client configuration gate
        if not self.client.is_configured():
            logger.warning("Velog API is not configured. Missing VELOG_ACCESS_TOKEN or VELOG_REFRESH_TOKEN.")
            return {"status": "unconfigured", "error": "Missing Velog authentication tokens."}

        # 4. Strict rate limit enforcement
        allowed, reason = self._check_rate_limit()
        if not allowed:
            logger.warning("Velog publish rate limit triggered: %s", reason)
            return {"status": "rate_limited", "error": reason}

        # 5. Anti-ban human review jitter (5.0s - 12.0s)
        jitter = self._apply_anti_ban_jitter(5.0, 12.0)

        # 6. Slug formatting
        slug = self._format_slug(candidate.url_slug, candidate.title)

        try:
            logger.info("Publishing article to Velog: '%s' (slug: %s)...", candidate.title, slug)
            res = self.client.write_post(
                title=candidate.title,
                body=candidate.body,
                tags=candidate.tags,
                url_slug=slug,
                is_markdown=True,
                is_temp=is_temp,
                is_private=is_private,
            )

            # Update rate-limit state
            now = time.time()
            self._last_publish_ts = now
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._daily_counts[today] = self._daily_counts.get(today, 0) + 1

            post_id = res.get("id") or ""
            actual_slug = res.get("url_slug") or slug
            user_handle = self.client.username or "aman179102"
            post_url = f"https://velog.io/@{user_handle}/{actual_slug}"

            logger.info("Successfully published Velog article: %s (ID: %s)", post_url, post_id)
            return {
                "status": "published",
                "id": post_id,
                "title": candidate.title,
                "url": post_url,
                "url_slug": actual_slug,
                "tags": candidate.tags,
                "category": candidate.category,
                "is_ai_generated": candidate.is_ai_generated,
                "jitter": round(jitter, 2),
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        except VelogAPIError as exc:
            logger.error("Velog API publication failed: %s", exc)
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            logger.error("Unexpected error during Velog publish: %s", exc)
            return {"status": "error", "error": str(exc)}

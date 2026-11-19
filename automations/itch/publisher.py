from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

from automations.itch.api import ItchAPIClient, ItchAPIError
from automations.itch.devlogs import ItchDevlogCandidate, validate_devlog_content
from common.config import Settings

logger = logging.getLogger(__name__)


class ItchRateLimitError(RuntimeError):
    """Raised when itch.io rate limits or cooldown bounds are exceeded."""
    pass


class ItchPublisher:
    """
    Handles publishing devlogs and systems updates to itch.io with:
      - Anti-ban human jitter pauses (5.0s to 10.0s)
      - Strict rate limit enforcement (minimum 300s between writes, max 2 devlogs/day)
      - Content safety & 0.1% rule validation
    """

    def __init__(
        self,
        settings: Settings,
        client: Optional[ItchAPIClient] = None,
        min_cooldown_seconds: float = 300.0,
        max_daily_posts: int = 2,
    ):
        self.settings = settings
        self.client = client or ItchAPIClient(settings)
        self.min_cooldown_seconds = min_cooldown_seconds
        self.max_daily_posts = max_daily_posts

        self._last_publish_ts: float = 0.0
        self._daily_counts: dict[str, int] = {}

    def _check_rate_limit(self) -> tuple[bool, str]:
        """Verifies if publishing is allowed under strict rate limit rules."""
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

    def _apply_anti_ban_jitter(self, min_delay: float = 5.0, max_delay: float = 10.0) -> float:
        """Executes natural human typing & review jitter pause (5.0s - 10.0s)."""
        jitter = random.uniform(min_delay, max_delay)
        logger.info("Applying anti-ban human jitter pause (%.2fs) before itch.io action...", jitter)
        time.sleep(jitter)
        return jitter

    def publish_devlog(
        self,
        candidate: ItchDevlogCandidate,
        game_id: Optional[str | int] = None,
        draft: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Publishes or drafts a devlog post to itch.io.
        Applies anti-ban jitter pauses and strict rate limits.
        """
        # 1. Content validation (length, diagram, 0.1% rule)
        is_valid, validation_msg = validate_devlog_content(candidate)
        if not is_valid:
            logger.error("Devlog validation rejected: %s", validation_msg)
            return {"status": "error", "error": f"Content validation failed: {validation_msg}"}

        # 2. Dry run simulation
        if dry_run:
            logger.info("[Dry-Run] Simulating devlog publish: '%s'", candidate.title)
            return {
                "status": "dry_run",
                "title": candidate.title,
                "game_id": game_id or "simulated",
                "draft": draft,
                "message": "Devlog simulated successfully (dry-run mode).",
            }

        # 3. Client configuration check
        if not self.client.is_configured():
            logger.warning("itch.io API is not configured. Missing ITCH_API_KEY.")
            return {"status": "unconfigured", "error": "Missing itch.io API key"}

        # 4. Strict rate limit enforcement
        allowed, reason = self._check_rate_limit()
        if not allowed:
            logger.warning("itch.io publish blocked by rate limit: %s", reason)
            return {"status": "rate_limited", "error": reason}

        # 5. Determine target game ID
        target_id = game_id or candidate.target_game_id
        if not target_id:
            try:
                games = self.client.get_my_games()
                if games:
                    target_id = games[0].get("id")
            except Exception as exc:
                logger.warning("Could not auto-detect game ID from account: %s", exc)

        if not target_id:
            target_id = "default"

        # 6. Anti-ban human jitter pause (5.0s - 10.0s)
        self._apply_anti_ban_jitter(5.0, 10.0)

        # 7. Execute publish
        try:
            res = self.client.create_devlog(
                game_id=target_id,
                title=candidate.title,
                body=candidate.body,
                draft=draft,
            )

            # Record success in rate limit tracker
            now = time.time()
            self._last_publish_ts = now
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._daily_counts[today] = self._daily_counts.get(today, 0) + 1

            return {
                "status": "success",
                "title": candidate.title,
                "game_id": target_id,
                "devlog_id": res.get("devlog_id"),
                "url": res.get("url"),
                "draft": res.get("draft", draft),
                "response": res,
            }
        except ItchAPIError as exc:
            logger.error("Failed to publish itch.io devlog '%s': %s", candidate.title, exc)
            return {"status": "error", "error": str(exc)}

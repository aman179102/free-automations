from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from common.config import Settings
from common.state import load_state, record_action, save_state

logger = logging.getLogger(__name__)

HN_BASE_URL = "https://news.ycombinator.com"


class HNPublisher:
    """
    Autonomous Hacker News Publisher.
    Handles automated login, direct comment submission, and direct story submission
    without requiring any manual button clicks.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.username = getattr(settings, "hn_username", "") or ""
        self.password = getattr(settings, "hn_password", "") or ""
        self.cookie = getattr(settings, "hn_cookie", "") or ""
        self.client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": HN_BASE_URL,
            },
            follow_redirects=True,
            timeout=25.0,
        )
        if self.cookie:
            self.client.cookies.set("user", self.cookie, domain="news.ycombinator.com")

    def _get_state_file(self) -> Path:
        raw_dir = getattr(self.settings, "state_dir", "state") if self.settings else "state"
        p = Path(raw_dir)
        if p.is_dir() or not p.suffix:
            return p / "hn_state.json"
        return p

    def is_logged_in(self) -> bool:
        """Verifies if the current session is logged in to Hacker News."""
        try:
            resp = self.client.get(HN_BASE_URL)
            if getattr(resp, "status_code", 0) == 200:
                raw_text = resp.text if isinstance(getattr(resp, "text", None), str) else ""
                soup = BeautifulSoup(raw_text, "html.parser")
                logout_link = soup.find("a", href=lambda h: h and "logout" in h)
                return bool(logout_link)
        except Exception as exc:
            logger.warning("Failed to check HN login status: %s", exc)
        return False

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """
        Logs in to news.ycombinator.com and stores the session cookie.
        """
        user = username or self.username
        pwd = password or self.password

        if not user or not pwd:
            logger.warning("No HN credentials configured (HN_USERNAME / HN_PASSWORD missing).")
            return False

        try:
            resp = self.client.post(
                f"{HN_BASE_URL}/login",
                data={
                    "acct": user,
                    "pw": pwd,
                    "goto": "news",
                },
            )
            # Check if login succeeded
            raw_text = resp.text if isinstance(getattr(resp, "text", None), str) else ""
            if "bad login" in raw_text.lower() or "unknown user" in raw_text.lower():
                logger.error("Hacker News login failed. Bad credentials or account not found.")
                return False

            soup = BeautifulSoup(raw_text, "html.parser")
            logout_link = soup.find("a", href=lambda h: h and "logout" in h)
            resp_cookies = getattr(resp, "cookies", {})
            if logout_link or "user" in resp_cookies:
                logger.info("Successfully logged in to Hacker News as '%s'!", user)
                # Save session cookie to state
                cookie_val = resp_cookies.get("user") or self.client.cookies.get("user")
                if cookie_val:
                    state_path = self._get_state_file()
                    state = load_state(state_path)
                    state["hn_cookie"] = cookie_val
                    save_state(state_path, state)
                return True
            else:
                logger.error("Hacker News login failed. Bad credentials or account not found.")
                return False
        except Exception as exc:
            logger.error("Hacker News login error: %s", exc)
            return False

    def ensure_authenticated(self) -> bool:
        """Ensures an active authenticated session exists, logging in if needed."""
        if self.is_logged_in():
            return True
        return self.login()

    def post_comment(self, item_id: int, comment_text: str) -> bool:
        """
        Autonomously posts a comment directly to a Hacker News story or comment.
        Extracts the required HMAC token from the item page form.
        """
        if not self.ensure_authenticated():
            logger.warning("Cannot post comment: Not authenticated on Hacker News.")
            return False

        item_url = f"{HN_BASE_URL}/item?id={item_id}"
        try:
            # 1. Fetch item page to extract hmac
            resp = self.client.get(item_url)
            if getattr(resp, "status_code", 0) != 200:
                logger.warning("Failed to fetch item page #%d (status %s)", item_id, getattr(resp, "status_code", "unknown"))
                return False

            raw_text = resp.text if isinstance(getattr(resp, "text", None), str) else ""
            soup = BeautifulSoup(raw_text, "html.parser")
            form = soup.find("form", action="comment")
            if not form:
                # Thread might be locked, dead, or too old
                logger.warning("No comment form found for item #%d (thread may be locked/archived).", item_id)
                return False

            hmac_input = form.find("input", {"name": "hmac"})
            parent_input = form.find("input", {"name": "parent"})
            if not hmac_input:
                logger.warning("HMAC input missing from comment form on item #%d", item_id)
                return False

            hmac_val = str(hmac_input.get("value", ""))
            parent_val = str(parent_input.get("value", str(item_id)))

            # Human-like typing delay to avoid sub-second bot detection
            delay = random.uniform(3.5, 7.5)
            logger.info("Natural typing pause (%.1fs) before submitting comment on #%d...", delay, item_id)
            time.sleep(delay)

            # 2. Submit the comment
            comment_resp = self.client.post(
                f"{HN_BASE_URL}/comment",
                data={
                    "parent": parent_val,
                    "goto": f"item?id={item_id}",
                    "hmac": hmac_val,
                    "text": comment_text,
                },
            )
            # Successful submission redirects back to item or returns 200
            if getattr(comment_resp, "status_code", 0) in (200, 302):
                logger.info("Successfully posted comment autonomously on HN item #%d!", item_id)
                return True
            else:
                logger.warning("Comment submission returned status %s", getattr(comment_resp, "status_code", "unknown"))
                return False
        except Exception as exc:
            logger.error("Failed to post comment to item #%d: %s", item_id, exc)
            return False

    def submit_story(self, title: str, text: str = "", url: str = "") -> Optional[str]:
        """
        Autonomously creates a new story submission (Show HN / Ask HN / URL) on Hacker News.
        """
        if not self.ensure_authenticated():
            logger.warning("Cannot submit story: Not authenticated on Hacker News.")
            return None

        submit_url = f"{HN_BASE_URL}/submit"
        try:
            resp = self.client.get(submit_url)
            if getattr(resp, "status_code", 0) != 200:
                logger.warning("Failed to load submit page (status %s)", getattr(resp, "status_code", "unknown"))
                return None

            raw_text = resp.text if isinstance(getattr(resp, "text", None), str) else ""
            soup = BeautifulSoup(raw_text, "html.parser")
            form = soup.find("form", action="r")
            if not form:
                logger.warning("Submit form not found on submit page.")
                return None

            fnid_input = form.find("input", {"name": "fnid"})
            fnop_input = form.find("input", {"name": "fnop"})
            if not fnid_input:
                logger.warning("fnid token missing from submit form.")
                return None

            fnid = str(fnid_input.get("value", ""))
            fnop = str(fnop_input.get("value", "submit-page"))

            submit_data = {
                "fnid": fnid,
                "fnop": fnop,
                "title": title,
                "url": url,
                "text": text,
            }

            # Human-like typing delay to avoid sub-second bot detection
            delay = random.uniform(5.0, 10.0)
            logger.info("Natural pause (%.1fs) before submitting Show HN story...", delay)
            time.sleep(delay)

            post_resp = self.client.post(f"{HN_BASE_URL}/r", data=submit_data)
            if getattr(post_resp, "status_code", 0) in (200, 302):
                logger.info("Successfully submitted new story '%s' to Hacker News!", title)
                return str(getattr(post_resp, "url", f"{HN_BASE_URL}/newest"))
            return None
        except Exception as exc:
            logger.error("Failed to submit story '%s': %s", title, exc)
            return None

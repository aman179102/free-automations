from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from authlib.integrations.httpx_client import OAuth1Auth

from common.config import Settings

logger = logging.getLogger(__name__)

TUMBLR_API_BASE = "https://api.tumblr.com/v2"


class TumblrAPIError(RuntimeError):
    """Raised when the Tumblr API returns an error."""
    pass


class TumblrAPIClient:
    """
    Official Tumblr API v2 Client using OAuth 1.0a authentication.
    Handles user info, creating posts (published or queued), reblogging with commentary,
    and exploring tagged community posts.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.consumer_key = getattr(settings, "tumblr_consumer_key", "") or ""
        self.consumer_secret = getattr(settings, "tumblr_consumer_secret", "") or ""
        self.oauth_token = getattr(settings, "tumblr_oauth_token", "") or ""
        self.oauth_token_secret = getattr(settings, "tumblr_oauth_token_secret", "") or ""
        self.blog_name = getattr(settings, "tumblr_blog_name", "") or ""

        self._auth: Optional[OAuth1Auth] = None
        if self.is_configured():
            self._auth = OAuth1Auth(
                client_id=self.consumer_key,
                client_secret=self.consumer_secret,
                token=self.oauth_token,
                token_secret=self.oauth_token_secret,
            )

        transport = httpx.HTTPTransport(retries=3)
        self.client = httpx.Client(
            base_url=TUMBLR_API_BASE,
            timeout=30.0,
            transport=transport,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            },
        )

    def is_configured(self) -> bool:
        """Returns True if all OAuth credentials are provided."""
        return bool(
            self.consumer_key
            and self.consumer_secret
            and self.oauth_token
            and self.oauth_token_secret
        )

    def _normalize_blog_id(self, blog_identifier: Optional[str] = None) -> str:
        """Normalizes blog name to '{name}.tumblr.com' if not already suffixed."""
        name = (blog_identifier or self.blog_name).strip()
        if not name:
            raise TumblrAPIError("Tumblr blog_name is not configured.")
        if "." not in name:
            return f"{name}.tumblr.com"
        return name

    def get_user_info(self) -> dict[str, Any]:
        """Fetches the authenticated user's account info and blog roster."""
        if not self._auth:
            raise TumblrAPIError("Tumblr API client is not authenticated (missing OAuth credentials).")
        resp = self.client.get("/user/info", auth=self._auth)
        if resp.status_code != 200:
            logger.error("Failed to get Tumblr user info (%d): %s", resp.status_code, resp.text)
            raise TumblrAPIError(f"HTTP {resp.status_code}: {resp.text}")
        data = resp.json().get("response", {})
        return data

    def verify_credentials(self) -> bool:
        """Checks whether the configured OAuth tokens are valid and active."""
        if not self.is_configured():
            return False
        try:
            info = self.get_user_info()
            user = info.get("user", {})
            return bool(user.get("name"))
        except Exception as exc:
            logger.warning("Tumblr credentials verification failed: %s", exc)
            return False

    def create_text_post(
        self,
        title: str,
        body: str,
        tags: list[str] | None = None,
        state: str = "published",
        blog_identifier: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Submits a text/markdown post directly to the specified Tumblr blog.
        Supports states: 'published' or 'queue'.
        """
        if not self._auth:
            raise TumblrAPIError("Cannot create post: Tumblr client is not authenticated.")

        blog_id = self._normalize_blog_id(blog_identifier)
        endpoint = f"/blog/{blog_id}/post"

        data: dict[str, Any] = {
            "type": "text",
            "format": "markdown",
            "title": title,
            "body": body,
            "state": state,
        }
        if tags:
            # Clean and sanitize tags (strip '#' and leading/trailing whitespace)
            clean_tags = [t.lstrip("#").strip() for t in tags if t.strip()]
            data["tags"] = ",".join(clean_tags)

        resp = self.client.post(endpoint, data=data, auth=self._auth)
        if resp.status_code not in (200, 201):
            logger.error("Failed to create Tumblr post on '%s' (%d): %s", blog_id, resp.status_code, resp.text)
            raise TumblrAPIError(f"HTTP {resp.status_code}: {resp.text}")

        res_data = resp.json().get("response", {})
        logger.info("Successfully created Tumblr post (id=%s, state=%s) on %s", res_data.get("id"), state, blog_id)
        return res_data

    def reblog_post(
        self,
        post_id: int | str,
        reblog_key: str,
        comment: str,
        tags: list[str] | None = None,
        state: str = "published",
        blog_identifier: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Reblogs an existing community post with high-signal engineering commentary.
        """
        if not self._auth:
            raise TumblrAPIError("Cannot reblog: Tumblr client is not authenticated.")

        blog_id = self._normalize_blog_id(blog_identifier)
        endpoint = f"/blog/{blog_id}/post/reblog"

        data: dict[str, Any] = {
            "id": str(post_id),
            "reblog_key": reblog_key,
            "comment": comment,
            "state": state,
        }
        if tags:
            clean_tags = [t.lstrip("#").strip() for t in tags if t.strip()]
            data["tags"] = ",".join(clean_tags)

        resp = self.client.post(endpoint, data=data, auth=self._auth)
        if resp.status_code not in (200, 201):
            logger.error("Failed to reblog Tumblr post #%s (%d): %s", post_id, resp.status_code, resp.text)
            raise TumblrAPIError(f"HTTP {resp.status_code}: {resp.text}")

        res_data = resp.json().get("response", {})
        logger.info("Successfully reblogged post #%s on %s", post_id, blog_id)
        return res_data

    def get_tagged_posts(self, tag: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Discovers recent public posts tagged with a specific keyword.
        Uses api_key (consumer_key) authentication.
        """
        if not self.consumer_key:
            return []

        clean_tag = tag.lstrip("#").strip()
        params = {
            "api_key": self.consumer_key,
            "tag": clean_tag,
            "limit": min(limit, 20),
        }
        try:
            resp = self.client.get("/tagged", params=params)
            if resp.status_code == 200:
                posts = resp.json().get("response", [])
                return posts if isinstance(posts, list) else []
            logger.warning("Tumblr tagged posts query failed (%d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("Error fetching tagged Tumblr posts for #%s: %s", clean_tag, exc)
        return []

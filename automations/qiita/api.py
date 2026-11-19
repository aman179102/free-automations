from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.config import Settings

logger = logging.getLogger(__name__)

QIITA_API_BASE = "https://qiita.com/api/v2"


class QiitaAPIError(RuntimeError):
    """Raised when a Qiita API v2 request fails or returns an error."""
    pass


class QiitaAPIClient:
    """
    Official Qiita API v2 Client.
    Supports requests.Session with Bearer token authentication and automatic backoff retries for:
      - /api/v2/authenticated_user (GET profile verification)
      - /api/v2/items (POST article publication)
      - /api/v2/authenticated_user/items (GET author articles & metrics)
      - /api/v2/tags/{tag_id}/items (GET tag radar & trending articles)
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        access_token: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ):
        self.settings = settings
        self.access_token = (
            access_token
            or (getattr(settings, "qiita_access_token", "") if settings else "")
            or ""
        ).strip()
        self.username = (
            (getattr(settings, "qiita_username", "") if settings else "")
            or "aman179102"
        ).strip()

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            retries = Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retries)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
                "FreeAutomations-QiitaEngine/1.0 (+https://github.com/aman179102)"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if self.access_token:
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"

    def is_configured(self) -> bool:
        """Returns True if Qiita access token is present."""
        return bool(self.access_token)

    def _build_url(self, endpoint: str) -> str:
        """Builds a full URL for a Qiita API v2 endpoint."""
        return f"{QIITA_API_BASE}/{endpoint.lstrip('/')}"

    def verify_credentials(self) -> bool:
        """Verifies if the access token is valid and active."""
        if not self.is_configured():
            return False
        try:
            user = self.get_authenticated_user()
            return bool(user and user.get("id"))
        except Exception as exc:
            logger.warning("Qiita credentials verification failed: %s", exc)
            return False

    def get_authenticated_user(self) -> dict[str, Any]:
        """
        Fetches the authenticated user profile from GET /api/v2/authenticated_user.
        """
        if not self.is_configured():
            raise QiitaAPIError("Qiita access token is not configured.")

        url = self._build_url("authenticated_user")
        try:
            resp = self.session.get(url, timeout=15)
        except Exception as exc:
            raise QiitaAPIError(f"Network error querying Qiita user profile: {exc}") from exc

        if resp.status_code >= 400:
            raise QiitaAPIError(
                f"Qiita API error (HTTP {resp.status_code}) fetching profile: {resp.text[:200]}"
            )

        try:
            data = resp.json()
            if isinstance(data, dict):
                return data
            raise QiitaAPIError(f"Unexpected response format from Qiita: {data}")
        except Exception as exc:
            raise QiitaAPIError(f"Failed to parse Qiita response JSON: {exc}") from exc

    def create_item(
        self,
        title: str,
        body: str,
        tags: list[dict[str, Any]] | list[str],
        private: bool = False,
        tweet: bool = False,
        coedit: bool = False,
    ) -> dict[str, Any]:
        """
        Publishes a new technical article to POST /api/v2/items.
        tags format: list of {"name": str, "versions": list[str]} or list of str.
        Qiita requires between 1 and 5 tags.
        """
        if not self.is_configured():
            raise QiitaAPIError("Qiita access token is not configured.")

        formatted_tags: list[dict[str, Any]] = []
        for tag in tags:
            if isinstance(tag, str):
                formatted_tags.append({"name": tag.strip(), "versions": []})
            elif isinstance(tag, dict) and "name" in tag:
                formatted_tags.append({
                    "name": str(tag["name"]).strip(),
                    "versions": tag.get("versions", []),
                })

        if not formatted_tags:
            formatted_tags = [{"name": "Python", "versions": []}]
        # Qiita allows maximum 5 tags per article
        formatted_tags = formatted_tags[:5]

        payload = {
            "title": title.strip(),
            "body": body.strip(),
            "tags": formatted_tags,
            "private": bool(private),
            "tweet": bool(tweet),
            "coedit": bool(coedit),
        }

        url = self._build_url("items")
        try:
            resp = self.session.post(url, json=payload, timeout=20)
        except Exception as exc:
            raise QiitaAPIError(f"Network error creating Qiita item: {exc}") from exc

        if resp.status_code >= 400:
            raise QiitaAPIError(
                f"Qiita API error (HTTP {resp.status_code}) creating item: {resp.text[:300]}"
            )

        try:
            data = resp.json()
            if isinstance(data, dict):
                return data
            raise QiitaAPIError(f"Unexpected response format creating item: {data}")
        except Exception as exc:
            raise QiitaAPIError(f"Failed to parse Qiita response JSON: {exc}") from exc

    def get_authenticated_user_items(
        self,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Fetches the authenticated user's articles from GET /api/v2/authenticated_user/items.
        """
        if not self.is_configured():
            raise QiitaAPIError("Qiita access token is not configured.")

        url = self._build_url("authenticated_user/items")
        params = {"page": max(1, page), "per_page": min(100, max(1, per_page))}
        try:
            resp = self.session.get(url, params=params, timeout=15)
        except Exception as exc:
            raise QiitaAPIError(f"Network error fetching user items: {exc}") from exc

        if resp.status_code >= 400:
            raise QiitaAPIError(
                f"Qiita API error (HTTP {resp.status_code}) fetching user items: {resp.text[:200]}"
            )

        try:
            data = resp.json()
            if isinstance(data, list):
                return data
            raise QiitaAPIError(f"Expected list of items from Qiita, got: {type(data)}")
        except Exception as exc:
            raise QiitaAPIError(f"Failed to parse Qiita response JSON: {exc}") from exc

    def get_tag_items(
        self,
        tag_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Fetches articles tagged with tag_id from GET /api/v2/tags/{tag_id}/items.
        """
        clean_tag = quote(tag_id.strip())
        url = self._build_url(f"tags/{clean_tag}/items")
        params = {"page": max(1, page), "per_page": min(100, max(1, per_page))}
        try:
            resp = self.session.get(url, params=params, timeout=15)
        except Exception as exc:
            raise QiitaAPIError(f"Network error fetching tag items for '{tag_id}': {exc}") from exc

        if resp.status_code >= 400:
            raise QiitaAPIError(
                f"Qiita API error (HTTP {resp.status_code}) fetching tag items: {resp.text[:200]}"
            )

        try:
            data = resp.json()
            if isinstance(data, list):
                return data
            raise QiitaAPIError(f"Expected list of items for tag '{tag_id}', got: {type(data)}")
        except Exception as exc:
            raise QiitaAPIError(f"Failed to parse Qiita response JSON: {exc}") from exc

    def get_item(self, item_id: str) -> dict[str, Any]:
        """
        Fetches a specific article by ID from GET /api/v2/items/{item_id}.
        """
        clean_id = quote(item_id.strip())
        url = self._build_url(f"items/{clean_id}")
        try:
            resp = self.session.get(url, timeout=15)
        except Exception as exc:
            raise QiitaAPIError(f"Network error fetching item '{item_id}': {exc}") from exc

        if resp.status_code >= 400:
            raise QiitaAPIError(
                f"Qiita API error (HTTP {resp.status_code}) fetching item '{item_id}': {resp.text[:200]}"
            )

        try:
            data = resp.json()
            if isinstance(data, dict):
                return data
            raise QiitaAPIError(f"Expected dict for item '{item_id}', got: {type(data)}")
        except Exception as exc:
            raise QiitaAPIError(f"Failed to parse Qiita response JSON: {exc}") from exc

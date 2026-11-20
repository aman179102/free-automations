from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.config import Settings

logger = logging.getLogger(__name__)

DEVTO_API_BASE = "https://dev.to/api"


class DevtoAPIError(RuntimeError):
    """Raised when a Dev.to Forem API v1 request fails or returns an error."""
    pass


class DevtoAPIClient:
    """
    Official Dev.to Forem API v1 Client (https://dev.to/api).
    Supports requests.Session with:
      - Header 'api-key': Personal API token
      - Header 'accept': application/vnd.forem.api-v1+json
      - Header 'Content-Type': application/json
      - Automatic exponential backoff retries for 429, 500, 502, 503, 504.

    Core Endpoints:
      - get_me (GET /api/users/me): User profile & authentication check
      - create_article (POST /api/articles): Publishes technical article
      - get_my_articles (GET /api/articles/me): Author's published & draft articles + stats
      - get_articles (GET /api/articles): Tech trend radar & category scanning
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ):
        self.settings = settings
        self.api_key = (
            api_key
            or (getattr(settings, "devto_api_key", "") if settings else "")
            or ""
        ).strip()
        self.username = (
            username
            or (getattr(settings, "devto_username", "") if settings else "")
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
                "FreeAutomations-DevtoEngine/1.0 (+https://github.com/aman179102)"
            ),
            "accept": "application/vnd.forem.api-v1+json",
            "Content-Type": "application/json",
        })
        if self.api_key:
            self.session.headers["api-key"] = self.api_key

    def is_configured(self) -> bool:
        """Returns True if Dev.to API key is present."""
        return bool(self.api_key)

    def _build_url(self, endpoint: str) -> str:
        """Constructs full Dev.to API v1 URL."""
        return f"{DEVTO_API_BASE}/{endpoint.lstrip('/')}"

    def verify_credentials(self) -> bool:
        """Verifies if the configured API key is valid and active."""
        if not self.is_configured():
            return False
        try:
            me = self.get_me()
            return bool(me and (me.get("id") or me.get("username")))
        except Exception as exc:
            logger.warning("Dev.to credentials verification failed: %s", exc)
            return False

    def get_me(self) -> dict[str, Any]:
        """
        Fetches the authenticated user profile from GET /api/users/me.
        """
        if not self.is_configured():
            raise DevtoAPIError("Dev.to API key is not configured.")

        url = self._build_url("users/me")
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 401:
                raise DevtoAPIError(f"HTTP 401 Unauthorized: Invalid Dev.to API key.")
            if resp.status_code != 200:
                raise DevtoAPIError(f"HTTP {resp.status_code}: {resp.text}")
            data = resp.json()
            if not isinstance(data, dict):
                raise DevtoAPIError(f"Unexpected response format from get_me: {data}")
            return data
        except requests.RequestException as exc:
            raise DevtoAPIError(f"Dev.to get_me network failure: {exc}") from exc

    def create_article(self, article: dict[str, Any]) -> dict[str, Any]:
        """
        Publishes a new article via POST /api/articles.
        Accepts dictionary containing either the raw article attributes or
        wrapped in an 'article' key.
        """
        if not self.is_configured():
            raise DevtoAPIError("Dev.to API key is not configured.")

        url = self._build_url("articles")
        payload = article if "article" in article else {"article": article}

        try:
            resp = self.session.post(url, json=payload, timeout=20)
            if resp.status_code in (200, 201):
                data = resp.json()
                if not isinstance(data, dict):
                    raise DevtoAPIError(f"Unexpected response format from create_article: {data}")
                return data

            err_msg = resp.text
            try:
                err_json = resp.json()
                if isinstance(err_json, dict) and "error" in err_json:
                    err_msg = err_json["error"]
            except Exception:
                pass
            raise DevtoAPIError(f"HTTP {resp.status_code} on create_article: {err_msg}")
        except requests.RequestException as exc:
            raise DevtoAPIError(f"Dev.to create_article network failure: {exc}") from exc

    def get_my_articles(self, page: int = 1, per_page: int = 30) -> list[dict[str, Any]]:
        """
        Fetches author's articles via GET /api/articles/me.
        Includes unpublished and published articles with metric counts.
        """
        if not self.is_configured():
            raise DevtoAPIError("Dev.to API key is not configured.")

        url = self._build_url("articles/me")
        params = {"page": max(1, page), "per_page": min(1000, max(1, per_page))}

        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                raise DevtoAPIError(f"HTTP {resp.status_code} on get_my_articles: {resp.text}")
            data = resp.json()
            if not isinstance(data, list):
                raise DevtoAPIError(f"Unexpected format from get_my_articles: {type(data)}")
            return data
        except requests.RequestException as exc:
            raise DevtoAPIError(f"Dev.to get_my_articles network failure: {exc}") from exc

    def get_articles(
        self,
        tag: Optional[str] = None,
        username: Optional[str] = None,
        state: Optional[str] = None,
        top: Optional[int] = None,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """
        Fetches public articles via GET /api/articles with filtering.
        """
        url = self._build_url("articles")
        params: dict[str, Any] = {
            "page": max(1, page),
            "per_page": min(1000, max(1, per_page)),
        }
        if tag:
            params["tag"] = tag.strip().lstrip("#")
        if username:
            params["username"] = username.strip()
        if state:
            params["state"] = state.strip()
        if top is not None:
            params["top"] = top

        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                raise DevtoAPIError(f"HTTP {resp.status_code} on get_articles: {resp.text}")
            data = resp.json()
            if not isinstance(data, list):
                raise DevtoAPIError(f"Unexpected format from get_articles: {type(data)}")
            return data
        except requests.RequestException as exc:
            raise DevtoAPIError(f"Dev.to get_articles network failure: {exc}") from exc

    def get_article(self, article_id: int | str) -> dict[str, Any]:
        """
        Fetches a single public article by ID via GET /api/articles/{id}.
        """
        url = self._build_url(f"articles/{article_id}")
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                raise DevtoAPIError(f"HTTP {resp.status_code} on get_article: {resp.text}")
            return resp.json()
        except requests.RequestException as exc:
            raise DevtoAPIError(f"Dev.to get_article network failure: {exc}") from exc

    def update_article(self, article_id: int | str, article: dict[str, Any]) -> dict[str, Any]:
        """
        Updates an existing article via PUT /api/articles/{id}.
        """
        if not self.is_configured():
            raise DevtoAPIError("Dev.to API key is not configured.")

        url = self._build_url(f"articles/{article_id}")
        payload = article if "article" in article else {"article": article}

        try:
            resp = self.session.put(url, json=payload, timeout=20)
            if resp.status_code not in (200, 201):
                raise DevtoAPIError(f"HTTP {resp.status_code} on update_article: {resp.text}")
            return resp.json()
        except requests.RequestException as exc:
            raise DevtoAPIError(f"Dev.to update_article network failure: {exc}") from exc

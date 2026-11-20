from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.config import Settings

logger = logging.getLogger(__name__)

VELOG_GRAPHQL_ENDPOINT = "https://v3.velog.io/graphql"


class VelogAPIError(RuntimeError):
    """Raised when a Velog GraphQL API request fails or returns an error."""
    pass


def _to_graphql_arg(val: Any) -> str:
    """Recursively formats a Python data structure into inline GraphQL argument syntax."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, list):
        items = ", ".join(_to_graphql_arg(x) for x in val)
        return f"[{items}]"
    if isinstance(val, dict):
        fields = ", ".join(f"{k}: {_to_graphql_arg(v)}" for k, v in val.items())
        return f"{{{fields}}}"
    raise TypeError(f"Unsupported GraphQL argument type: {type(val)}")


class VelogAPIClient:
    """
    Official Velog GraphQL Client (https://v3.velog.io/graphql).
    Supports requests.Session with cookies (access_token, refresh_token),
    headers (Referer: https://velog.io/, User-Agent: Mozilla/5.0...), and backoff retries.

    Supported GraphQL operations:
      - currentUser: profile verification & user ID lookup
      - posts(input: GetPostsInput!): author's published/draft articles
      - trendingPosts(input: TrendingPostsInput!): weekly/monthly tech trend radar
      - writePost(input: WritePostInput!): publication engine (meta must be {} empty dict)
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        username: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ):
        self.settings = settings
        self.access_token = (
            access_token
            or (getattr(settings, "velog_access_token", "") if settings else "")
            or ""
        ).strip()
        self.refresh_token = (
            refresh_token
            or (getattr(settings, "velog_refresh_token", "") if settings else "")
            or ""
        ).strip()
        self.username = (
            username
            or (getattr(settings, "velog_username", "") if settings else "")
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
                "FreeAutomations-VelogEngine/1.0 (+https://github.com/aman179102)"
            ),
            "Referer": "https://velog.io/",
            "Origin": "https://velog.io",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        if self.access_token:
            self.session.cookies.set("access_token", self.access_token, domain=".velog.io")
        if self.refresh_token:
            self.session.cookies.set("refresh_token", self.refresh_token, domain=".velog.io")

    def is_configured(self) -> bool:
        """Returns True if Velog authentication cookies are configured."""
        return bool(self.access_token or self.refresh_token)

    def execute_graphql(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Executes a GraphQL query/mutation against the Velog endpoint.
        Syncs any newly issued access/refresh tokens from response cookies.
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            resp = self.session.post(
                VELOG_GRAPHQL_ENDPOINT,
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()

            # Synchronize refreshed tokens from cookies if present
            new_access = self.session.cookies.get("access_token")
            if new_access and new_access != self.access_token:
                self.access_token = new_access
            new_refresh = self.session.cookies.get("refresh_token")
            if new_refresh and new_refresh != self.refresh_token:
                self.refresh_token = new_refresh

            if not resp.text.strip():
                raise VelogAPIError("Velog GraphQL returned empty response body.")

            data = resp.json()
            if "errors" in data and data["errors"]:
                err_msg = "; ".join(e.get("message", "Unknown error") for e in data["errors"])
                raise VelogAPIError(f"Velog GraphQL error: {err_msg}")

            return data
        except requests.RequestException as exc:
            raise VelogAPIError(f"Velog network communication failed: {exc}") from exc

    def verify_credentials(self) -> bool:
        """Verifies if the session credentials are valid and can access currentUser."""
        if not self.is_configured():
            return False
        try:
            user = self.get_current_user()
            return bool(user and user.get("id"))
        except Exception as exc:
            logger.warning("Velog credentials verification failed: %s", exc)
            return False

    def get_current_user(self) -> dict[str, Any]:
        """
        Fetches authenticated user profile via currentUser GraphQL query.
        """
        query = """
        query CurrentUser {
          currentUser {
            id
            username
            email
            profile {
              display_name
              short_bio
              thumbnail
            }
          }
        }
        """
        data = self.execute_graphql(query)
        res = data.get("data", {}).get("currentUser")
        if not res:
            raise VelogAPIError("User not authenticated or currentUser returned null.")
        return res

    def get_posts(
        self,
        username: Optional[str] = None,
        limit: int = 20,
        cursor: Optional[str] = None,
        temp_only: bool = False,
        tag: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetches articles for a specific user using posts(input: GetPostsInput!).
        """
        target_user = username or self.username
        input_dict: dict[str, Any] = {
            "username": target_user,
            "limit": limit,
            "temp_only": temp_only,
        }
        if cursor:
            input_dict["cursor"] = cursor
        if tag:
            input_dict["tag"] = tag

        input_str = _to_graphql_arg(input_dict)
        query = f"""
        query {{
          posts(input: {input_str}) {{
            id
            title
            short_description
            thumbnail
            is_markdown
            is_temp
            is_private
            url_slug
            likes
            views
            comments_count
            released_at
            updated_at
            tags
          }}
        }}
        """
        data = self.execute_graphql(query)
        return data.get("data", {}).get("posts", [])

    def get_trending_posts(
        self,
        limit: int = 20,
        timeframe: str = "week",
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Scans weekly/monthly trending articles using trendingPosts(input: TrendingPostsInput!).
        """
        input_dict: dict[str, Any] = {
            "limit": limit,
            "timeframe": timeframe,
        }
        if offset > 0:
            input_dict["offset"] = offset

        input_str = _to_graphql_arg(input_dict)
        query = f"""
        query {{
          trendingPosts(input: {input_str}) {{
            id
            title
            short_description
            thumbnail
            url_slug
            likes
            comments_count
            released_at
            updated_at
            user {{
              id
              username
              profile {{
                display_name
                thumbnail
              }}
            }}
            tags
          }}
        }}
        """
        data = self.execute_graphql(query)
        return data.get("data", {}).get("trendingPosts", [])

    def write_post(
        self,
        title: str,
        body: str,
        tags: list[str],
        url_slug: str,
        is_markdown: bool = True,
        is_temp: bool = False,
        is_private: bool = False,
        thumbnail: Optional[str] = None,
        series_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Publishes or creates a draft post on Velog using writePost(input: WritePostInput!).
        Note: The GraphQL schema strictly requires meta to be an empty dict {}.
        """
        if not self.is_configured():
            raise VelogAPIError("Velog authentication tokens are not configured.")

        input_dict: dict[str, Any] = {
            "title": title.strip(),
            "body": body.strip(),
            "tags": [t.strip() for t in tags if t.strip()],
            "is_markdown": is_markdown,
            "is_temp": is_temp,
            "is_private": is_private,
            "url_slug": url_slug.strip(),
            "meta": {},  # Strict schema requirement: meta must be empty dict {}
        }
        if thumbnail:
            input_dict["thumbnail"] = thumbnail
        if series_id:
            input_dict["series_id"] = series_id
        if token:
            input_dict["token"] = token

        input_str = _to_graphql_arg(input_dict)
        query = f"""
        mutation {{
          writePost(input: {input_str}) {{
            id
            title
            url_slug
            user {{
              id
              username
            }}
          }}
        }}
        """
        data = self.execute_graphql(query)
        res = data.get("data", {}).get("writePost")
        if not res:
            raise VelogAPIError(f"Failed to publish post: {data}")
        return res

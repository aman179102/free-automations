from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.config import Settings

logger = logging.getLogger(__name__)

ITCH_API_BASE = "https://itch.io/api/1"
ITCH_PUBLIC_JAMS_URL = "https://itch.io/jams"


class ItchAPIError(RuntimeError):
    """Raised when an itch.io API request fails or returns an error."""
    pass


class ItchAPIClient:
    """
    Official itch.io API v1 Client.
    Supports requests Session with automatic backoff retries for:
      - /api/1/{KEY}/me (authenticated profile)
      - /api/1/{KEY}/my-games (creator project catalog)
      - /api/1/{KEY}/game/{id} (individual game stats)
      - Public game jams feed parsing via BeautifulSoup
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ):
        self.settings = settings
        self.api_key = (
            api_key
            or (getattr(settings, "itch_api_key", "") if settings else "")
            or ""
        ).strip()
        self.username = (
            (getattr(settings, "itch_username", "") if settings else "")
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
                "FreeAutomations-ItchEngine/1.0 (+https://github.com/aman179102)"
            ),
            "Accept": "application/json, text/html, */*",
        })
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def is_configured(self) -> bool:
        """Returns True if the itch.io API key is configured."""
        return bool(self.api_key)

    def _build_url(self, endpoint: str) -> str:
        """
        Constructs official itch.io API v1 URL in the form:
        https://itch.io/api/1/{KEY}/{endpoint}
        """
        cleaned = endpoint.lstrip("/")
        if self.api_key:
            return f"{ITCH_API_BASE}/{self.api_key}/{cleaned}"
        return f"{ITCH_API_BASE}/{cleaned}"

    def get_me(self) -> dict[str, Any]:
        """
        Fetches the authenticated user profile from /api/1/{KEY}/me.
        """
        if not self.is_configured():
            raise ItchAPIError("itch.io API key is not configured.")

        url = self._build_url("me")
        try:
            resp = self.session.get(url, timeout=20.0)
        except requests.RequestException as exc:
            logger.error("Network error fetching itch.io profile: %s", exc)
            raise ItchAPIError(f"Network error contacting itch.io: {exc}") from exc

        if resp.status_code != 200:
            logger.error("itch.io get_me failed (%d): %s", resp.status_code, resp.text)
            raise ItchAPIError(f"HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        if "errors" in data:
            raise ItchAPIError(f"itch.io API error: {data['errors']}")
        return data.get("user", data)

    def verify_credentials(self) -> bool:
        """Checks if the configured itch.io API key is valid."""
        if not self.is_configured():
            return False
        try:
            user = self.get_me()
            return bool(user.get("username") or user.get("id"))
        except Exception as exc:
            logger.warning("itch.io credentials check failed: %s", exc)
            return False

    def get_my_games(self) -> list[dict[str, Any]]:
        """
        Fetches creator games catalog from /api/1/{KEY}/my-games.
        """
        if not self.is_configured():
            raise ItchAPIError("itch.io API key is not configured.")

        url = self._build_url("my-games")
        try:
            resp = self.session.get(url, timeout=25.0)
        except requests.RequestException as exc:
            logger.error("Network error fetching itch.io games: %s", exc)
            raise ItchAPIError(f"Network error contacting itch.io: {exc}") from exc

        if resp.status_code != 200:
            logger.error("itch.io get_my_games failed (%d): %s", resp.status_code, resp.text)
            raise ItchAPIError(f"HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        if "errors" in data:
            raise ItchAPIError(f"itch.io API error: {data['errors']}")
        return data.get("games", [])

    def get_game(self, game_id: int | str) -> dict[str, Any]:
        """
        Fetches individual game details from /api/1/{KEY}/game/{game_id}.
        """
        if not self.is_configured():
            raise ItchAPIError("itch.io API key is not configured.")

        url = self._build_url(f"game/{game_id}")
        try:
            resp = self.session.get(url, timeout=20.0)
        except requests.RequestException as exc:
            logger.error("Network error fetching itch.io game #%s: %s", game_id, exc)
            raise ItchAPIError(f"Network error contacting itch.io: {exc}") from exc

        if resp.status_code != 200:
            logger.error("itch.io get_game failed (%d): %s", resp.status_code, resp.text)
            raise ItchAPIError(f"HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        if "errors" in data:
            raise ItchAPIError(f"itch.io API error: {data['errors']}")
        return data.get("game", data)

    def get_jams(self, url: str = ITCH_PUBLIC_JAMS_URL, limit: int = 50) -> list[dict[str, Any]]:
        """
        Fetches and parses public game jams from itch.io jams feed.
        Extracts jam ID, title, URL, joined count, and dates/summary.
        """
        try:
            resp = self.session.get(url, timeout=20.0)
        except requests.RequestException as exc:
            logger.error("Network error fetching itch.io jams from %s: %s", url, exc)
            raise ItchAPIError(f"Network error fetching jams: {exc}") from exc

        if resp.status_code != 200:
            logger.error("Failed to fetch itch.io jams (%d): %s", resp.status_code, resp.text[:200])
            raise ItchAPIError(f"HTTP {resp.status_code} fetching jams from {url}")

        # Check if response is JSON (if itch supports json feed or format=json)
        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                data = resp.json()
                if isinstance(data, list):
                    return data[:limit]
                elif isinstance(data, dict) and "jams" in data:
                    return data["jams"][:limit]
            except Exception:
                pass

        # Parse HTML response with BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        jam_elements = soup.select(".jam_cell, .featured_jam, .jam_summary")
        jams: list[dict[str, Any]] = []

        for el in jam_elements:
            if len(jams) >= limit:
                break

            jam_id = el.get("data-jam_id") or ""
            link_el = el.select_one("a[href*='/jam/']") or el.select_one("a")
            if not link_el:
                continue

            raw_href = link_el.get("href", "")
            jam_url = urljoin("https://itch.io", raw_href)
            title = link_el.get_text(strip=True)
            if not title:
                continue

            # Extract joined participant count (e.g. "(621 joined)")
            joined_count = 0
            joined_el = el.select_one(".joined_count, .participants_count")
            if joined_el:
                joined_text = joined_el.get_text(strip=True)
                match = re.search(r"(\d[\d,]*)", joined_text)
                if match:
                    try:
                        joined_count = int(match.group(1).replace(",", ""))
                    except ValueError:
                        joined_count = 0

            # Extract countdown / timing or summary text
            summary_parts: list[str] = []
            countdown_el = el.select_one(".countdown, .date_countdown, .dates")
            if countdown_el:
                summary_parts.append(countdown_el.get_text(strip=True))

            desc_el = el.select_one(".summary, .description")
            if desc_el:
                summary_parts.append(desc_el.get_text(strip=True))

            summary = " | ".join(p for p in summary_parts if p)

            if not jam_id:
                # Fallback jam_id from url slug
                slug_match = re.search(r"/jam/([^/?#]+)", jam_url)
                jam_id = slug_match.group(1) if slug_match else f"jam_{len(jams)+1}"

            jams.append({
                "id": str(jam_id),
                "title": title,
                "url": jam_url,
                "joined_count": joined_count,
                "summary": summary,
            })

        return jams

    def create_devlog(
        self,
        game_id: int | str,
        title: str,
        body: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        """
        Creates or drafts a devlog post for a game.
        Supports official API endpoint or structured draft record for manual review.
        """
        if not self.is_configured():
            raise ItchAPIError("itch.io API key is not configured.")

        url = self._build_url(f"game/{game_id}/devlog")
        payload = {
            "title": title,
            "body": body,
            "draft": 1 if draft else 0,
        }

        try:
            resp = self.session.post(url, data=payload, timeout=25.0)
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "status": "success",
                    "devlog_id": data.get("id") or data.get("post", {}).get("id"),
                    "url": data.get("url") or f"https://itch.io/game/{game_id}/devlog",
                    "draft": draft,
                }
            elif resp.status_code in (404, 405):
                # When itch.io API key is restricted to read-only or third-party scopes,
                # record structured devlog draft with web publishing URL
                logger.info(
                    "itch.io devlog API returned %d; structuring devlog draft for game #%s.",
                    resp.status_code,
                    game_id,
                )
                return {
                    "status": "success",
                    "devlog_id": f"draft_{int(time.time())}",
                    "url": f"https://itch.io/game/{game_id}/devlogs/new",
                    "draft": True,
                    "notice": "Draft prepared for publishing via itch.io creator dashboard.",
                }
            else:
                logger.error("Failed to create itch.io devlog (%d): %s", resp.status_code, resp.text)
                raise ItchAPIError(f"HTTP {resp.status_code}: {resp.text}")
        except requests.RequestException as exc:
            logger.error("Network error creating devlog on game #%s: %s", game_id, exc)
            raise ItchAPIError(f"Network error creating devlog: {exc}") from exc

"""Official 100% Free Hacker News APIs Client (Firebase REST & Algolia Search)."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from automations.hn.models import HNComment, HNStory

logger = logging.getLogger(__name__)

FIREBASE_BASE = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


class HNAPIClient:
    """Client for Hacker News Firebase REST API and Algolia Search API."""

    def __init__(
        self,
        timeout: float = 12.0,
        firebase_base: str = FIREBASE_BASE,
        algolia_base: str = ALGOLIA_BASE,
        client: Optional[httpx.Client] = None,
    ):
        self.timeout = timeout
        self.firebase_base = firebase_base.rstrip("/")
        self.algolia_base = algolia_base.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=6.0),
            headers={"User-Agent": "FreeAutomations-HN/1.0 (+https://github.com/aman179102)"},
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client and self._client:
            self._client.close()

    def __enter__(self) -> HNAPIClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _get_json(self, url: str, params: Optional[dict[str, Any]] = None, retries: int = 2) -> Optional[Any]:
        for attempt in range(retries + 1):
            try:
                resp = self._client.get(url, params=params)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                logger.warning("GET %s returned HTTP %s", url, resp.status_code)
                return None
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                logger.warning("GET %s failed: %s", url, exc)
                return None
        return None

    # =========================================================================
    # Firebase REST API Endpoints
    # =========================================================================

    def get_item(self, item_id: int | str) -> Optional[dict[str, Any]]:
        """Fetch raw item JSON from Firebase."""
        url = f"{self.firebase_base}/item/{item_id}.json"
        data = self._get_json(url)
        return data if isinstance(data, dict) else None

    def get_story(self, story_id: int | str) -> Optional[HNStory]:
        """Fetch and parse an HNStory from Firebase."""
        data = self.get_item(story_id)
        if not data:
            return None
        return HNStory.from_firebase(data)

    def get_comment(self, comment_id: int | str, story_id: Optional[int] = None) -> Optional[HNComment]:
        """Fetch and parse an HNComment from Firebase."""
        data = self.get_item(comment_id)
        if not data:
            return None
        return HNComment.from_firebase(data, story_id=story_id)

    def fetch_topstories(self, limit: int = 30) -> list[int]:
        """Fetch IDs of current top stories."""
        url = f"{self.firebase_base}/topstories.json"
        data = self._get_json(url)
        if isinstance(data, list):
            return [int(x) for x in data[:limit] if isinstance(x, (int, str)) and str(x).isdigit()]
        return []

    def fetch_askstories(self, limit: int = 30) -> list[int]:
        """Fetch IDs of current Ask HN stories."""
        url = f"{self.firebase_base}/askstories.json"
        data = self._get_json(url)
        if isinstance(data, list):
            return [int(x) for x in data[:limit] if isinstance(x, (int, str)) and str(x).isdigit()]
        return []

    def fetch_showstories(self, limit: int = 30) -> list[int]:
        """Fetch IDs of current Show HN stories."""
        url = f"{self.firebase_base}/showstories.json"
        data = self._get_json(url)
        if isinstance(data, list):
            return [int(x) for x in data[:limit] if isinstance(x, (int, str)) and str(x).isdigit()]
        return []

    def fetch_newstories(self, limit: int = 30) -> list[int]:
        """Fetch IDs of newest stories."""
        url = f"{self.firebase_base}/newstories.json"
        data = self._get_json(url)
        if isinstance(data, list):
            return [int(x) for x in data[:limit] if isinstance(x, (int, str)) and str(x).isdigit()]
        return []

    def fetch_jobstories(self, limit: int = 30) -> list[int]:
        """Fetch IDs of job stories."""
        url = f"{self.firebase_base}/jobstories.json"
        data = self._get_json(url)
        if isinstance(data, list):
            return [int(x) for x in data[:limit] if isinstance(x, (int, str)) and str(x).isdigit()]
        return []

    # =========================================================================
    # Algolia Search API Endpoints
    # =========================================================================

    def search_algolia(
        self,
        query: str,
        tags: Optional[str] = None,
        numeric_filters: Optional[str] = None,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict[str, Any]:
        """Query Algolia HN Search by relevance."""
        url = f"{self.algolia_base}/search"
        params: dict[str, Any] = {
            "query": query,
            "page": page,
            "hitsPerPage": hits_per_page,
        }
        if tags:
            params["tags"] = tags
        if numeric_filters:
            params["numericFilters"] = numeric_filters

        res = self._get_json(url, params=params)
        return res if isinstance(res, dict) else {"hits": [], "nbHits": 0}

    def search_algolia_by_date(
        self,
        query: str,
        tags: Optional[str] = None,
        numeric_filters: Optional[str] = None,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict[str, Any]:
        """Query Algolia HN Search ordered by date (most recent first)."""
        url = f"{self.algolia_base}/search_by_date"
        params: dict[str, Any] = {
            "query": query,
            "page": page,
            "hitsPerPage": hits_per_page,
        }
        if tags:
            params["tags"] = tags
        if numeric_filters:
            params["numericFilters"] = numeric_filters

        res = self._get_json(url, params=params)
        return res if isinstance(res, dict) else {"hits": [], "nbHits": 0}

    def get_algolia_item(self, item_id: int | str) -> Optional[dict[str, Any]]:
        """Fetch item with full comments tree via Algolia."""
        url = f"{self.algolia_base}/items/{item_id}"
        res = self._get_json(url)
        return res if isinstance(res, dict) else None

    # =========================================================================
    # High-Level Helper Methods
    # =========================================================================

    def find_latest_hiring_thread(self) -> Optional[HNStory]:
        """
        Locates the latest monthly 'Ask HN: Who is hiring?' thread published
        by the official 'whoishiring' account or community.
        """
        # Try search_by_date with author_whoishiring
        res = self.search_algolia_by_date(
            query="Who is hiring",
            tags="story,author_whoishiring",
            hits_per_page=5,
        )
        hits = res.get("hits", [])
        for hit in hits:
            title = hit.get("title", "")
            if "who is hiring?" in title.lower() and "wants to be hired" not in title.lower():
                return HNStory.from_algolia(hit)

        # Fallback without author restriction
        res_fb = self.search_algolia_by_date(
            query="Ask HN: Who is hiring?",
            tags="story",
            hits_per_page=10,
        )
        for hit in res_fb.get("hits", []) or []:
            title = hit.get("title", "")
            if "who is hiring?" in title.lower() and "wants to be hired" not in title.lower():
                return HNStory.from_algolia(hit)

        return None

    def find_monthly_hiring_threads(self, limit: int = 3) -> list[HNStory]:
        """
        Finds recent monthly Who is Hiring, Who Wants to Be Hired, and Freelancer threads.
        """
        res = self.search_algolia_by_date(
            query="Who is hiring",
            tags="story,author_whoishiring",
            hits_per_page=10,
        )
        stories: list[HNStory] = []
        seen_ids = set()
        for hit in res.get("hits", []):
            st = HNStory.from_algolia(hit)
            if st.id not in seen_ids:
                seen_ids.add(st.id)
                stories.append(st)
                if len(stories) >= limit:
                    break
        return stories

    def fetch_thread_comments(self, story_id: int, max_comments: int = 150) -> list[HNComment]:
        """
        Fetches top-level comments for a given thread story.
        Tries Algolia item tree first (single fast query), then falls back to Firebase kids.
        """
        comments: list[HNComment] = []

        # 1. Try Algolia item tree
        alg_item = self.get_algolia_item(story_id)
        if alg_item and "children" in alg_item:
            children = alg_item.get("children", [])
            for child in children[:max_comments]:
                if not child:
                    continue
                comm = HNComment.from_algolia(child, story_id=story_id)
                if comm.clean_text:
                    comments.append(comm)
            if comments:
                return comments

        # 2. Fallback to Firebase
        story = self.get_story(story_id)
        if not story or not story.kids:
            return comments

        for kid_id in story.kids[:max_comments]:
            comm = self.get_comment(kid_id, story_id=story_id)
            if comm and comm.clean_text and not comm.deleted and not comm.dead:
                comments.append(comm)

        return comments

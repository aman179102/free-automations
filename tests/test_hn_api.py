"""Unit and integration tests for Hacker News API client and data models.
Tests Firebase story fetching, item parsing, Algolia search, and comment trees.
"""

from __future__ import annotations

import glob
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Ensure workspace root and virtualenv site-packages are in path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

for site in glob.glob(str(WORKSPACE_ROOT / ".venv" / "lib" / "python*" / "site-packages")):
    if site not in sys.path:
        sys.path.insert(0, site)

import httpx

from automations.hn.api import ALGOLIA_BASE, FIREBASE_BASE, HNAPIClient
from automations.hn.models import HNComment, HNStory, strip_html_tags


class TestHNModels(unittest.TestCase):
    """Tests data models and HTML sanitization."""

    def test_strip_html_tags_basic(self):
        html_input = "Hello &amp; world!<p>This is a test.</p><br>New line."
        expected = "Hello & world!\n\nThis is a test.\nNew line."
        self.assertEqual(strip_html_tags(html_input), expected)

    def test_strip_html_tags_with_links(self):
        html_input = 'Check out <a href="https://example.com">Example Site</a> and <a href="https://go.dev">https://go.dev</a>'
        cleaned = strip_html_tags(html_input)
        self.assertIn("Example Site (https://example.com)", cleaned)
        self.assertIn("https://go.dev", cleaned)

    def test_strip_html_empty_and_escaped(self):
        self.assertEqual(strip_html_tags(""), "")
        self.assertEqual(strip_html_tags("&#62; quoted text &#x2F; slash"), "> quoted text / slash")

    def test_hn_story_from_firebase(self):
        payload = {
            "id": 99001,
            "title": "Ask HN: How do you architect distributed queues?",
            "url": "https://news.ycombinator.com/item?id=99001",
            "by": "systems_lead",
            "score": 142,
            "time": 1700000000,
            "text": "<p>Looking for insights on Kafka vs Redis Streams.</p>",
            "descendants": 55,
            "type": "story",
            "kids": [99002, 99003, "invalid", 99004],
        }
        story = HNStory.from_firebase(payload)
        self.assertEqual(story.id, 99001)
        self.assertEqual(story.title, "Ask HN: How do you architect distributed queues?")
        self.assertEqual(story.by, "systems_lead")
        self.assertEqual(story.score, 142)
        self.assertEqual(story.descendants, 55)
        self.assertEqual(story.kids, [99002, 99003, 99004])
        self.assertEqual(story.hn_url, "https://news.ycombinator.com/item?id=99001")
        self.assertIn("Looking for insights on Kafka vs Redis Streams.", story.clean_text)
        self.assertIsNotNone(story.created_at)

    def test_hn_story_from_algolia(self):
        hit = {
            "objectID": "88123",
            "title": "Show HN: Fast Distributed Storage in Go",
            "url": "https://github.com/aman179102/distributed-file-storage-service",
            "author": "aman179102",
            "points": 310,
            "created_at_i": 1700100000,
            "story_text": "<p>S3-compatible storage with Reed-Solomon coding.</p>",
            "num_comments": 48,
            "children": [88124, 88125],
        }
        story = HNStory.from_algolia(hit)
        self.assertEqual(story.id, 88123)
        self.assertEqual(story.title, "Show HN: Fast Distributed Storage in Go")
        self.assertEqual(story.by, "aman179102")
        self.assertEqual(story.score, 310)
        self.assertEqual(story.descendants, 48)
        self.assertEqual(story.kids, [88124, 88125])
        self.assertEqual(story.hn_url, "https://news.ycombinator.com/item?id=88123")
        self.assertIn("Reed-Solomon coding", story.clean_text)

    def test_hn_comment_from_firebase_and_algolia(self):
        fb_payload = {
            "id": 555,
            "by": "founder_bob",
            "text": "Acme Corp | Senior Backend | Remote | $160k<p>Contact bob@acme.com</p>",
            "time": 1700200000,
            "parent": 100,
            "kids": [556],
            "deleted": False,
            "dead": False,
        }
        comm_fb = HNComment.from_firebase(fb_payload, story_id=100)
        self.assertEqual(comm_fb.id, 555)
        self.assertEqual(comm_fb.by, "founder_bob")
        self.assertEqual(comm_fb.story_id, 100)
        self.assertIn("Acme Corp", comm_fb.clean_text)
        self.assertEqual(comm_fb.hn_url, "https://news.ycombinator.com/item?id=555")

        alg_hit = {
            "objectID": "666",
            "author": "jane_cto",
            "comment_text": "We are building an MVP in FastAPI &amp; Go. Remote.",
            "created_at_i": 1700200500,
            "parent_id": 100,
            "story_id": 100,
        }
        comm_alg = HNComment.from_algolia(alg_hit, story_id=100)
        self.assertEqual(comm_alg.id, 666)
        self.assertEqual(comm_alg.by, "jane_cto")
        self.assertIn("FastAPI & Go", comm_alg.clean_text)


class TestHNAPIClient(unittest.TestCase):
    """Tests for HNAPIClient Firebase & Algolia endpoints."""

    def setUp(self):
        self.mock_http = MagicMock(spec=httpx.Client)
        self.client = HNAPIClient(client=self.mock_http)

    def tearDown(self):
        self.client.close()

    def test_context_manager(self):
        with HNAPIClient(client=self.mock_http) as client:
            self.assertIsNotNone(client)

    def test_get_item_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1234, "title": "Test Title", "type": "story"}
        self.mock_http.get.return_value = mock_resp

        data = self.client.get_item(1234)
        self.assertEqual(data["id"], 1234)
        self.assertEqual(data["title"], "Test Title")
        self.mock_http.get.assert_called_once_with(f"{FIREBASE_BASE}/item/1234.json", params=None)

    @patch("automations.hn.api.logger.warning")
    def test_get_item_not_found(self, mock_log):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        self.mock_http.get.return_value = mock_resp

        data = self.client.get_item(9999999)
        self.assertIsNone(data)
        mock_log.assert_called_once()

    def test_get_story(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 777,
            "title": "Scaling Distributed Databases",
            "score": 88,
            "type": "story",
        }
        self.mock_http.get.return_value = mock_resp

        story = self.client.get_story(777)
        self.assertIsInstance(story, HNStory)
        self.assertEqual(story.id, 777)
        self.assertEqual(story.score, 88)

    def test_get_comment(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 888,
            "by": "reviewer",
            "text": "Great writeup!",
            "parent": 777,
        }
        self.mock_http.get.return_value = mock_resp

        comment = self.client.get_comment(888, story_id=777)
        self.assertIsInstance(comment, HNComment)
        self.assertEqual(comment.id, 888)
        self.assertEqual(comment.story_id, 777)

    def test_fetch_story_categories(self):
        id_list = [101, 102, 103, 104, 105]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = id_list
        self.mock_http.get.return_value = mock_resp

        self.assertEqual(self.client.fetch_topstories(limit=3), [101, 102, 103])
        self.assertEqual(self.client.fetch_askstories(limit=2), [101, 102])
        self.assertEqual(self.client.fetch_showstories(limit=4), [101, 102, 103, 104])
        self.assertEqual(self.client.fetch_newstories(limit=1), [101])
        self.assertEqual(self.client.fetch_jobstories(limit=5), [101, 102, 103, 104, 105])

    def test_search_algolia(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "hits": [
                {"objectID": "111", "title": "Result 1"},
                {"objectID": "222", "title": "Result 2"},
            ],
            "nbHits": 2,
        }
        self.mock_http.get.return_value = mock_resp

        res = self.client.search_algolia("Postgres", tags="story", hits_per_page=10)
        self.assertEqual(res["nbHits"], 2)
        self.assertEqual(len(res["hits"]), 2)
        self.mock_http.get.assert_called_with(
            f"{ALGOLIA_BASE}/search",
            params={"query": "Postgres", "page": 0, "hitsPerPage": 10, "tags": "story"},
        )

    def test_search_algolia_by_date(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"hits": [], "nbHits": 0}
        self.mock_http.get.return_value = mock_resp

        res = self.client.search_algolia_by_date("Ask HN", page=1, hits_per_page=15)
        self.assertEqual(res["nbHits"], 0)
        self.mock_http.get.assert_called_with(
            f"{ALGOLIA_BASE}/search_by_date",
            params={"query": "Ask HN", "page": 1, "hitsPerPage": 15},
        )

    def test_get_algolia_item(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 333,
            "title": "Thread Root",
            "children": [{"id": 334, "author": "dev", "text": "Child comment"}],
        }
        self.mock_http.get.return_value = mock_resp

        res = self.client.get_algolia_item(333)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], 333)
        self.assertEqual(len(res["children"]), 1)

    def test_find_latest_hiring_thread_primary(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "hits": [
                {
                    "objectID": "40001",
                    "title": "Ask HN: Who is hiring? (November 2026)",
                    "author": "whoishiring",
                    "points": 250,
                    "num_comments": 400,
                    "created_at_i": 1700300000,
                }
            ]
        }
        self.mock_http.get.return_value = mock_resp

        thread = self.client.find_latest_hiring_thread()
        self.assertIsNotNone(thread)
        self.assertEqual(thread.id, 40001)
        self.assertIn("Who is hiring?", thread.title)

    def test_find_latest_hiring_thread_fallback(self):
        # First call (author_whoishiring) returns empty hits; second call returns hit
        resp_empty = MagicMock()
        resp_empty.status_code = 200
        resp_empty.json.return_value = {"hits": []}

        resp_found = MagicMock()
        resp_found.status_code = 200
        resp_found.json.return_value = {
            "hits": [
                {
                    "objectID": "40002",
                    "title": "Ask HN: Who is hiring? (Community Thread)",
                    "author": "community_dev",
                    "points": 180,
                    "created_at_i": 1700310000,
                }
            ]
        }
        self.mock_http.get.side_effect = [resp_empty, resp_found]

        thread = self.client.find_latest_hiring_thread()
        self.assertIsNotNone(thread)
        self.assertEqual(thread.id, 40002)

    def test_fetch_thread_comments_via_algolia(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 50001,
            "children": [
                {
                    "objectID": "50002",
                    "author": "hiring_mgr",
                    "comment_text": "<p>Company X | Remote | Go Developer</p>",
                    "created_at_i": 1700400000,
                },
                {
                    "objectID": "50003",
                    "author": "tech_lead",
                    "comment_text": "<p>Company Y | Remote | Python FastAPI</p>",
                    "created_at_i": 1700400100,
                },
            ],
        }
        self.mock_http.get.return_value = mock_resp

        comments = self.client.fetch_thread_comments(50001)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0].id, 50002)
        self.assertIn("Company X", comments[0].clean_text)
        self.assertEqual(comments[1].id, 50003)
        self.assertIn("Company Y", comments[1].clean_text)

    def test_fetch_thread_comments_firebase_fallback(self):
        # 1. Algolia returns empty or None
        resp_alg_empty = MagicMock()
        resp_alg_empty.status_code = 200
        resp_alg_empty.json.return_value = {"id": 50010, "children": []}

        # 2. Firebase returns story with kids
        resp_fb_story = MagicMock()
        resp_fb_story.status_code = 200
        resp_fb_story.json.return_value = {
            "id": 50010,
            "title": "Ask HN: Who is hiring?",
            "kids": [50011],
        }

        # 3. Firebase returns kid item
        resp_fb_kid = MagicMock()
        resp_fb_kid.status_code = 200
        resp_fb_kid.json.return_value = {
            "id": 50011,
            "by": "founder_z",
            "text": "Remote Go/Python role",
            "parent": 50010,
        }

        self.mock_http.get.side_effect = [resp_alg_empty, resp_fb_story, resp_fb_kid]

        comments = self.client.fetch_thread_comments(50010)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].id, 50011)
        self.assertIn("Remote Go/Python role", comments[0].clean_text)

    @patch("time.sleep", return_value=None)
    def test_retry_on_server_error(self, mock_sleep):
        resp_503 = MagicMock()
        resp_503.status_code = 503

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"status": "recovered"}

        self.mock_http.get.side_effect = [resp_503, resp_200]

        res = self.client._get_json("https://example.com/api")
        self.assertEqual(res, {"status": "recovered"})
        self.assertEqual(self.mock_http.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()

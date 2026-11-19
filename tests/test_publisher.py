"""Unit tests for Hacker News Autonomous Publisher, Cooldown Gates, and Auto-Publish Mode."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from automations.hn.publisher import HNPublisher
from automations.hn.runner import HNRunner
from automations.hn.models import DiscussionTopic, HNStory, ShowcaseProject


class TestHNPublisher(unittest.TestCase):
    """Tests for HN authenticated publisher (session, login, commenting, story submission)."""

    def setUp(self):
        self.mock_settings = MagicMock()
        self.mock_settings.hn_username = "aman179102"
        self.mock_settings.hn_password = "test_password_123"
        self.mock_settings.hn_cookie = "test_cookie_abc"
        self.mock_settings.state_dir = "/tmp/test_hn_state"
        self.publisher = HNPublisher(self.mock_settings)

    def test_is_logged_in_true(self):
        html_with_logout = '<html><body><span class="pagetop"><a href="logout?auth=xyz">logout</a></span></body></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_with_logout

        with patch.object(self.publisher.client, "get", return_value=mock_resp):
            self.assertTrue(self.publisher.is_logged_in())

    def test_is_logged_in_false(self):
        html_logged_out = '<html><body><a href="login?goto=news">login</a></body></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_logged_out

        with patch.object(self.publisher.client, "get", return_value=mock_resp):
            self.assertFalse(self.publisher.is_logged_in())

    def test_login_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><body><a href="logout">logout</a></body></html>'

        with patch.object(self.publisher.client, "post", return_value=mock_resp):
            with patch("automations.hn.publisher.load_state", return_value={}):
                with patch("automations.hn.publisher.save_state"):
                    ok = self.publisher.login("aman179102", "password")
                    self.assertTrue(ok)

    def test_login_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><body>Bad login. <a href="login">Try again</a></body></html>'

        with patch.object(self.publisher.client, "post", return_value=mock_resp):
            ok = self.publisher.login("aman179102", "badpassword")
            self.assertFalse(ok)

    def test_post_comment_success(self):
        item_html = """
        <html><body>
        <form action="comment" method="post">
            <input type="hidden" name="parent" value="12345">
            <input type="hidden" name="goto" value="item?id=12345">
            <input type="hidden" name="hmac" value="mock_hmac_token_abc">
            <textarea name="text"></textarea>
        </form>
        </body></html>
        """
        get_resp = MagicMock(status_code=200, text=item_html)
        post_resp = MagicMock(status_code=302)

        with patch.object(self.publisher, "ensure_authenticated", return_value=True):
            with patch.object(self.publisher.client, "get", return_value=get_resp):
                with patch.object(self.publisher.client, "post", return_value=post_resp) as mock_post:
                    ok = self.publisher.post_comment(12345, "Senior engineering analysis...")
                    self.assertTrue(ok)
                    mock_post.assert_called_once()
                    args, kwargs = mock_post.call_args
                    self.assertEqual(kwargs["data"]["hmac"], "mock_hmac_token_abc")
                    self.assertEqual(kwargs["data"]["parent"], "12345")
                    self.assertEqual(kwargs["data"]["text"], "Senior engineering analysis...")

    def test_submit_story_success(self):
        submit_html = """
        <html><body>
        <form action="r" method="post">
            <input type="hidden" name="fnid" value="mock_fnid_999">
            <input type="hidden" name="fnop" value="submit-page">
            <input type="text" name="title">
            <input type="text" name="url">
            <textarea name="text"></textarea>
        </form>
        </body></html>
        """
        get_resp = MagicMock(status_code=200, text=submit_html)
        post_resp = MagicMock(status_code=302, url="https://news.ycombinator.com/item?id=99999")

        with patch.object(self.publisher, "ensure_authenticated", return_value=True):
            with patch.object(self.publisher.client, "get", return_value=get_resp):
                with patch.object(self.publisher.client, "post", return_value=post_resp) as mock_post:
                    res_url = self.publisher.submit_story(
                        title="Show HN: Go Distributed Storage",
                        text="Architecture writeup...",
                        url="",
                    )
                    self.assertEqual(res_url, "https://news.ycombinator.com/item?id=99999")
                    mock_post.assert_called_once()
                    args, kwargs = mock_post.call_args
                    self.assertEqual(kwargs["data"]["fnid"], "mock_fnid_999")
                    self.assertEqual(kwargs["data"]["title"], "Show HN: Go Distributed Storage")


class TestAutonomousRunner(unittest.TestCase):
    """Tests for 100% autonomous auto-pilot mode in HNRunner."""

    def setUp(self):
        self.mock_settings = MagicMock()
        self.mock_settings.has_telegram = False
        self.mock_settings.has_ai = False
        self.mock_settings.hn_auto_publish = True
        self.mock_settings.hn_username = "aman179102"
        self.runner = HNRunner(settings=self.mock_settings, state_file="/tmp/test_runner_state.json")
        self.runner.discussion_engine = MagicMock()
        self.runner.showcase_manager = MagicMock()

    @patch("automations.hn.publisher.HNPublisher.post_comment", return_value=True)
    def test_autonomous_discussion_auto_publish(self, mock_post_comment):
        topic = MagicMock()
        topic.story.id = 5555
        topic.story.title = "Ask HN: How to handle 100k events/sec?"
        topic.story.score = 45
        topic.story.descendants = 8
        topic.category = "Real-Time Streaming & High-Throughput Ingestion"
        topic.draft_response = "Decouple HTTP ingestion with append-only streaming logs..."

        self.runner.discussion_engine.find_architecture_discussions.return_value = [topic]

        # Initial state with no prior comments (cooldown satisfied)
        test_state = {"seen_discussion_ids": [], "hn_last_comment_at": 0}

        with patch.object(self.runner, "_load_state", return_value=test_state):
            with patch.object(self.runner, "_save_state"):
                topics = self.runner.run_discussion(max_discussions=1, auto_publish=True, send_telegram=False)
                self.assertEqual(len(topics), 1)
                mock_post_comment.assert_called_once_with(5555, topic.draft_response)
                self.assertGreater(test_state["hn_last_comment_at"], 0)

    @patch("automations.hn.publisher.HNPublisher.post_comment")
    def test_autonomous_comment_cooldown_gate(self, mock_post_comment):
        """Verifies that cooldown prevents posting comments too frequently (anti-shadowban guard)."""
        topic = MagicMock()
        topic.story.id = 6666
        topic.story.title = "Ask HN: Database sharding vs replication"
        topic.story.score = 25
        topic.story.descendants = 4
        topic.category = "Databases & I/O Internals"
        topic.draft_response = "B+ Trees vs LSM..."

        self.runner.discussion_engine.find_architecture_discussions.return_value = [topic]

        # State showing a comment was posted 5 minutes ago (cooldown is 30 mins)
        recent_ts = time.time() - 300  # 5 minutes ago
        test_state = {"seen_discussion_ids": [], "hn_last_comment_at": recent_ts}

        with patch.object(self.runner, "_load_state", return_value=test_state):
            with patch.object(self.runner, "_save_state"):
                topics = self.runner.run_discussion(max_discussions=1, auto_publish=True, send_telegram=False)
                self.assertEqual(len(topics), 1)
                # Should NOT have posted live because cooldown is active!
                mock_post_comment.assert_not_called()

    @patch("automations.hn.publisher.HNPublisher.submit_story", return_value="https://news.ycombinator.com/item?id=88888")
    def test_autonomous_showcase_submission(self, mock_submit_story):
        proj = ShowcaseProject(
            slug="distributed-file-storage",
            title="Distributed File Storage",
            github_url="https://github.com/aman179102/distributed-file-storage-service",
            summary="S3 compatible storage engine in Go",
            tech_stack=["Go", "Reed-Solomon", "PostgreSQL"],
            architecture_points=["Reed-Solomon 4+2"],
            tradeoffs=["CPU vs Storage overhead"],
            suggested_title="Show HN: Distributed File Storage in Go",
            body_markdown="Hey HN, I built this distributed file storage...",
        )
        self.runner.showcase_manager.list_projects.return_value = [proj]
        self.runner.showcase_manager.get_project.return_value = proj

        # State with cooldown satisfied
        test_state = {"hn_last_story_at": 0, "posted_showcases": {}}

        with patch.object(self.runner, "_load_state", return_value=test_state):
            with patch.object(self.runner, "_save_state"):
                projects = self.runner.run_showcase(project_slug="distributed-file-storage", auto_publish=True, send_telegram=False)
                self.assertEqual(len(projects), 1)
                mock_submit_story.assert_called_once_with(
                    title="Show HN: Distributed File Storage in Go",
                    text="Hey HN, I built this distributed file storage...",
                    url="",
                )
                self.assertGreater(test_state["hn_last_story_at"], 0)
                self.assertIn("distributed-file-storage", test_state["posted_showcases"])


if __name__ == "__main__":
    unittest.main()

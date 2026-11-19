from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from automations.tumblr.api import TumblrAPIClient, TumblrAPIError
from automations.tumblr.content import generate_tumblr_post
from automations.tumblr.publisher import TumblrPublisher
from automations.tumblr.runner import TumblrRunner


class TestTumblrModule(unittest.TestCase):
    """Unit tests for the Tumblr autonomous engine."""

    def setUp(self):
        self.sleep_patch = patch("time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

        self.mock_settings = MagicMock()
        self.mock_settings.tumblr_consumer_key = "mock_consumer_key"
        self.mock_settings.tumblr_consumer_secret = "mock_consumer_secret"
        self.mock_settings.tumblr_oauth_token = "mock_token"
        self.mock_settings.tumblr_oauth_token_secret = "mock_secret"
        self.mock_settings.tumblr_blog_name = "aman179102"
        self.mock_settings.tumblr_auto_publish = True
        self.mock_settings.has_ai = False
        self.mock_settings.has_telegram = True
        self.mock_settings.state_dir = Path("/tmp/test_tumblr_state")

    def test_client_is_configured(self):
        client = TumblrAPIClient(self.mock_settings)
        self.assertTrue(client.is_configured())

        empty_settings = MagicMock()
        empty_settings.tumblr_consumer_key = ""
        client_empty = TumblrAPIClient(empty_settings)
        self.assertFalse(client_empty.is_configured())

    def test_normalize_blog_id(self):
        client = TumblrAPIClient(self.mock_settings)
        self.assertEqual(client._normalize_blog_id("aman179102"), "aman179102.tumblr.com")
        self.assertEqual(client._normalize_blog_id("aman179102.tumblr.com"), "aman179102.tumblr.com")

    @patch("httpx.Client.post")
    def test_create_text_post(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"response": {"id": 123456789}}
        mock_post.return_value = mock_resp

        client = TumblrAPIClient(self.mock_settings)
        res = client.create_text_post(
            title="WAL Mode in SQLite",
            body="Here is why WAL mode is fast...",
            tags=["#programming", "#databases"],
            state="published",
        )
        self.assertEqual(res.get("id"), 123456789)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs["data"]["tags"], "programming,databases")
        self.assertEqual(call_kwargs["data"]["state"], "published")

    @patch("httpx.Client.post")
    def test_reblog_post(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"response": {"id": 987654321}}
        mock_post.return_value = mock_resp

        client = TumblrAPIClient(self.mock_settings)
        res = client.reblog_post(
            post_id="555",
            reblog_key="xyz123",
            comment="Great architectural insight!",
            tags=["systems"],
        )
        self.assertEqual(res.get("id"), 987654321)
        mock_post.assert_called_once()

    def test_generate_tumblr_post_content(self):
        candidate = generate_tumblr_post(self.mock_settings)
        self.assertTrue(len(candidate.title) > 5)
        self.assertTrue(len(candidate.body) > 50)
        self.assertTrue(len(candidate.tags) >= 2)
        self.assertFalse(candidate.is_promo)
        # Verify 99.9% clean content has no promotional links
        self.assertNotIn("http://", candidate.body)
        self.assertNotIn("https://", candidate.body)

    def test_publisher_post(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_text_post.return_value = {"id": 12345}

        publisher = TumblrPublisher(self.mock_settings, client=mock_client)
        candidate = generate_tumblr_post(self.mock_settings)
        res = publisher.publish_post(candidate, use_queue=False)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["response"]["id"], 12345)
        mock_client.create_text_post.assert_called_once()

    @patch("automations.tumblr.runner.load_state")
    @patch("automations.tumblr.runner.save_state")
    def test_runner_cooldown_and_post(self, mock_save, mock_load):
        # 1. First run: succeeds
        mock_load.return_value = {"tumblr_last_post_at": 0, "tumblr_daily_posts": {}}
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client._normalize_blog_id.return_value = "aman179102.tumblr.com"
        mock_client.create_text_post.return_value = {"id": 999}

        runner = TumblrRunner(self.mock_settings, client=mock_client)
        res = runner.run_post(force=False)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["live"])

        # 2. Immediate second run: blocked by cooldown
        mock_load.return_value = {"tumblr_last_post_at": 10000000000, "tumblr_daily_posts": {}}
        res_cooldown = runner.run_post(force=False)
        self.assertEqual(res_cooldown["status"], "skipped")
        self.assertTrue("cooldown" in res_cooldown["reason"])


if __name__ == "__main__":
    unittest.main()

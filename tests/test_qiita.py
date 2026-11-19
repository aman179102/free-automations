from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from automations.qiita.analytics import (
    QiitaAnalyticsReport,
    QiitaArticleMetrics,
    format_analytics_digest,
    track_analytics,
)
from automations.qiita.api import QIITA_API_BASE, QiitaAPIClient, QiitaAPIError
from automations.qiita.articles import (
    QIITA_TEMPLATES,
    QiitaArticleCandidate,
    generate_ai_article,
    generate_qiita_article,
    generate_template_article,
    validate_article_content,
)
from automations.qiita.publisher import QiitaPublisher, QiitaRateLimitError
from automations.qiita.radar import (
    QiitaRadarItem,
    filter_unseen_items,
    format_radar_digest,
    parse_radar_item,
    scan_tag_radar,
)
from automations.qiita.runner import (
    MIN_ARTICLE_COOLDOWN_SECONDS,
    QiitaRunner,
    run_all,
    run_analytics,
    run_article,
    run_radar,
)
from common.config import Settings, load_settings
from server import AutomationHandler, _dispatch_automation_job, _handle_telegram_webhook_async
from main import run_qiita_action


class DummyAutomationHandler(AutomationHandler):
    """Test helper for exercising AutomationHandler HTTP request methods in-memory."""

    def __init__(self, method: str, path: str, body: bytes = b"", settings: Settings | None = None):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
        self.command = method
        self.path = path
        self.request_version = "HTTP/1.1"
        self.settings = settings
        self.status_code: int = 0
        self.response_headers: dict[str, str] = {}

    def send_response(self, code: int, message: str | None = None) -> None:
        self.status_code = code

    def send_header(self, keyword: str, value: str) -> None:
        self.response_headers[keyword] = value

    def end_headers(self) -> None:
        pass

    def log_message(self, format: str, *args: object) -> None:
        pass

    def get_response_json(self) -> dict:
        raw = self.wfile.getvalue().decode("utf-8")
        return json.loads(raw) if raw else {}


class TestQiitaModule(unittest.TestCase):
    """Comprehensive test suite for the Qiita Autonomous Tech Publishing Engine."""

    def setUp(self):
        self.sleep_patch = patch("time.sleep")
        self.mock_sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_dir = Path(self.temp_dir.name)

        self.mock_settings = Settings(
            qiita_access_token="test_token_456",
            qiita_username="aman179102",
            qiita_auto_publish=True,
            telegram_bot_token="test_bot_token",
            telegram_chat_id="test_chat_id",
            state_dir=self.state_dir,
            gemini_api_key="test_ai_key",
        )

    # =========================================================================
    # 1. QiitaAPIClient Tests
    # =========================================================================

    def test_api_client_configuration(self):
        client = QiitaAPIClient(access_token="abc123token")
        self.assertTrue(client.is_configured())
        self.assertEqual(client.access_token, "abc123token")
        self.assertIn("Authorization", client.session.headers)
        self.assertEqual(client.session.headers["Authorization"], "Bearer abc123token")

        empty_client = QiitaAPIClient(access_token="")
        self.assertFalse(empty_client.is_configured())
        self.assertNotIn("Authorization", empty_client.session.headers)

    def test_api_client_build_url(self):
        client = QiitaAPIClient(access_token="token")
        self.assertEqual(client._build_url("authenticated_user"), f"{QIITA_API_BASE}/authenticated_user")
        self.assertEqual(client._build_url("/items"), f"{QIITA_API_BASE}/items")

    def test_api_client_get_authenticated_user_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "aman179102", "name": "Aman Kumar", "items_count": 5}
        mock_session.get.return_value = mock_resp

        client = QiitaAPIClient(access_token="valid_token", session=mock_session)
        user = client.get_authenticated_user()
        self.assertEqual(user["id"], "aman179102")
        self.assertEqual(user["name"], "Aman Kumar")
        self.assertTrue(client.verify_credentials())

    def test_api_client_get_authenticated_user_error(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_session.get.return_value = mock_resp

        client = QiitaAPIClient(access_token="invalid_token", session=mock_session)
        with self.assertRaises(QiitaAPIError):
            client.get_authenticated_user()
        self.assertFalse(client.verify_credentials())

    def test_api_client_create_item_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "id": "item_123",
            "title": "Test Title",
            "url": "https://qiita.com/aman179102/items/item_123",
        }
        mock_session.post.return_value = mock_resp

        client = QiitaAPIClient(access_token="valid_token", session=mock_session)
        tags = ["Python", "FastAPI", "asyncio"]
        result = client.create_item("Test Title", "Article Body", tags, private=False)

        self.assertEqual(result["id"], "item_123")
        self.assertEqual(result["url"], "https://qiita.com/aman179102/items/item_123")
        mock_session.post.assert_called_once()
        call_args, call_kwargs = mock_session.post.call_args
        self.assertEqual(call_args[0], f"{QIITA_API_BASE}/items")
        posted_json = call_kwargs["json"]
        self.assertEqual(posted_json["title"], "Test Title")
        self.assertEqual(len(posted_json["tags"]), 3)
        self.assertEqual(posted_json["tags"][0], {"name": "Python", "versions": []})

    def test_api_client_create_item_tag_capping(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "item_456"}
        mock_session.post.return_value = mock_resp

        client = QiitaAPIClient(access_token="valid_token", session=mock_session)
        # Pass 7 tags; Qiita allows max 5 tags
        tags = ["Tag1", "Tag2", "Tag3", "Tag4", "Tag5", "Tag6", "Tag7"]
        client.create_item("Title", "Body", tags)
        _, call_kwargs = mock_session.post.call_args
        self.assertEqual(len(call_kwargs["json"]["tags"]), 5)

    def test_api_client_get_authenticated_user_items(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": "item_1", "title": "Article 1", "likes_count": 10},
            {"id": "item_2", "title": "Article 2", "likes_count": 5},
        ]
        mock_session.get.return_value = mock_resp

        client = QiitaAPIClient(access_token="valid_token", session=mock_session)
        items = client.get_authenticated_user_items(page=1, per_page=10)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "item_1")

    def test_api_client_get_tag_items(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": "tag_item_1", "title": "Python Async Guide", "likes_count": 25},
        ]
        mock_session.get.return_value = mock_resp

        client = QiitaAPIClient(access_token="valid_token", session=mock_session)
        items = client.get_tag_items("Python", page=1, per_page=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Python Async Guide")

    def test_api_client_get_item(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "af0038f3628b75e913af",
            "title": "FastAPI Benchmark",
            "likes_count": 42,
        }
        mock_session.get.return_value = mock_resp

        client = QiitaAPIClient(access_token="valid_token", session=mock_session)
        item = client.get_item("af0038f3628b75e913af")
        self.assertEqual(item["id"], "af0038f3628b75e913af")
        self.assertEqual(item["title"], "FastAPI Benchmark")

    def test_api_client_unconfigured_calls_raise(self):
        client = QiitaAPIClient(access_token="")
        with self.assertRaises(QiitaAPIError):
            client.create_item("Title", "Body", ["Python"])
        with self.assertRaises(QiitaAPIError):
            client.get_authenticated_user_items()

    # =========================================================================
    # 2. Articles Generator & 0.1% Rule Tests
    # =========================================================================

    def test_all_curated_templates_validity(self):
        self.assertEqual(len(QIITA_TEMPLATES), 5)
        for tmpl in QIITA_TEMPLATES:
            cand = generate_template_article(tmpl["category"])
            is_valid, msg = validate_article_content(cand)
            self.assertTrue(is_valid, f"Template '{cand.category}' failed validation: {msg}")
            self.assertGreaterEqual(len(cand.body.split()), 150)
            self.assertTrue(bool(cand.ascii_diagram))
            self.assertIn("```", cand.body)

    def test_article_validation_length_gate(self):
        cand = QiitaArticleCandidate(
            title="Short Post",
            body="短い記事です。FastAPI は速いです。",
            category="test",
            tags=["Python"],
            ascii_diagram="+---+",
        )
        is_valid, msg = validate_article_content(cand)
        self.assertFalse(is_valid)
        self.assertIn("too brief", msg)

    def test_article_validation_japanese_gate(self):
        english_only_body = (
            "This is a purely English article about FastAPI and uvloop performance. " * 30
            + "\n```python\nimport fastapi\n```\n```\n+---+\n| A |\n+---+\n```"
        )
        cand = QiitaArticleCandidate(
            title="English Article",
            body=english_only_body,
            category="test",
            tags=["Python"],
            ascii_diagram="+---+",
        )
        is_valid, msg = validate_article_content(cand)
        self.assertFalse(is_valid)
        self.assertIn("authentic Japanese", msg)

    def test_article_validation_diagram_gate(self):
        body_without_diagram = (
            "# 【Python】テスト記事\n\n"
            + "これはテスト記事です。パフォーマンスを最適化するためのガイドです。" * 40
            + "\n```python\nimport asyncio\n```"
        )
        cand = QiitaArticleCandidate(
            title="No Diagram Article",
            body=body_without_diagram,
            category="test",
            tags=["Python"],
            ascii_diagram="",
        )
        is_valid, msg = validate_article_content(cand)
        self.assertFalse(is_valid)
        self.assertIn("ASCII", msg)

    def test_article_validation_code_block_gate(self):
        body_without_code = (
            "# 【Python】テスト記事\n\n"
            + "これはテスト記事です。パフォーマンスを最適化するためのガイドです。" * 40
            + "\n```\n+---------+\n| Gateway |\n+---------+\n```"
        )
        cand = QiitaArticleCandidate(
            title="No Code Article",
            body=body_without_code,
            category="test",
            tags=["Python"],
            ascii_diagram="+---------+",
        )
        is_valid, msg = validate_article_content(cand)
        self.assertFalse(is_valid)
        self.assertIn("working code blocks", msg)

    def test_article_validation_spam_keywords_rejection(self):
        tmpl = QIITA_TEMPLATES[0]
        spam_body = tmpl["body"] + "\n\nClick here for buy now discount limited offer promo code!"
        cand = QiitaArticleCandidate(
            title="Spam Article",
            body=spam_body,
            category="test",
            tags=["Python"],
            ascii_diagram="+---+",
        )
        is_valid, msg = validate_article_content(cand)
        self.assertFalse(is_valid)
        self.assertIn("Spam detected", msg)

    def test_article_validation_multiple_external_links_rejection(self):
        tmpl = QIITA_TEMPLATES[0]
        two_links_body = (
            tmpl["body"]
            + "\n[Link 1](https://github.com/aman179102)\n[Link 2](https://github.com/aman179102/repo)"
        )
        cand = QiitaArticleCandidate(
            title="Two Links Article",
            body=two_links_body,
            category="test",
            tags=["Python"],
            ascii_diagram="+---+",
        )
        is_valid, msg = validate_article_content(cand)
        self.assertFalse(is_valid)
        self.assertIn("0.1% rule violation", msg)

    def test_article_validation_disallowed_external_link_rejection(self):
        tmpl = QIITA_TEMPLATES[0]
        # Replace github link with untrusted external domain
        bad_link_body = tmpl["body"].replace("https://github.com/aman179102", "https://untrusted-external-site.com/docs")
        cand = QiitaArticleCandidate(
            title="Phishing Link Article",
            body=bad_link_body,
            category="test",
            tags=["Python"],
            ascii_diagram="+---+",
        )
        is_valid, msg = validate_article_content(cand)
        self.assertFalse(is_valid)
        self.assertIn("Disallowed external link", msg)

    @patch("automations.qiita.articles.generate_text")
    def test_generate_ai_article_fallback(self, mock_ai):
        # When AI fails, it should return None and generate_qiita_article falls back to template
        mock_ai.side_effect = RuntimeError("AI quota exhausted")
        cand = generate_qiita_article(self.mock_settings, category="fastapi_high_throughput")
        self.assertIsNotNone(cand)
        self.assertFalse(cand.is_ai_generated)
        self.assertEqual(cand.category, "fastapi_high_throughput")

    @patch("automations.qiita.articles.generate_text")
    def test_generate_ai_article_success(self, mock_ai):
        valid_body = (
            "# 【FastAPI】AI 生成の超高速非同期アーキテクチャ\n\n"
            + "これは FastAPI と uvloop の性能を最大化する日本語記事です。" * 30
            + "\n```\n+---------+\n| Gateway |\n+---------+\n```\n"
            + "```python\nimport asyncio\nasync def main(): pass\n```\n\n"
            + "詳細は https://github.com/aman179102 を参照してください。\n"
            + "TAGS: Python, FastAPI, asyncio, uvloop"
        )
        mock_ai.return_value = valid_body
        cand = generate_ai_article(self.mock_settings, category="random")
        self.assertIsNotNone(cand)
        self.assertTrue(cand.is_ai_generated)
        self.assertIn("FastAPI", cand.title)
        self.assertIn("Python", cand.tags)

    # =========================================================================
    # 3. Tag Radar Tests
    # =========================================================================

    def test_parse_radar_item(self):
        raw = {
            "id": "q123",
            "title": "FastAPI uvloop チューニング",
            "url": "https://qiita.com/user/items/q123",
            "likes_count": 15,
            "stocks_count": 4,
            "page_views_count": 120,
            "user": {"id": "dev_user", "name": "Dev User"},
            "tags": [{"name": "Python"}, {"name": "FastAPI"}],
            "created_at": "2026-11-19T10:00:00Z",
        }
        item = parse_radar_item(raw, matched_tag="FastAPI")
        self.assertIsNotNone(item)
        self.assertEqual(item.item_id, "q123")
        self.assertEqual(item.likes_count, 15)
        self.assertEqual(item.stocks_count, 4)
        self.assertEqual(item.views_count, 120)
        self.assertEqual(item.author_id, "dev_user")
        self.assertIn("Python", item.tags)
        self.assertEqual(item.matched_tag, "FastAPI")

    def test_scan_tag_radar(self):
        mock_client = MagicMock()
        mock_client.get_tag_items.side_effect = lambda tag, page, per_page: [
            {
                "id": f"{tag}_item_1",
                "title": f"Intro to {tag}",
                "url": f"https://qiita.com/items/{tag}_1",
                "likes_count": 5,
                "stocks_count": 1,
                "user": {"id": "author1"},
                "tags": [{"name": tag}],
            }
        ]

        items = scan_tag_radar(mock_client, tags=["Python", "Go"], limit_per_tag=2)
        self.assertEqual(len(items), 2)
        tag_names = {i.matched_tag for i in items}
        self.assertEqual(tag_names, {"Python", "Go"})

    def test_filter_unseen_items(self):
        items = [
            QiitaRadarItem("id_1", "Title 1", "url_1", 10, 2, 50, "a1", "A1", ["Python"]),
            QiitaRadarItem("id_2", "Title 2", "url_2", 20, 5, 80, "a2", "A2", ["FastAPI"]),
        ]
        unseen = filter_unseen_items(items, seen_ids={"id_1"})
        self.assertEqual(len(unseen), 1)
        self.assertEqual(unseen[0].item_id, "id_2")

    def test_format_radar_digest(self):
        items = [
            QiitaRadarItem("id_1", "Title 1", "https://qiita.com/1", 12, 3, 40, "auth1", "Author 1", ["Python"], matched_tag="Python"),
        ]
        digest = format_radar_digest(items)
        self.assertIn("Qiita Tech Radar", digest)
        self.assertIn("Title 1", digest)
        self.assertIn("12", digest)
        self.assertIn("#Python", digest)

    def test_scan_tag_radar_error_resilience(self):
        mock_client = MagicMock()
        mock_client.get_tag_items.side_effect = QiitaAPIError("Rate limited on tag")
        items = scan_tag_radar(mock_client, tags=["Python"])
        self.assertEqual(items, [])

    # =========================================================================
    # 4. Analytics Tracker Tests
    # =========================================================================

    def test_track_analytics_with_deltas(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_authenticated_user_items.return_value = [
            {"id": "a1", "title": "Art 1", "url": "u1", "likes_count": 15, "stocks_count": 5, "page_views_count": 100},
            {"id": "a2", "title": "Art 2", "url": "u2", "likes_count": 25, "stocks_count": 10, "page_views_count": 200},
        ]

        prior_state = {
            "analytics": {
                "total_likes": 30,
                "total_stocks": 12,
                "total_views": 250,
            }
        }

        report = track_analytics(mock_client, prior_state=prior_state)
        self.assertEqual(report.total_likes, 40)
        self.assertEqual(report.total_stocks, 15)
        self.assertEqual(report.total_views, 300)
        self.assertEqual(report.items_count, 2)
        # Deltas
        self.assertEqual(report.delta_likes, 10)
        self.assertEqual(report.delta_stocks, 3)
        self.assertEqual(report.delta_views, 50)

    def test_format_analytics_digest(self):
        report = QiitaAnalyticsReport(
            total_likes=50,
            total_stocks=20,
            total_views=1500,
            items_count=3,
            delta_likes=5,
            delta_stocks=2,
            delta_views=100,
            articles=[
                QiitaArticleMetrics("1", "FastAPI Deep Dive", "https://qiita.com/1", 30, 15, 1000),
            ],
        )
        digest = format_analytics_digest(report)
        self.assertIn("Qiita Creator Analytics", digest)
        self.assertIn("50", digest)
        self.assertIn("(+5)", digest)
        self.assertIn("FastAPI Deep Dive", digest)

    def test_track_analytics_unconfigured_or_error(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = False
        report = track_analytics(mock_client)
        self.assertEqual(report.total_likes, 0)
        self.assertEqual(report.items_count, 0)

        # When client raises QiitaAPIError
        mock_client.is_configured.return_value = True
        mock_client.get_authenticated_user_items.side_effect = QiitaAPIError("Network timeout")
        report2 = track_analytics(mock_client)
        self.assertEqual(report2.total_likes, 0)

    # =========================================================================
    # 5. Publisher Anti-Ban & Rate Limit Tests
    # =========================================================================

    def test_publisher_dry_run(self):
        cand = generate_template_article("fastapi_high_throughput")
        mock_client = MagicMock()
        publisher = QiitaPublisher(self.mock_settings, client=mock_client)

        res = publisher.publish_article(cand, dry_run=True)
        self.assertEqual(res["status"], "dry_run")
        self.assertEqual(res["title"], cand.title)
        mock_client.create_item.assert_not_called()

    def test_publisher_jitter_and_success(self):
        cand = generate_template_article("fastapi_high_throughput")
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_item.return_value = {
            "id": "new_pub_123",
            "url": "https://qiita.com/aman179102/items/new_pub_123",
            "title": cand.title,
        }

        publisher = QiitaPublisher(self.mock_settings, client=mock_client)
        res = publisher.publish_article(cand, dry_run=False)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["item_id"], "new_pub_123")
        self.assertTrue(res["published"])
        self.mock_sleep.assert_called_once()
        jitter_arg = self.mock_sleep.call_args[0][0]
        self.assertGreaterEqual(jitter_arg, 5.0)
        self.assertLessEqual(jitter_arg, 12.0)

    def test_publisher_cooldown_rate_limit(self):
        cand = generate_template_article("fastapi_high_throughput")
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_item.return_value = {"id": "1", "url": "u"}

        publisher = QiitaPublisher(self.mock_settings, client=mock_client, min_cooldown_seconds=300.0)
        # First publish
        res1 = publisher.publish_article(cand)
        self.assertEqual(res1["status"], "success")

        # Second publish immediately -> rate limited
        res2 = publisher.publish_article(cand)
        self.assertEqual(res2["status"], "rate_limited")
        self.assertIn("Anti-spam rate limit", res2["error"])

    def test_publisher_daily_cap_limit(self):
        cand = generate_template_article("fastapi_high_throughput")
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_item.return_value = {"id": "1", "url": "u"}

        # Cap at 1 post per day
        publisher = QiitaPublisher(self.mock_settings, client=mock_client, min_cooldown_seconds=0.0, max_daily_posts=1)
        res1 = publisher.publish_article(cand)
        self.assertEqual(res1["status"], "success")

        res2 = publisher.publish_article(cand)
        self.assertEqual(res2["status"], "rate_limited")
        self.assertIn("Daily publish cap reached", res2["error"])

    def test_publisher_unconfigured_and_validation_errors(self):
        # Unconfigured
        unconf_settings = Settings(qiita_access_token="", state_dir=self.state_dir)
        unconf_publisher = QiitaPublisher(unconf_settings)
        cand = generate_template_article("fastapi_high_throughput")
        res1 = unconf_publisher.publish_article(cand)
        self.assertEqual(res1["status"], "unconfigured")

        # Validation error
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        publisher = QiitaPublisher(self.mock_settings, client=mock_client)
        invalid_cand = QiitaArticleCandidate("Bad", "too short", "cat", ["Py"], "")
        res2 = publisher.publish_article(invalid_cand)
        self.assertEqual(res2["status"], "error")
        self.assertIn("validation", res2["error"].lower())

    def test_publisher_api_error_handling(self):
        cand = generate_template_article("fastapi_high_throughput")
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_item.side_effect = QiitaAPIError("Qiita 500 Internal Error")
        publisher = QiitaPublisher(self.mock_settings, client=mock_client)
        res = publisher.publish_article(cand)
        self.assertEqual(res["status"], "error")
        self.assertIn("Qiita 500 Internal Error", res["error"])

    # =========================================================================
    # 6. Runner & Cooldown Enforcement Tests
    # =========================================================================

    def test_runner_article_cooldown_enforcement(self):
        runner = QiitaRunner(self.mock_settings)
        # Simulate an article published 2 hours ago
        state = {"last_publish_ts": time.time() - 7200}
        runner._save_state(state)

        # Without force -> skipped
        res_skipped = runner.run_article(force=False, dry_run=True)
        self.assertEqual(res_skipped["status"], "skipped")
        self.assertIn("hours_remaining", res_skipped)
        self.assertGreater(res_skipped["hours_remaining"], 20.0)

        # With force=True -> runs
        res_forced = runner.run_article(force=True, dry_run=True)
        self.assertEqual(res_forced["status"], "dry_run")

    @patch("automations.qiita.runner.send_telegram_message")
    def test_runner_run_all_consolidation_and_zero_spam(self, mock_tg):
        runner = QiitaRunner(self.mock_settings)
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.username = "aman179102"
        mock_client.get_authenticated_user_items.return_value = []
        mock_client.get_tag_items.return_value = []
        runner.client = mock_client
        runner.publisher.client = mock_client

        # Seed state so no new deltas or items exist
        initial_state = {
            "analytics": {"total_likes": 0, "total_stocks": 0, "total_views": 0},
            "seen_qiita_item_ids": [],
            "last_publish_ts": time.time(),  # In cooldown
        }
        runner._save_state(initial_state)

        # Background run without force and no new activity -> zero spam (no telegram sent)
        res_quiet = runner.run_all(force=False, dry_run=False)
        self.assertEqual(res_quiet["status"], "success")
        self.assertFalse(res_quiet["telegram_dispatched"])
        mock_tg.assert_not_called()

        # Run with force=True -> sends exactly 1 consolidated telegram digest
        res_forced = runner.run_all(force=True, dry_run=False)
        self.assertEqual(res_forced["status"], "success")
        self.assertTrue(res_forced["telegram_dispatched"])
        self.assertEqual(mock_tg.call_count, 1)

    @patch("automations.qiita.runner.send_telegram_message")
    def test_runner_run_all_with_fresh_radar_dispatches_telegram(self, mock_tg):
        runner = QiitaRunner(self.mock_settings)
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.username = "aman179102"
        mock_client.get_authenticated_user_items.return_value = []
        mock_client.get_tag_items.return_value = [
            {
                "id": "fresh_qiita_item_999",
                "title": "Fresh Python Discovery",
                "url": "https://qiita.com/dev/items/fresh_qiita_item_999",
                "likes_count": 35,
                "stocks_count": 8,
                "user": {"id": "dev"},
                "tags": [{"name": "Python"}],
            }
        ]
        runner.client = mock_client
        runner.publisher.client = mock_client

        # Seed state with seen IDs empty and in cooldown
        initial_state = {
            "analytics": {"total_likes": 0, "total_stocks": 0, "total_views": 0},
            "seen_qiita_item_ids": [],
            "last_publish_ts": time.time(),
        }
        runner._save_state(initial_state)

        res = runner.run_all(force=False, dry_run=False)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["telegram_dispatched"])
        self.assertEqual(mock_tg.call_count, 1)

    def test_runner_standalone_radar_and_analytics(self):
        runner = QiitaRunner(self.mock_settings)
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_authenticated_user_items.return_value = [
            {"id": "a1", "title": "Article 1", "likes_count": 10, "stocks_count": 2, "page_views_count": 50}
        ]
        mock_client.get_tag_items.return_value = [
            {"id": "t1", "title": "Tag 1", "likes_count": 5, "stocks_count": 1, "user": {"id": "u1"}, "tags": [{"name": "Python"}]}
        ]
        runner.client = mock_client

        # Run radar
        res_radar = runner.run_radar(dry_run=True, notify_telegram=False)
        self.assertEqual(res_radar["status"], "success")
        self.assertEqual(len(res_radar["all_scanned"]), 1)

        # Run analytics
        res_analytics = runner.run_analytics(dry_run=True, notify_telegram=False)
        self.assertEqual(res_analytics["status"], "success")
        self.assertEqual(res_analytics["report"].total_likes, 10)

    # =========================================================================
    # 7. Server & Webhook Endpoints Tests
    # =========================================================================

    def test_server_health_endpoints_include_qiita(self):
        handler = DummyAutomationHandler("GET", "/health", settings=self.mock_settings)
        handler.do_GET()
        self.assertEqual(handler.status_code, 200)
        data = handler.get_response_json()
        endpoints = data.get("endpoints", [])
        self.assertIn("POST /trigger/qiita/article", endpoints)
        self.assertIn("POST /trigger/qiita/radar", endpoints)
        self.assertIn("POST /trigger/qiita/analytics", endpoints)
        self.assertIn("POST /trigger/qiita/all", endpoints)

    @patch("server._dispatch_automation_job")
    def test_server_post_qiita_triggers(self, mock_dispatch):
        routes = [
            ("/trigger/qiita/article", "qiita_article"),
            ("/trigger/qiita/radar", "qiita_radar"),
            ("/trigger/qiita/analytics", "qiita_analytics"),
            ("/trigger/qiita/all", "qiita"),
        ]
        for route, expected_action in routes:
            handler = DummyAutomationHandler("POST", route, body=b"{}", settings=self.mock_settings)
            handler.do_POST()
            self.assertEqual(handler.status_code, 202)
            resp = handler.get_response_json()
            self.assertEqual(resp.get("status"), "accepted")
            self.assertEqual(resp.get("action"), expected_action)

    @patch("server.send_telegram_message")
    @patch("server._dispatch_automation_job")
    def test_server_telegram_webhook_qiita_commands(self, mock_dispatch, mock_send_tg):
        commands = [
            ("/qiita_article", "qiita_article"),
            ("/qiita_radar", "qiita_radar"),
            ("/qiita_analytics", "qiita_analytics"),
            ("/qiita", "qiita"),
        ]
        for cmd, expected_action in commands:
            body = {"message": {"text": cmd}}
            _handle_telegram_webhook_async(body, self.mock_settings)
            mock_dispatch.assert_called_with(expected_action, self.mock_settings)
            mock_send_tg.assert_called()

    # =========================================================================
    # 8. main.py CLI Integration Tests
    # =========================================================================

    @patch("automations.qiita.runner.run_article")
    @patch("automations.qiita.runner.run_radar")
    @patch("automations.qiita.runner.run_analytics")
    @patch("automations.qiita.runner.run_all")
    def test_main_cli_actions(self, mock_all, mock_analytics, mock_radar, mock_article):
        mock_article.return_value = {"status": "success"}
        mock_radar.return_value = {"status": "success"}
        mock_analytics.return_value = {"status": "success"}
        mock_all.return_value = {"status": "success"}

        self.assertTrue(run_qiita_action("article", self.mock_settings))
        self.assertTrue(run_qiita_action("radar", self.mock_settings))
        self.assertTrue(run_qiita_action("analytics", self.mock_settings))
        self.assertTrue(run_qiita_action("all", self.mock_settings))


if __name__ == "__main__":
    unittest.main()

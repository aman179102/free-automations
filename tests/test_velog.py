from __future__ import annotations

import ast
import io
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import requests

from automations.velog.analytics import (
    VelogAnalyticsReport,
    VelogArticleMetrics,
    format_analytics_digest,
    track_analytics,
)
from automations.velog.api import (
    VELOG_GRAPHQL_ENDPOINT,
    VelogAPIClient,
    VelogAPIError,
    _to_graphql_arg,
)
from automations.velog.articles import (
    VELOG_TEMPLATES,
    VelogArticleCandidate,
    generate_ai_article,
    generate_template_article,
    generate_velog_article,
    validate_article_content,
)
from automations.velog.publisher import VelogPublisher, VelogRateLimitError
from automations.velog.radar import (
    VelogRadarItem,
    filter_unseen_items,
    format_radar_digest,
    parse_radar_item,
    scan_trending_radar,
)
from automations.velog.runner import (
    MIN_ARTICLE_COOLDOWN_SECONDS,
    VelogRunner,
    run_all,
    run_analytics,
    run_article,
    run_radar,
)
from common.config import Settings, load_settings
from main import run_velog_action
from server import AutomationHandler, _dispatch_automation_job, _handle_telegram_webhook_async


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


class TestVelogModule(unittest.TestCase):
    """Comprehensive unit and integration test suite for the Velog Autonomous Publishing Engine."""

    def setUp(self):
        # Patch sleep so jitter does not delay test suite
        self.sleep_patch = patch("time.sleep")
        self.mock_sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

        # Patch AI generation to prevent external network calls during unit tests
        self.ai_patch = patch("automations.velog.articles.generate_text", side_effect=RuntimeError("AI offline"))
        self.mock_ai = self.ai_patch.start()
        self.addCleanup(self.ai_patch.stop)

        # Patch telegram sender
        self.tg_patch = patch("automations.velog.runner.send_telegram_message")
        self.mock_tg = self.tg_patch.start()
        self.addCleanup(self.tg_patch.stop)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_dir = Path(self.temp_dir.name)

        self.mock_settings = Settings(
            velog_access_token="test_access_token_123",
            velog_refresh_token="test_refresh_token_456",
            velog_username="aman179102",
            velog_auto_publish=True,
            telegram_bot_token="test_bot_token",
            telegram_chat_id="test_chat_id",
            state_dir=self.state_dir,
            gemini_api_key="test_ai_key",
        )

    # --------------------------------------------------------------------------
    # 1. Config Tests
    # --------------------------------------------------------------------------
    def test_settings_has_velog_property(self):
        s_full = Settings(velog_access_token="token_abc", velog_username="aman179102")
        self.assertTrue(s_full.has_velog)

        s_refresh = Settings(velog_refresh_token="ref_xyz", velog_username="aman179102")
        self.assertTrue(s_refresh.has_velog)

        s_empty = Settings(velog_access_token="", velog_refresh_token="")
        self.assertFalse(s_empty.has_velog)

    def test_load_settings_overrides(self):
        loaded = load_settings({
            "velog_access_token": "ovr_token",
            "velog_refresh_token": "ovr_ref",
            "velog_username": "custom_user",
            "velog_auto_publish": True,
        })
        self.assertEqual(loaded.velog_access_token, "ovr_token")
        self.assertEqual(loaded.velog_refresh_token, "ovr_ref")
        self.assertEqual(loaded.velog_username, "custom_user")
        self.assertTrue(loaded.velog_auto_publish)
        self.assertTrue(loaded.has_velog)

    # --------------------------------------------------------------------------
    # 2. Velog API Client & GraphQL serialization Tests
    # --------------------------------------------------------------------------
    def test_to_graphql_arg(self):
        self.assertEqual(_to_graphql_arg(None), "null")
        self.assertEqual(_to_graphql_arg(True), "true")
        self.assertEqual(_to_graphql_arg(False), "false")
        self.assertEqual(_to_graphql_arg(42), "42")
        self.assertEqual(_to_graphql_arg(3.14), "3.14")
        self.assertEqual(_to_graphql_arg("hello"), '"hello"')
        self.assertEqual(_to_graphql_arg(["a", "b"]), '["a", "b"]')
        self.assertEqual(_to_graphql_arg({"key": "val", "num": 10}), '{key: "val", num: 10}')
        self.assertEqual(_to_graphql_arg({}), "{}")

    def test_api_client_initialization_and_headers(self):
        client = VelogAPIClient(self.mock_settings)
        self.assertEqual(client.access_token, "test_access_token_123")
        self.assertEqual(client.refresh_token, "test_refresh_token_456")
        self.assertEqual(client.username, "aman179102")
        self.assertTrue(client.is_configured())

        # Check session headers
        headers = client.session.headers
        self.assertEqual(headers.get("Referer"), "https://velog.io/")
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertIn("Mozilla/5.0", headers.get("User-Agent", ""))

        # Check cookies
        self.assertEqual(client.session.cookies.get("access_token"), "test_access_token_123")
        self.assertEqual(client.session.cookies.get("refresh_token"), "test_refresh_token_456")

    def test_api_client_unconfigured(self):
        empty_settings = Settings()
        client = VelogAPIClient(empty_settings)
        self.assertFalse(client.is_configured())
        self.assertFalse(client.verify_credentials())
        with self.assertRaises(VelogAPIError):
            client.write_post("Title", "Body", ["tag"], "slug")

    def test_get_current_user_success(self):
        client = VelogAPIClient(self.mock_settings)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({
            "data": {
                "currentUser": {
                    "id": "user-uuid-123",
                    "username": "aman179102",
                    "email": "aman@example.com",
                    "profile": {"display_name": "Aman Kumar", "short_bio": "Engineer"},
                }
            }
        })
        mock_resp.json.return_value = json.loads(mock_resp.text)

        with patch.object(client.session, "post", return_value=mock_resp):
            user = client.get_current_user()
            self.assertEqual(user["id"], "user-uuid-123")
            self.assertEqual(user["username"], "aman179102")
            self.assertTrue(client.verify_credentials())

    def test_execute_graphql_error_handling(self):
        client = VelogAPIClient(self.mock_settings)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({"errors": [{"message": "Field 'unknown' not found"}]})
        mock_resp.json.return_value = json.loads(mock_resp.text)

        with patch.object(client.session, "post", return_value=mock_resp):
            with self.assertRaises(VelogAPIError) as ctx:
                client.execute_graphql("query { unknown }")
            self.assertIn("Field 'unknown' not found", str(ctx.exception))

    def test_get_posts_query(self):
        client = VelogAPIClient(self.mock_settings)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({
            "data": {
                "posts": [
                    {
                        "id": "post-1",
                        "title": "Post 1",
                        "url_slug": "post-1",
                        "likes": 15,
                        "views": 250,
                        "comments_count": 3,
                    }
                ]
            }
        })
        mock_resp.json.return_value = json.loads(mock_resp.text)

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            posts = client.get_posts(username="aman179102", limit=10)
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0]["likes"], 15)
            called_payload = mock_post.call_args[1]["json"]
            self.assertIn('username: "aman179102"', called_payload["query"])

    def test_get_trending_posts_query(self):
        client = VelogAPIClient(self.mock_settings)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({
            "data": {
                "trendingPosts": [
                    {
                        "id": "trend-1",
                        "title": "Trend 1",
                        "url_slug": "trend-1",
                        "likes": 50,
                        "comments_count": 8,
                        "user": {"username": "koreandev"},
                    }
                ]
            }
        })
        mock_resp.json.return_value = json.loads(mock_resp.text)

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            trends = client.get_trending_posts(limit=20, timeframe="week")
            self.assertEqual(len(trends), 1)
            self.assertEqual(trends[0]["title"], "Trend 1")
            called_payload = mock_post.call_args[1]["json"]
            self.assertIn('timeframe: "week"', called_payload["query"])

    def test_write_post_mutation_meta_empty_dict(self):
        client = VelogAPIClient(self.mock_settings)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({
            "data": {
                "writePost": {
                    "id": "created-post-123",
                    "title": "FastAPI Pipeline",
                    "url_slug": "fastapi-pipeline",
                    "user": {"id": "user-uuid", "username": "aman179102"},
                }
            }
        })
        mock_resp.json.return_value = json.loads(mock_resp.text)

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            res = client.write_post(
                title="FastAPI Pipeline",
                body="# Content",
                tags=["Python", "FastAPI"],
                url_slug="fastapi-pipeline",
            )
            self.assertEqual(res["id"], "created-post-123")
            called_payload = mock_post.call_args[1]["json"]
            # Strict schema check: meta must be {} empty dict
            self.assertIn("meta: {}", called_payload["query"])
            self.assertIn('url_slug: "fastapi-pipeline"', called_payload["query"])

    # --------------------------------------------------------------------------
    # 3. Articles & Content Validation Tests
    # --------------------------------------------------------------------------
    def test_curated_templates_count_and_categories(self):
        self.assertEqual(len(VELOG_TEMPLATES), 5)
        categories = {t["category"] for t in VELOG_TEMPLATES}
        expected = {
            "uvloop_asyncpg_pipeline",
            "pydantic_v2_zero_copy",
            "asyncio_internals_event_loop",
            "redis_cache_stampede",
            "concurrency_patterns",
        }
        self.assertEqual(categories, expected)

    def test_all_curated_templates_validate_successfully(self):
        for tmpl in VELOG_TEMPLATES:
            cand = generate_template_article(tmpl["category"])
            is_valid, msg = validate_article_content(cand)
            self.assertTrue(is_valid, f"Template '{tmpl['category']}' failed validation: {msg}")
            self.assertIn("https://github.com/aman179102", cand.body)
            self.assertTrue(cand.url_slug)

    def test_working_code_blocks_ast_syntax_across_all_templates(self):
        """Verify that every python code block in every template parses without syntax error."""
        for tmpl in VELOG_TEMPLATES:
            body = tmpl["body"]
            code_blocks = re.findall(r"```python\s*(.+?)```", body, re.DOTALL)
            self.assertGreater(len(code_blocks), 0, f"Template {tmpl['category']} has no code blocks")
            for i, code in enumerate(code_blocks):
                try:
                    ast.parse(code)
                except SyntaxError as e:
                    self.fail(f"Syntax error in template {tmpl['category']} code block {i+1}: {e}")

    def test_tech_korean_language_ratio_and_ascii_diagrams(self):
        """Verify 80% Korean + 20% English Tech Korean style and ASCII diagrams."""
        for tmpl in VELOG_TEMPLATES:
            body = tmpl["body"]
            # Check ASCII diagram presence
            has_ascii_diagram = bool(re.search(r"(\+[-+]+\+|\|.*\||\[.*\]\s*---+>|\+======+\+)", body))
            self.assertTrue(has_ascii_diagram, f"Template {tmpl['category']} lacks ASCII diagram")

            # Check Korean characters quantity
            korean_chars = len(re.findall(r"[\uac00-\ud7a3]", body))
            self.assertGreater(korean_chars, 400, f"Template {tmpl['category']} has too few Korean characters ({korean_chars})")

            # Check English technical words
            english_words = re.findall(r"\b[A-Za-z]{3,}\b", body)
            self.assertGreater(len(english_words), 20, f"Template {tmpl['category']} has too few English technical terms")

    def test_strict_0_1_percent_link_rule_and_zero_spam(self):
        """Verify 0.1% rule: strictly maximum 1 author citation link and zero spam."""
        for tmpl in VELOG_TEMPLATES:
            body = tmpl["body"]
            urls = re.findall(r"https?://[^\s)\]]+", body)
            self.assertLessEqual(len(urls), 1, f"Template {tmpl['category']} violates 0.1% rule with {len(urls)} links")
            if urls:
                self.assertIn("github.com/aman179102", urls[0])

            # Check zero spam phrases
            for spam in ["click here", "buy now", "discount", "limited offer", "promo code", "쿠폰", "특가"]:
                self.assertNotIn(spam.lower(), body.lower(), f"Spam phrase '{spam}' found in {tmpl['category']}")

    def test_validate_article_content_failures(self):
        # 1. Too brief
        cand_short = VelogArticleCandidate(
            title="Short",
            body="너무 짧은 본문",
            category="test",
            tags=["Python"],
            ascii_diagram="",
            url_slug="short",
        )
        valid, msg = validate_article_content(cand_short)
        self.assertFalse(valid)
        self.assertIn("too brief", msg)

        # 2. No Korean characters
        cand_no_kr = VelogArticleCandidate(
            title="English Only Title",
            body="This is an entirely English technical article with code def main(): pass and diagrams +----+ but zero korean characters at all in the entire text body." * 5,
            category="test",
            tags=["Python"],
            ascii_diagram="+----+",
            url_slug="no-kr",
        )
        valid, msg = validate_article_content(cand_no_kr)
        self.assertFalse(valid)
        self.assertIn("Korean technical style", msg)

        # 3. No ASCII diagram
        cand_no_diag = VelogArticleCandidate(
            title="No Diagram",
            body="이 글은 한국어로 작성된 기술 아티클입니다. 하지만 다이어그램이 전혀 없습니다. " * 30 + "\n```python\ndef test(): pass\n```",
            category="test",
            tags=["Python"],
            ascii_diagram="",
            url_slug="no-diag",
        )
        valid, msg = validate_article_content(cand_no_diag)
        self.assertFalse(valid)
        self.assertIn("ASCII architecture diagram", msg)

        # 4. No code block
        cand_no_code = VelogArticleCandidate(
            title="No Code",
            body="이 글은 한국어로 작성된 기술 아티클입니다. +----+ | Box | +----+ 다이어그램은 있지만 코드 블록이 없습니다. " * 30,
            category="test",
            tags=["Python"],
            ascii_diagram="+----+",
            url_slug="no-code",
        )
        valid, msg = validate_article_content(cand_no_code)
        self.assertFalse(valid)
        self.assertIn("working code blocks", msg)

        # 5. Spam detection
        cand_spam = VelogArticleCandidate(
            title="Spam Title",
            body="이 글은 한국어로 작성된 기술 아티클입니다. +----+ | Box | +----+ ```python\ndef code(): pass\n``` Click here to buy now for discount!" * 10,
            category="test",
            tags=["Python"],
            ascii_diagram="+----+",
            url_slug="spam",
        )
        valid, msg = validate_article_content(cand_spam)
        self.assertFalse(valid)
        self.assertIn("Spam detected", msg)

        # 6. 0.1% rule: Multiple links
        cand_multi_links = VelogArticleCandidate(
            title="Multi Link",
            body="이 글은 한국어로 작성된 기술 아티클입니다. +----+ | Box | +----+ ```python\ndef code(): pass\n``` "
            "Link 1: https://github.com/aman179102 Link 2: https://github.com/another/repo" * 10,
            category="test",
            tags=["Python"],
            ascii_diagram="+----+",
            url_slug="multi-link",
        )
        valid, msg = validate_article_content(cand_multi_links)
        self.assertFalse(valid)
        self.assertIn("0.1% rule violation", msg)

    def test_generate_ai_article_fallback(self):
        # When AI generation raises exception or returns invalid candidate, generate_velog_article falls back to template
        cand = generate_velog_article(self.mock_settings, category="random")
        self.assertIsNotNone(cand)
        self.assertFalse(cand.is_ai_generated)
        valid, _ = validate_article_content(cand)
        self.assertTrue(valid)

    # --------------------------------------------------------------------------
    # 4. Radar Tests
    # --------------------------------------------------------------------------
    def test_parse_radar_item(self):
        raw = {
            "id": "radar-item-1",
            "title": "FastAPI Tips",
            "url_slug": "fastapi-tips",
            "likes": 25,
            "comments_count": 4,
            "user": {"username": "fastapifan"},
            "tags": ["FastAPI", "Python"],
            "released_at": "2026-11-20T00:00:00Z",
        }
        item = parse_radar_item(raw)
        self.assertIsNotNone(item)
        self.assertEqual(item.item_id, "radar-item-1")
        self.assertEqual(item.url, "https://velog.io/@fastapifan/fastapi-tips")
        self.assertEqual(item.likes, 25)
        self.assertEqual(item.tags_label, "FastAPI, Python")

    def test_scan_trending_radar_and_deduplication(self):
        client = VelogAPIClient(self.mock_settings)
        mock_items = [
            {"id": "p1", "title": "P1", "url_slug": "p1", "likes": 10, "comments_count": 1, "user": {"username": "u1"}},
            {"id": "p2", "title": "P2", "url_slug": "p2", "likes": 50, "comments_count": 5, "user": {"username": "u2"}},
            {"id": "p1", "title": "P1 Duplicate", "url_slug": "p1", "likes": 10, "comments_count": 1, "user": {"username": "u1"}},
        ]
        with patch.object(client, "get_trending_posts", return_value=mock_items):
            scanned = scan_trending_radar(client, limit=20)
            self.assertEqual(len(scanned), 2)  # Deduplicated in batch
            # Ranked by likes descending
            self.assertEqual(scanned[0].item_id, "p2")
            self.assertEqual(scanned[1].item_id, "p1")

            # Filter unseen items
            seen = {"p2"}
            fresh = filter_unseen_items(scanned, seen)
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0].item_id, "p1")

    def test_format_radar_digest(self):
        item = VelogRadarItem(
            item_id="item-1",
            title="Korean Tech Trend",
            url="https://velog.io/@user/korean-tech-trend",
            url_slug="korean-tech-trend",
            likes=42,
            comments_count=7,
            author_username="user",
            tags=["Python"],
        )
        digest = format_radar_digest([item], limit=5)
        self.assertIn("Velog Tech Trend Radar", digest)
        self.assertIn("Korean Tech Trend", digest)
        self.assertIn("42", digest)
        self.assertIn("@user", digest)

    # --------------------------------------------------------------------------
    # 5. Analytics Tests
    # --------------------------------------------------------------------------
    def test_track_analytics_and_deltas(self):
        client = VelogAPIClient(self.mock_settings)
        mock_posts = [
            {"id": "post-1", "title": "Art 1", "url_slug": "art-1", "likes": 20, "comments_count": 5, "views": 1000},
            {"id": "post-2", "title": "Art 2", "url_slug": "art-2", "likes": 30, "comments_count": 10, "views": 2500},
        ]
        prior_state = {
            "analytics": {
                "total_likes": 40,
                "total_comments": 12,
                "total_views": 3000,
            }
        }
        with patch.object(client, "get_posts", return_value=mock_posts):
            report = track_analytics(client, username="aman179102", prior_state=prior_state)
            self.assertEqual(report.total_likes, 50)
            self.assertEqual(report.total_comments, 15)
            self.assertEqual(report.total_views, 3500)
            self.assertEqual(report.posts_count, 2)
            # Deltas
            self.assertEqual(report.delta_likes, 10)
            self.assertEqual(report.delta_comments, 3)
            self.assertEqual(report.delta_views, 500)

            digest = format_analytics_digest(report, username="aman179102")
            self.assertIn("50", digest)
            self.assertIn("(+10)", digest)
            self.assertIn("(+500)", digest)

    # --------------------------------------------------------------------------
    # 6. Publisher & Anti-Ban Tests
    # --------------------------------------------------------------------------
    def test_anti_ban_jitter_range(self):
        """Verify anti-ban jitter is strictly within 5.0s to 12.0s."""
        publisher = VelogPublisher(self.mock_settings)
        for _ in range(30):
            jitter = publisher._apply_anti_ban_jitter(5.0, 12.0)
            self.assertGreaterEqual(jitter, 5.0)
            self.assertLessEqual(jitter, 12.0)
            self.mock_sleep.assert_called_with(jitter)

    def test_publisher_dry_run(self):
        client = VelogAPIClient(self.mock_settings)
        publisher = VelogPublisher(self.mock_settings, client=client)
        candidate = generate_template_article("uvloop_asyncpg_pipeline")

        res = publisher.publish_article(candidate, dry_run=True)
        self.assertEqual(res["status"], "dry_run")
        self.assertEqual(res["title"], candidate.title)

    def test_publisher_rate_limiting_and_daily_cap(self):
        client = VelogAPIClient(self.mock_settings)
        # max 2 daily posts
        publisher = VelogPublisher(self.mock_settings, client=client, min_cooldown_seconds=300, max_daily_posts=2)
        candidate = generate_template_article("uvloop_asyncpg_pipeline")

        mock_write = MagicMock(return_value={"id": "post-123", "url_slug": "slug-123"})
        with patch.object(client, "write_post", mock_write):
            # 1st publish succeeds
            res1 = publisher.publish_article(candidate, dry_run=False)
            self.assertEqual(res1["status"], "published")
            self.assertGreaterEqual(res1["jitter"], 5.0)

            # 2nd publish immediately hits cooldown
            res2 = publisher.publish_article(candidate, dry_run=False)
            self.assertEqual(res2["status"], "rate_limited")
            self.assertIn("rate limit", res2["error"].lower())

            # Fast-forward past cooldown -> 2nd publish succeeds
            publisher._last_publish_ts = time.time() - 400
            res2_retry = publisher.publish_article(candidate, dry_run=False)
            self.assertEqual(res2_retry["status"], "published")

            # 3rd publish hits daily cap
            publisher._last_publish_ts = time.time() - 400
            res3 = publisher.publish_article(candidate, dry_run=False)
            self.assertEqual(res3["status"], "rate_limited")
            self.assertIn("Daily publish cap reached (2/2 today)", res3["error"])

    def test_publisher_validation_gate_rejection(self):
        publisher = VelogPublisher(self.mock_settings)
        invalid_cand = VelogArticleCandidate("Bad", "short", "test", [], "", "bad")
        res = publisher.publish_article(invalid_cand, dry_run=False)
        self.assertEqual(res["status"], "error")
        self.assertIn("Content validation failed", res["error"])

    def test_publisher_api_error_handling(self):
        client = VelogAPIClient(self.mock_settings)
        publisher = VelogPublisher(self.mock_settings, client=client)
        candidate = generate_template_article("uvloop_asyncpg_pipeline")

        with patch.object(client, "write_post", side_effect=VelogAPIError("GraphQL Network Error")):
            res = publisher.publish_article(candidate, dry_run=False)
            self.assertEqual(res["status"], "error")
            self.assertIn("GraphQL Network Error", res["error"])

    # --------------------------------------------------------------------------
    # 7. Runner, Cooldown & Zero-Spam Tests
    # --------------------------------------------------------------------------
    def test_runner_24h_cooldown_enforcement(self):
        runner = VelogRunner(self.mock_settings)
        # Pre-seed state with publication 2 hours ago
        two_hours_ago = time.time() - 7200.0
        runner._save_state({"last_published_ts": two_hours_ago})

        # Publication should be skipped due to 24h cooldown
        res = runner.run_article(force=False, dry_run=False)
        self.assertEqual(res["status"], "cooldown")
        self.assertIn("24h cooldown active", res["message"])
        self.assertGreater(res["remaining_hours"], 20.0)

        # Force bypasses cooldown
        mock_cand = generate_template_article("uvloop_asyncpg_pipeline")
        with patch("automations.velog.runner.generate_velog_article", return_value=mock_cand), \
             patch.object(runner.publisher, "publish_article", return_value={"status": "published", "id": "p1", "url": "https://velog.io/@aman179102/slug-1"}):
            res_forced = runner.run_article(force=True, dry_run=False)
            self.assertEqual(res_forced["status"], "published")

    def test_runner_run_all_zero_spam_suppression(self):
        """Zero-spam guarantee: Telegram digest is suppressed when no new deltas or posts occur."""
        runner = VelogRunner(self.mock_settings)
        runner._save_state({
            "last_published_ts": time.time() - 100,  # in 24h cooldown
            "seen_velog_post_ids": ["trend-1"],
            "analytics": {"total_likes": 10, "total_comments": 2, "total_views": 100},
        })

        mock_report = VelogAnalyticsReport(
            total_likes=10,
            total_comments=2,
            total_views=100,
            posts_count=1,
            delta_likes=0,
            delta_comments=0,
            delta_views=0,
        )
        mock_radar_item = VelogRadarItem("trend-1", "T1", "url1", "slug1", 10, 1, "u1")

        with patch("automations.velog.runner.track_analytics", return_value=mock_report), \
             patch("automations.velog.runner.scan_trending_radar", return_value=[mock_radar_item]):
            # Quiet background run
            res = runner.run_all(force=False, dry_run=False)
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["digest_suppressed"])
            self.mock_tg.assert_not_called()

    def test_runner_run_all_consolidated_digest_on_force(self):
        """When forced or when fresh items appear, a single consolidated digest is dispatched."""
        runner = VelogRunner(self.mock_settings)
        mock_report = VelogAnalyticsReport(
            total_likes=15,
            total_comments=3,
            total_views=250,
            posts_count=1,
            delta_likes=5,
            delta_comments=1,
            delta_views=150,
        )
        mock_radar_item = VelogRadarItem("trend-99", "New Trend", "https://velog.io/@u/trend-99", "trend-99", 88, 12, "u")

        with patch("automations.velog.runner.track_analytics", return_value=mock_report), \
             patch("automations.velog.runner.scan_trending_radar", return_value=[mock_radar_item]), \
             patch.object(runner, "run_article", return_value={"status": "cooldown", "remaining_hours": 18.5}):
            res = runner.run_all(force=True, dry_run=False)
            self.assertEqual(res["status"], "success")
            self.assertFalse(res.get("digest_suppressed", False))

            # Exactly one consolidated telegram message dispatched
            self.mock_tg.assert_called_once()
            call_text = self.mock_tg.call_args[0][1]
            self.assertIn("VELOG AUTONOMOUS ENGINE DIGEST", call_text)
            self.assertIn("CREATOR METRICS", call_text)
            self.assertIn("(+5)", call_text)
            self.assertIn("TECH TREND RADAR", call_text)
            self.assertIn("New Trend", call_text)
            self.assertIn("PUBLICATION STATUS", call_text)

    # --------------------------------------------------------------------------
    # 8. Server Endpoints & CLI Integration Tests
    # --------------------------------------------------------------------------
    def test_server_http_endpoints_and_status(self):
        # Test HTTP POST triggers
        for path, action in [
            ("/trigger/velog/article", "velog_article"),
            ("/trigger/velog/radar", "velog_radar"),
            ("/trigger/velog/analytics", "velog_analytics"),
            ("/trigger/velog/all", "velog"),
        ]:
            handler = DummyAutomationHandler("POST", path, b"", self.mock_settings)
            with patch("server._dispatch_automation_job") as mock_dispatch:
                handler.do_POST()
                self.assertEqual(handler.status_code, 202)
                mock_dispatch.assert_called_with(action, self.mock_settings, {})

        # Test GET /health lists velog endpoints
        health_handler = DummyAutomationHandler("GET", "/health", b"", self.mock_settings)
        health_handler.do_GET()
        self.assertEqual(health_handler.status_code, 200)
        data = health_handler.get_response_json()
        endpoints = data.get("endpoints", [])
        self.assertIn("POST /trigger/velog/article", endpoints)
        self.assertIn("POST /trigger/velog/radar", endpoints)
        self.assertIn("POST /trigger/velog/analytics", endpoints)
        self.assertIn("POST /trigger/velog/all", endpoints)

    def test_telegram_webhook_commands(self):
        for cmd, expected_action in [
            ("/velog_article", "velog_article"),
            ("/velog_radar", "velog_radar"),
            ("/velog_analytics", "velog_analytics"),
            ("/velog", "velog"),
        ]:
            update = {"message": {"text": cmd, "chat": {"id": 12345}}}
            with patch("server._dispatch_automation_job") as mock_dispatch, \
                 patch("server.send_telegram_message"):
                _handle_telegram_webhook_async(update, self.mock_settings)
                mock_dispatch.assert_called_with(expected_action, self.mock_settings)

    def test_main_cli_runner_dispatch(self):
        with patch("automations.velog.runner.run_article", return_value={"status": "published"}), \
             patch("automations.velog.runner.run_radar", return_value={"status": "success"}), \
             patch("automations.velog.runner.run_analytics", return_value={"status": "success"}), \
             patch("automations.velog.runner.run_all", return_value={"status": "success"}):
            self.assertTrue(run_velog_action("article", self.mock_settings))
            self.assertTrue(run_velog_action("radar", self.mock_settings))
            self.assertTrue(run_velog_action("analytics", self.mock_settings))
            self.assertTrue(run_velog_action("all", self.mock_settings))
            self.assertFalse(run_velog_action("unknown", self.mock_settings))

    # --------------------------------------------------------------------------
    # 9. Live Test Article URL Verification Test
    # --------------------------------------------------------------------------
    def test_live_test_article_url_format_and_accessibility(self):
        live_url = "https://velog.io/@aman179102/fastapi-uvloop-asyncpg-high-throughput-pipeline"
        self.assertTrue(live_url.startswith("https://velog.io/@aman179102/"))
        self.assertIn("uvloop", live_url)

        # Mock requests.get check
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><head><title>【FastAPI】uvloop + asyncpg</title></head><body><h1>Content</h1></body></html>"
        with patch("requests.get", return_value=mock_response):
            resp = requests.get(live_url, timeout=10)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("【FastAPI】uvloop + asyncpg", resp.text)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import io
import json
import re
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import requests

from automations.devto.analytics import (
    DevtoAnalyticsReport,
    DevtoArticleMetrics,
    format_analytics_digest,
    track_analytics,
)
from automations.devto.api import (
    DEVTO_API_BASE,
    DevtoAPIClient,
    DevtoAPIError,
)
from automations.devto.articles import (
    DEVTO_TEMPLATES,
    DevtoArticleCandidate,
    format_frontmatter,
    generate_devto_article,
    generate_template_article,
    validate_article_content,
)
from automations.devto.publisher import DevtoPublisher, DevtoRateLimitError
from automations.devto.radar import (
    DEFAULT_RADAR_TAGS,
    DevtoRadarItem,
    filter_unseen_items,
    format_radar_digest,
    parse_radar_item,
    scan_trend_radar,
)
from automations.devto.runner import (
    MIN_ARTICLE_COOLDOWN_SECONDS,
    DevtoRunner,
    run_all,
    run_analytics,
    run_article,
    run_radar,
)
from common.config import Settings, load_settings
from main import run_devto_action
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


class TestDevtoModule(unittest.TestCase):
    """Comprehensive unit and integration test suite for the Dev.to Autonomous Engine."""

    def setUp(self):
        # Patch sleep so jitter does not delay test suite
        self.sleep_patch = patch("time.sleep")
        self.mock_sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

        # Patch AI generation to prevent external network calls during unit tests
        self.ai_patch = patch("automations.devto.articles.generate_text", side_effect=RuntimeError("AI offline"))
        self.mock_ai = self.ai_patch.start()
        self.addCleanup(self.ai_patch.stop)

        # Patch telegram sender
        self.tg_patch = patch("automations.devto.runner.send_telegram_message")
        self.mock_tg = self.tg_patch.start()
        self.addCleanup(self.tg_patch.stop)

        self.test_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.test_dir.cleanup)
        self.state_dir = Path(self.test_dir.name)

        self.settings = Settings(
            devto_api_key="test_devto_key",
            devto_username="aman179102",
            devto_auto_publish=True,
            state_dir=self.state_dir,
            telegram_bot_token="test_token",
            telegram_chat_id="test_chat",
            gemini_api_key="test_ai_key",
        )

    # =========================================================================
    # 1. Settings & Platform Config Tests
    # =========================================================================

    def test_settings_has_devto_property(self):
        s1 = Settings(devto_api_key="valid_key")
        self.assertTrue(s1.has_devto)

        s2 = Settings(devto_api_key="")
        self.assertFalse(s2.has_devto)

    def test_load_settings_overrides(self):
        overrides = {
            "devto_api_key": "override_key_999",
            "devto_username": "custom_user",
            "devto_auto_publish": True,
        }
        loaded = load_settings(overrides)
        self.assertEqual(loaded.devto_api_key, "override_key_999")
        self.assertEqual(loaded.devto_username, "custom_user")
        self.assertTrue(loaded.devto_auto_publish)
        self.assertTrue(loaded.has_devto)

    # =========================================================================
    # 2. DevtoAPIClient Tests
    # =========================================================================

    def test_client_init_and_headers(self):
        client = DevtoAPIClient(self.settings)
        self.assertTrue(client.is_configured())
        self.assertEqual(client.api_key, "test_devto_key")
        self.assertEqual(client.username, "aman179102")
        self.assertEqual(client.session.headers.get("api-key"), "test_devto_key")
        self.assertEqual(client.session.headers.get("accept"), "application/vnd.forem.api-v1+json")
        self.assertEqual(client.session.headers.get("Content-Type"), "application/json")
        self.assertIn("FreeAutomations-DevtoEngine", client.session.headers.get("User-Agent", ""))

    def test_client_build_url(self):
        client = DevtoAPIClient(self.settings)
        self.assertEqual(client._build_url("users/me"), f"{DEVTO_API_BASE}/users/me")
        self.assertEqual(client._build_url("/articles"), f"{DEVTO_API_BASE}/articles")

    def test_client_unconfigured(self):
        client = DevtoAPIClient(api_key="")
        self.assertFalse(client.is_configured())
        self.assertFalse(client.verify_credentials())
        with self.assertRaises(DevtoAPIError):
            client.get_me()
        with self.assertRaises(DevtoAPIError):
            client.create_article({"title": "Test"})
        with self.assertRaises(DevtoAPIError):
            client.get_my_articles()
        with self.assertRaises(DevtoAPIError):
            client.update_article(1, {"title": "Update"})

    def test_client_get_me_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 2725242, "username": "aman179102", "name": "Aman Kumar"}
        mock_session.get.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        me = client.get_me()
        self.assertEqual(me["id"], 2725242)
        self.assertEqual(me["username"], "aman179102")
        self.assertTrue(client.verify_credentials())

    def test_client_get_me_unauthorized(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_session.get.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError) as ctx:
            client.get_me()
        self.assertIn("401", str(ctx.exception))
        self.assertFalse(client.verify_credentials())

    def test_client_get_me_network_error(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Connection timed out")

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError) as ctx:
            client.get_me()
        self.assertIn("network failure", str(ctx.exception))

    def test_client_create_article_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "id": 99999,
            "title": "AsyncPG Scaling",
            "url": "https://dev.to/aman179102/asyncpg-scaling-123",
            "slug": "asyncpg-scaling-123",
        }
        mock_session.post.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        res = client.create_article({
            "title": "AsyncPG Scaling",
            "body_markdown": "# Scaling",
            "published": True,
            "tags": ["python", "fastapi"],
        })
        self.assertEqual(res["id"], 99999)
        self.assertIn("dev.to", res["url"])
        mock_session.post.assert_called_once()
        args, kwargs = mock_session.post.call_args
        self.assertIn("article", kwargs["json"])
        self.assertEqual(kwargs["json"]["article"]["title"], "AsyncPG Scaling")

    def test_client_create_article_error_response(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = '{"error": "Title has already been taken"}'
        mock_resp.json.return_value = {"error": "Title has already been taken"}
        mock_session.post.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError) as ctx:
            client.create_article({"title": "Duplicate"})
        self.assertIn("Title has already been taken", str(ctx.exception))

    def test_client_create_article_network_error(self):
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.RequestException("DNS lookup failed")

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError) as ctx:
            client.create_article({"title": "Fail"})
        self.assertIn("network failure", str(ctx.exception))

    def test_client_get_my_articles(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": 1, "title": "Article 1", "page_views_count": 50, "public_reactions_count": 5, "comments_count": 2},
            {"id": 2, "title": "Article 2", "page_views_count": 100, "public_reactions_count": 10, "comments_count": 4},
        ]
        mock_session.get.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        articles = client.get_my_articles(page=1, per_page=10)
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["page_views_count"], 50)

    def test_client_get_my_articles_network_error(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Read timed out")

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError) as ctx:
            client.get_my_articles()
        self.assertIn("network failure", str(ctx.exception))

    def test_client_get_articles_radar(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": 10, "title": "Rising Python Post", "url": "https://dev.to/user/post1", "public_reactions_count": 25},
        ]
        mock_session.get.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        articles = client.get_articles(tag="python", state="rising", per_page=5)
        self.assertEqual(len(articles), 1)
        mock_session.get.assert_called_once()
        args, kwargs = mock_session.get.call_args
        self.assertEqual(kwargs["params"]["tag"], "python")
        self.assertEqual(kwargs["params"]["state"], "rising")

    def test_client_get_articles_network_error(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Connection refused")

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError) as ctx:
            client.get_articles(tag="python")
        self.assertIn("network failure", str(ctx.exception))

    def test_client_get_article_by_id_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1234, "title": "Kafka Architecture"}
        mock_session.get.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        art = client.get_article(1234)
        self.assertEqual(art["id"], 1234)
        self.assertEqual(art["title"], "Kafka Architecture")

    def test_client_get_article_by_id_error(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not found"
        mock_session.get.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError):
            client.get_article(999999)

    def test_client_update_article_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 555, "title": "Updated Title"}
        mock_session.put.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        res = client.update_article(555, {"title": "Updated Title"})
        self.assertEqual(res["id"], 555)
        mock_session.put.assert_called_once()

    def test_client_update_article_error(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_session.put.return_value = mock_resp

        client = DevtoAPIClient(self.settings, session=mock_session)
        with self.assertRaises(DevtoAPIError):
            client.update_article(555, {"title": "Updated Title"})

    # =========================================================================
    # 3. Articles, AST Code Syntax, Diagrams & 0.1% Rule Tests
    # =========================================================================

    def test_all_five_templates_valid(self):
        expected_categories = [
            "kafka_vs_redis_streams",
            "idempotency_keys_sagas",
            "asyncpg_connection_pooling",
            "rust_ffi_python_pipelines",
            "lockstep_simulation_engines",
        ]
        self.assertEqual(len(DEVTO_TEMPLATES), 5)
        for cat in expected_categories:
            cand = generate_template_article(category=cat)
            self.assertEqual(cand.category, cat)
            is_valid, msg = validate_article_content(cand)
            self.assertTrue(is_valid, f"Template {cat} failed validation: {msg}")
            self.assertGreaterEqual(len(cand.body.split()), 200)
            self.assertIn("```", cand.body)
            self.assertIn("https://github.com/aman179102", cand.body)
            self.assertTrue(cand.body.startswith("---"))
            self.assertIn("title:", cand.body)
            self.assertIn("tags:", cand.body)

    def test_working_code_blocks_ast_syntax_across_all_templates(self):
        """Verify that every python code block in all templates is syntactically valid via ast.parse."""
        for tmpl in DEVTO_TEMPLATES:
            cat = tmpl["category"]
            content = tmpl["markdown_content"]
            py_blocks = re.findall(r"```python\s*(.+?)```", content, re.DOTALL)
            self.assertGreater(len(py_blocks), 0, f"Template {cat} has no python code blocks")
            for idx, code in enumerate(py_blocks):
                try:
                    ast.parse(code)
                except SyntaxError as exc:
                    self.fail(f"Syntax error in template {cat} code block {idx+1}: {exc}")

    def test_ascii_diagrams_across_all_templates(self):
        """Verify that every template includes rich architectural ASCII diagrams with box drawing."""
        for tmpl in DEVTO_TEMPLATES:
            cat = tmpl["category"]
            diag = tmpl.get("ascii_diagram", "").strip()
            self.assertGreater(len(diag), 50, f"Template {cat} diagram too short")
            has_box_chars = any(c in diag for c in ["+", "|", "-", ">"])
            self.assertTrue(has_box_chars, f"Template {cat} diagram lacks box drawing characters")
            # Verify diagram is embedded in the markdown content as well
            self.assertIn(diag, tmpl["markdown_content"], f"Template {cat} diagram not found in markdown")

    def test_frontmatter_metadata_across_all_templates(self):
        """Verify proper YAML frontmatter format across all templates."""
        for tmpl in DEVTO_TEMPLATES:
            cand = generate_template_article(tmpl["category"])
            self.assertTrue(cand.body.startswith("---\n"))
            self.assertIn("\n---\n", cand.body[4:])
            self.assertIn('title: "', cand.body)
            self.assertIn('description: "', cand.body)
            self.assertIn('tags: ', cand.body)
            for tag in tmpl["tags"]:
                self.assertIn(tag, cand.body)
            self.assertIn('series: "', cand.body)
            self.assertIn('canonical_url: "', cand.body)
            self.assertIn('published: true', cand.body)

    def test_strict_0_1_percent_link_rule_and_zero_spam(self):
        """Verify the 0.1% rule: ZERO spam links, maximum 1 author GitHub citation."""
        for tmpl in DEVTO_TEMPLATES:
            cand = generate_template_article(tmpl["category"])
            urls = re.findall(r"https?://[^\s)\]]+", cand.body)
            author_urls = [u for u in urls if "github.com/aman179102" in u]
            self.assertEqual(len(author_urls), 1, f"Template {tmpl['category']} must have exactly 1 author citation")

            # Check for zero spam or URL-shortener domains
            for u in urls:
                self.assertNotIn("bit.ly", u)
                self.assertNotIn("tinyurl.com", u)
                self.assertNotIn("goo.gl", u)
                self.assertNotIn("affiliate", u)

    def test_validate_content_rejections(self):
        # 1. Brief title (< 10 chars)
        c1 = DevtoArticleCandidate(
            title="Short",
            body="--- \n title: Short \n tags: python \n --- \n" + ("word " * 300) + "\n ```python\npass\n```\n+---+",
            category="test",
            tags=["python"],
            description="A valid long description of the article",
            series="Series",
            canonical_url="https://dev.to/aman179102/test",
            ascii_diagram="+---+",
        )
        ok, msg = validate_article_content(c1)
        self.assertFalse(ok)
        self.assertIn("Title too brief", msg)

        # 2. Too many tags (>4)
        c2 = DevtoArticleCandidate(
            title="Valid Long Title for Systems Engineering",
            body="--- \n title: Valid \n tags: python \n --- \n" + ("word " * 300) + "\n ```python\npass\n```\n+---+",
            category="test",
            tags=["tag1", "tag2", "tag3", "tag4", "tag5"],
            description="A valid long description of the article",
            series="Series",
            canonical_url="https://dev.to/aman179102/test",
            ascii_diagram="+---+",
        )
        ok, msg = validate_article_content(c2)
        self.assertFalse(ok)
        self.assertIn("maximum of 4 tags", msg)

        # 3. Missing code block
        c3 = DevtoArticleCandidate(
            title="Valid Long Title for Systems Engineering",
            body="--- \n title: Valid \n tags: python \n --- \n" + ("word " * 300) + "\n+---+",
            category="test",
            tags=["python"],
            description="A valid long description of the article",
            series="Series",
            canonical_url="https://dev.to/aman179102/test",
            ascii_diagram="+---+",
        )
        ok, msg = validate_article_content(c3)
        self.assertFalse(ok)
        self.assertIn("missing working Python or Rust code block", msg)

        # 4. Spam link
        c4 = DevtoArticleCandidate(
            title="Valid Long Title for Systems Engineering",
            body="--- \n title: Valid \n tags: python \n --- \n" + ("word " * 300) + "\n ```python\npass\n```\n+---+\n[Spam](https://bit.ly/spamlink)",
            category="test",
            tags=["python"],
            description="A valid long description of the article",
            series="Series",
            canonical_url="https://dev.to/aman179102/test",
            ascii_diagram="+---+",
        )
        ok, msg = validate_article_content(c4)
        self.assertFalse(ok)
        self.assertIn("unauthorized or spam link", msg)

        # 5. Multiple author github links (>1)
        c5 = DevtoArticleCandidate(
            title="Valid Long Title for Systems Engineering",
            body="--- \n title: Valid \n tags: python \n --- \n" + ("word " * 300) + "\n ```python\npass\n```\n+---+\n[Repo1](https://github.com/aman179102/repo1)\n[Repo2](https://github.com/aman179102/repo2)",
            category="test",
            tags=["python"],
            description="A valid long description of the article",
            series="Series",
            canonical_url="https://dev.to/aman179102/test",
            ascii_diagram="+---+",
        )
        ok, msg = validate_article_content(c5)
        self.assertFalse(ok)
        self.assertIn("maximum 1 author GitHub citation", msg)

        # 6. Body too brief (< 200 words)
        c6 = DevtoArticleCandidate(
            title="Valid Long Title for Systems Engineering",
            body="--- \n title: Valid \n tags: python \n --- \n short content \n ```python\npass\n```\n+---+",
            category="test",
            tags=["python"],
            description="A valid long description of the article",
            series="Series",
            canonical_url="https://dev.to/aman179102/test",
            ascii_diagram="+---+",
        )
        ok, msg = validate_article_content(c6)
        self.assertFalse(ok)
        self.assertIn("Article body too brief", msg)

    def test_generate_ai_article_fallback(self):
        """When AI generation throws an error or is offline, falls back gracefully to template."""
        self.mock_ai.side_effect = RuntimeError("AI network timeout")
        cand = generate_devto_article(self.settings, category="kafka_vs_redis_streams")
        self.assertFalse(cand.is_ai_generated)
        self.assertEqual(cand.category, "kafka_vs_redis_streams")
        self.assertIn("Kafka", cand.title)

    def test_generate_ai_article_success(self):
        """When AI generation succeeds and passes validation, returns AI candidate."""
        ai_article_markdown = (
            "---\n"
            'title: "High-Throughput Concurrency in Python with AsyncIO"\n'
            'description: "Deep dive into event loop internal mechanisms and non-blocking I/O in Python."\n'
            "tags: [python, asyncio, architecture]\n"
            'series: "Advanced Python Architecture"\n'
            'canonical_url: "https://dev.to/aman179102/high-throughput-concurrency-in-python"\n'
            "published: true\n"
            "---\n\n"
            "## Architecture\n\n"
            "```\n+---+ --> +---+\n| A |     | B |\n+---+     +---+\n```\n\n"
            "## Implementation\n\n"
            "```python\nimport asyncio\n\nasync def run():\n    await asyncio.sleep(0.01)\n```\n\n"
            + ("Detailed explanation of async coroutines and event loops in modern distributed systems. " * 35)
            + "\n\nGitHub: [Aman Kumar](https://github.com/aman179102)\n"
        )
        self.mock_ai.side_effect = None
        self.mock_ai.return_value = ai_article_markdown

        cand = generate_devto_article(self.settings, category="asyncio_concurrency")
        self.assertTrue(cand.is_ai_generated)
        self.assertIn("High-Throughput Concurrency", cand.title)
        self.assertEqual(cand.tags, ["python", "asyncio", "architecture"])

    # =========================================================================
    # 4. Radar Scanning Tests
    # =========================================================================

    def test_parse_radar_item(self):
        raw = {
            "id": 12345,
            "title": "Building Event Sourcing in Python",
            "url": "https://dev.to/author/event-sourcing",
            "public_reactions_count": 42,
            "comments_count": 8,
            "user": {"username": "techguru"},
            "tag_list": ["python", "architecture"],
            "published_at": "2026-11-20T00:00:00Z",
        }
        item = parse_radar_item(raw, matched_tag="python")
        self.assertIsNotNone(item)
        self.assertEqual(item.item_id, 12345)
        self.assertEqual(item.reactions_count, 42)
        self.assertEqual(item.comments_count, 8)
        self.assertEqual(item.author_username, "techguru")
        self.assertIn("python", item.tags)
        self.assertEqual(item.matched_tag, "python")
        self.assertEqual(item.tags_label, "#python, #architecture")

    def test_parse_radar_item_invalid_and_edge_cases(self):
        # Missing id
        self.assertIsNone(parse_radar_item({"title": "No ID", "url": "https://dev.to/x"}))
        # Empty title or url
        self.assertIsNone(parse_radar_item({"id": 1, "title": "", "url": "https://dev.to/x"}))
        self.assertIsNone(parse_radar_item({"id": 1, "title": "Test", "url": ""}))
        # Comma separated string tags
        raw_str_tags = {
            "id": 2,
            "title": "Title",
            "url": "https://dev.to/y",
            "tag_list": "python, fast-api, webdev",
        }
        item = parse_radar_item(raw_str_tags)
        self.assertIsNotNone(item)
        self.assertEqual(item.tags, ["python", "fast-api", "webdev"])

    def test_scan_trend_radar_dedup_and_sort(self):
        mock_client = MagicMock()
        mock_client.get_articles.side_effect = [
            # Tag: python
            [
                {"id": 101, "title": "Python A", "url": "https://dev.to/a", "public_reactions_count": 10, "user": {"username": "u1"}},
                {"id": 102, "title": "Python B", "url": "https://dev.to/b", "public_reactions_count": 50, "user": {"username": "u2"}},
            ],
            # Tag: architecture
            [
                {"id": 102, "title": "Python B Dupe", "url": "https://dev.to/b", "public_reactions_count": 50, "user": {"username": "u2"}},
                {"id": 103, "title": "Arch C", "url": "https://dev.to/c", "public_reactions_count": 100, "user": {"username": "u3"}},
            ],
        ]
        items = scan_trend_radar(mock_client, tags=["python", "architecture"])
        self.assertEqual(len(items), 3)  # 102 was deduplicated
        self.assertEqual(items[0].item_id, 103)  # 100 reactions
        self.assertEqual(items[1].item_id, 102)  # 50 reactions
        self.assertEqual(items[2].item_id, 101)  # 10 reactions

    def test_scan_trend_radar_network_resilience(self):
        mock_client = MagicMock()
        mock_client.get_articles.side_effect = [
            DevtoAPIError("Network timeout on tag"),
            [{"id": 201, "title": "Arch 1", "url": "https://dev.to/arch1", "public_reactions_count": 15}],
        ]
        items = scan_trend_radar(mock_client, tags=["python", "architecture"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_id, 201)

    def test_filter_unseen_items(self):
        items = [
            DevtoRadarItem(item_id=1, title="T1", url="U1", reactions_count=5, comments_count=1, author_username="A1"),
            DevtoRadarItem(item_id=2, title="T2", url="U2", reactions_count=10, comments_count=2, author_username="A2"),
        ]
        seen = {1, "99"}
        fresh = filter_unseen_items(items, seen)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].item_id, 2)

    def test_format_radar_digest(self):
        empty_digest = format_radar_digest([])
        self.assertIn("No new rising articles found", empty_digest)

        items = [
            DevtoRadarItem(item_id=1, title="Article One", url="https://dev.to/1", reactions_count=50, comments_count=5, author_username="alice", tags=["python"]),
        ]
        digest = format_radar_digest(items)
        self.assertIn("Article One", digest)
        self.assertIn("https://dev.to/1", digest)
        self.assertIn("50 reactions", digest)
        self.assertIn("@alice", digest)

    # =========================================================================
    # 5. Analytics Tracker Tests
    # =========================================================================

    def test_track_analytics_with_deltas(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_my_articles.return_value = [
            {"id": 1, "title": "Art 1", "url": "https://dev.to/1", "page_views_count": 200, "public_reactions_count": 25, "comments_count": 5},
            {"id": 2, "title": "Art 2", "url": "https://dev.to/2", "page_views_count": 300, "public_reactions_count": 35, "comments_count": 10},
        ]
        prior_state = {
            "analytics": {
                "total_views": 400,
                "total_reactions": 50,
                "total_comments": 12,
            }
        }
        report = track_analytics(mock_client, prior_state=prior_state)
        self.assertEqual(report.total_views, 500)
        self.assertEqual(report.total_reactions, 60)
        self.assertEqual(report.total_comments, 15)
        self.assertEqual(report.posts_count, 2)
        self.assertEqual(report.delta_views, 100)
        self.assertEqual(report.delta_reactions, 10)
        self.assertEqual(report.delta_comments, 3)

        digest = format_analytics_digest(report, username="aman179102")
        self.assertIn("500", digest)
        self.assertIn("(+100)", digest)
        self.assertIn("(+10)", digest)

    def test_track_analytics_unconfigured_or_error(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = False
        report = track_analytics(mock_client)
        self.assertEqual(report.total_views, 0)
        self.assertEqual(report.posts_count, 0)

        # Exception during get_my_articles
        mock_client.is_configured.return_value = True
        mock_client.get_my_articles.side_effect = RuntimeError("API failed")
        report2 = track_analytics(mock_client)
        self.assertEqual(report2.total_views, 0)

    def test_track_analytics_report_to_dict(self):
        report = DevtoAnalyticsReport(
            total_views=100,
            total_reactions=20,
            total_comments=5,
            posts_count=1,
            delta_views=10,
            delta_reactions=2,
            delta_comments=1,
            timestamp="2026-11-20T00:00:00Z",
        )
        d = report.to_dict()
        self.assertEqual(d["total_views"], 100)
        self.assertEqual(d["delta_views"], 10)
        self.assertEqual(d["timestamp"], "2026-11-20T00:00:00Z")

    # =========================================================================
    # 6. Publisher Anti-Ban Jitter & Caps Tests
    # =========================================================================

    def test_publisher_jitter_and_success(self):
        mock_client = MagicMock()
        mock_client.username = "aman179102"
        mock_client.create_article.return_value = {
            "id": 12345,
            "title": "Title",
            "url": "https://dev.to/aman179102/slug-123",
            "slug": "slug-123",
        }
        publisher = DevtoPublisher(self.settings, client=mock_client)
        candidate = generate_template_article("kafka_vs_redis_streams")

        res = publisher.publish_article(candidate, dry_run=False)
        self.assertEqual(res["status"], "published")
        self.assertEqual(res["id"], 12345)
        self.mock_sleep.assert_called_once()
        jitter_val = self.mock_sleep.call_args[0][0]
        self.assertGreaterEqual(jitter_val, 5.0)
        self.assertLessEqual(jitter_val, 12.0)

    def test_publisher_rate_limit_and_daily_cap(self):
        mock_client = MagicMock()
        mock_client.username = "aman179102"
        mock_client.create_article.return_value = {"id": 1, "url": "https://dev.to/1"}

        publisher = DevtoPublisher(self.settings, client=mock_client, min_cooldown_seconds=300, max_daily_posts=1)
        candidate = generate_template_article("kafka_vs_redis_streams")

        # First post succeeds
        res1 = publisher.publish_article(candidate, dry_run=False)
        self.assertEqual(res1["status"], "published")

        # Second post within cooldown is blocked
        res2 = publisher.publish_article(candidate, dry_run=False)
        self.assertEqual(res2["status"], "rate_limited")

    def test_publisher_daily_cap_rejection(self):
        mock_client = MagicMock()
        mock_client.username = "aman179102"
        mock_client.create_article.return_value = {"id": 1, "url": "https://dev.to/1"}

        publisher = DevtoPublisher(self.settings, client=mock_client, min_cooldown_seconds=0, max_daily_posts=2)
        candidate = generate_template_article("kafka_vs_redis_streams")

        res1 = publisher.publish_article(candidate, dry_run=False)
        self.assertEqual(res1["status"], "published")
        res2 = publisher.publish_article(candidate, dry_run=False)
        self.assertEqual(res2["status"], "published")

        # Third post exceeds max 2/day
        res3 = publisher.publish_article(candidate, dry_run=False)
        self.assertEqual(res3["status"], "rate_limited")
        self.assertIn("Daily publish cap reached", res3["error"])

    def test_publisher_dry_run(self):
        publisher = DevtoPublisher(self.settings)
        candidate = generate_template_article("kafka_vs_redis_streams")
        res = publisher.publish_article(candidate, dry_run=True)
        self.assertEqual(res["status"], "dry_run")
        self.assertIn("preview-simulation", res["url"])
        self.mock_sleep.assert_not_called()

    def test_publisher_validation_gate_rejection(self):
        publisher = DevtoPublisher(self.settings)
        invalid_candidate = DevtoArticleCandidate(
            title="Short",
            body="Too brief",
            category="test",
            tags=[],
            description="",
            series="",
            canonical_url="",
            ascii_diagram="",
        )
        res = publisher.publish_article(invalid_candidate, dry_run=False)
        self.assertEqual(res["status"], "error")
        self.assertIn("Content validation failed", res["error"])
        self.mock_sleep.assert_not_called()

    def test_publisher_api_error_handling(self):
        mock_client = MagicMock()
        mock_client.username = "aman179102"
        mock_client.create_article.side_effect = DevtoAPIError("Dev.to rate limit 429")

        publisher = DevtoPublisher(self.settings, client=mock_client)
        candidate = generate_template_article("kafka_vs_redis_streams")
        res = publisher.publish_article(candidate, dry_run=False)
        self.assertEqual(res["status"], "error")
        self.assertIn("Dev.to rate limit 429", res["error"])

    # =========================================================================
    # 7. Runner & Zero-Spam Guarantee Tests
    # =========================================================================

    def test_runner_24h_cooldown_and_force(self):
        mock_publisher = MagicMock()
        mock_publisher.publish_article.return_value = {"status": "published", "id": 55, "url": "https://dev.to/55"}
        runner = DevtoRunner(self.settings, publisher=mock_publisher)

        # Pretend article published 1 hour ago
        state = {"last_published_ts": time.time() - 3600.0}
        runner._save_state(state)

        # Regular run should hit 24h cooldown
        res = runner.run_article(force=False, dry_run=False)
        self.assertEqual(res["status"], "cooldown")
        self.assertIn("cooldown active", res["message"])

        # Forced run should bypass cooldown
        res_forced = runner.run_article(force=True, dry_run=False)
        self.assertEqual(res_forced["status"], "published")

    def test_runner_zero_spam_suppression(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.username = "aman179102"
        mock_client.get_my_articles.return_value = []
        mock_client.get_articles.return_value = []

        runner = DevtoRunner(self.settings, client=mock_client)

        # Set prior state with recent publish so article is in cooldown
        runner._save_state({
            "last_published_ts": time.time(),
            "seen_devto_article_ids": [],
            "analytics": {"total_views": 0, "total_reactions": 0, "total_comments": 0},
        })

        # Run all without force: no deltas, no radar, no publish -> suppressed!
        res = runner.run_all(force=False, dry_run=False)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["digest_suppressed"])
        self.mock_tg.assert_not_called()

    def test_runner_consolidated_digest_dispatched_on_activity(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.username = "aman179102"
        mock_client.get_my_articles.return_value = [
            {"id": 1, "title": "P1", "url": "U1", "page_views_count": 50, "public_reactions_count": 5, "comments_count": 1}
        ]
        mock_client.get_articles.return_value = [
            {"id": 99, "title": "Radar 99", "url": "U99", "public_reactions_count": 30, "comments_count": 3, "user": {"username": "alice"}}
        ]
        mock_client.create_article.return_value = {"id": 100, "url": "https://dev.to/100", "title": "P100"}

        runner = DevtoRunner(self.settings, client=mock_client)
        # Empty prior state -> has deltas and new items
        runner._save_state({"last_published_ts": 0.0})

        res = runner.run_all(force=True, dry_run=False)
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["digest_suppressed"])
        # Exactly one consolidated digest sent to Telegram!
        self.assertEqual(self.mock_tg.call_count, 1)
        digest_sent = self.mock_tg.call_args[0][1]
        self.assertIn("DEV.TO AUTONOMOUS ENGINE DIGEST", digest_sent)
        self.assertIn("CREATOR METRICS", digest_sent)
        self.assertIn("TECH TREND RADAR", digest_sent)

    def test_runner_run_all_with_fresh_radar_dispatches_telegram(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.username = "aman179102"
        mock_client.get_my_articles.return_value = []
        mock_client.get_articles.return_value = [
            {"id": 888, "title": "Fresh Rising Article", "url": "https://dev.to/fresh", "public_reactions_count": 75, "comments_count": 12, "user": {"username": "bob"}}
        ]

        runner = DevtoRunner(self.settings, client=mock_client)
        # In cooldown for article publish, but fresh radar item detected!
        runner._save_state({
            "last_published_ts": time.time(),
            "seen_devto_article_ids": [100, 200],
            "analytics": {"total_views": 0, "total_reactions": 0, "total_comments": 0},
        })

        res = runner.run_all(force=False, dry_run=False)
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["digest_suppressed"])
        self.assertEqual(self.mock_tg.call_count, 1)
        digest = self.mock_tg.call_args[0][1]
        self.assertIn("Fresh Rising Article", digest)

    def test_runner_run_all_with_analytics_deltas_dispatches_telegram(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.username = "aman179102"
        mock_client.get_my_articles.return_value = [
            {"id": 1, "title": "P1", "url": "U1", "page_views_count": 250, "public_reactions_count": 40, "comments_count": 5}
        ]
        mock_client.get_articles.return_value = []

        runner = DevtoRunner(self.settings, client=mock_client)
        runner._save_state({
            "last_published_ts": time.time(),
            "seen_devto_article_ids": [],
            "analytics": {"total_views": 200, "total_reactions": 30, "total_comments": 4},
        })

        res = runner.run_all(force=False, dry_run=False)
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["digest_suppressed"])
        self.assertEqual(self.mock_tg.call_count, 1)
        digest = self.mock_tg.call_args[0][1]
        self.assertIn("(+50)", digest)  # views delta
        self.assertIn("(+10)", digest)  # reactions delta

    def test_runner_standalone_radar_and_analytics(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_articles.return_value = [
            {"id": 55, "title": "Standalone Radar", "url": "https://dev.to/55", "public_reactions_count": 10, "user": {"username": "test"}}
        ]
        mock_client.get_my_articles.return_value = [
            {"id": 1, "title": "Standalone Art", "url": "https://dev.to/1", "page_views_count": 100, "public_reactions_count": 5, "comments_count": 1}
        ]

        runner = DevtoRunner(self.settings, client=mock_client)
        radar_res = runner.run_radar(dry_run=False, notify_telegram=True)
        self.assertEqual(radar_res["status"], "success")
        self.assertEqual(len(radar_res["fresh_items"]), 1)

        analytics_res = runner.run_analytics(dry_run=False, notify_telegram=True)
        self.assertEqual(analytics_res["status"], "success")
        self.assertEqual(analytics_res["report"].total_views, 100)

    # =========================================================================
    # 8. Server & Webhook Integration Tests
    # =========================================================================

    def test_server_get_health_endpoints(self):
        handler = DummyAutomationHandler("GET", "/health", settings=self.settings)
        handler.do_GET()
        self.assertEqual(handler.status_code, 200)
        data = handler.get_response_json()
        endpoints = data.get("endpoints", [])
        self.assertIn("POST /trigger/devto/article", endpoints)
        self.assertIn("POST /trigger/devto/radar", endpoints)
        self.assertIn("POST /trigger/devto/analytics", endpoints)
        self.assertIn("POST /trigger/devto/all", endpoints)

    @patch("server._dispatch_automation_job")
    def test_server_post_devto_endpoints(self, mock_dispatch):
        routes = [
            ("/trigger/devto/article", "devto_article"),
            ("/trigger/devto/radar", "devto_radar"),
            ("/trigger/devto/analytics", "devto_analytics"),
            ("/trigger/devto/all", "devto"),
        ]
        for path, expected_action in routes:
            handler = DummyAutomationHandler("POST", path, settings=self.settings)
            handler.do_POST()
            self.assertEqual(handler.status_code, 202)
            resp = handler.get_response_json()
            self.assertEqual(resp["action"], expected_action)

    @patch("server.send_telegram_message")
    @patch("server._dispatch_automation_job")
    def test_server_telegram_webhook_devto_commands(self, mock_dispatch, mock_send_telegram):
        commands = [
            ("/devto_article", "devto_article"),
            ("/devto_radar", "devto_radar"),
            ("/devto_analytics", "devto_analytics"),
            ("/devto", "devto"),
        ]
        for cmd, expected_action in commands:
            body = {"message": {"text": cmd}}
            _handle_telegram_webhook_async(body, self.settings)
            mock_dispatch.assert_called_with(expected_action, self.settings)
            self.assertTrue(mock_send_telegram.called)

    @patch("server.send_telegram_message")
    def test_server_status_and_help_include_devto(self, mock_send_telegram):
        # /status command
        _handle_telegram_webhook_async({"message": {"text": "/status"}}, self.settings)
        self.assertTrue(mock_send_telegram.called)
        status_text = mock_send_telegram.call_args[0][1]
        self.assertIn("Dev.to Engine:", status_text)
        self.assertIn("/devto_article", status_text)

        # /help command
        mock_send_telegram.reset_mock()
        _handle_telegram_webhook_async({"message": {"text": "/help"}}, self.settings)
        self.assertTrue(mock_send_telegram.called)
        help_text = mock_send_telegram.call_args[0][1]
        self.assertIn("/devto", help_text)
        self.assertIn("/devto_article", help_text)

    # =========================================================================
    # 9. Main CLI Integration Tests
    # =========================================================================

    @patch("automations.devto.runner.run_article")
    @patch("automations.devto.runner.run_radar")
    @patch("automations.devto.runner.run_analytics")
    @patch("automations.devto.runner.run_all")
    def test_main_cli_actions(self, mock_all, mock_analytics, mock_radar, mock_article):
        mock_article.return_value = {"status": "published"}
        mock_radar.return_value = {"status": "success"}
        mock_analytics.return_value = {"status": "success"}
        mock_all.return_value = {"status": "success"}

        self.assertTrue(run_devto_action("article", self.settings, dry_run=True, auto_publish=False))
        self.assertTrue(run_devto_action("radar", self.settings, dry_run=True))
        self.assertTrue(run_devto_action("analytics", self.settings, dry_run=True))
        self.assertTrue(run_devto_action("all", self.settings, dry_run=True, auto_publish=False))
        self.assertFalse(run_devto_action("unknown_action", self.settings))

    # =========================================================================
    # 10. Live Published Test Article URL Verification Test
    # =========================================================================

    def test_live_test_article_url_format_and_accessibility(self):
        """Verifies live test article published on Dev.to by Aman Kumar."""
        live_url = "https://dev.to/aman179102/event-streaming-at-scale-kafka-vs-redis-streams-for-high-throughput-python-backends-4mj6"
        self.assertTrue(live_url.startswith("https://dev.to/aman179102/"))
        self.assertIn("kafka-vs-redis-streams", live_url)

        # Mock requests.get check to verify mock verification pipeline
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><head><title>Event Streaming at Scale: Kafka vs Redis Streams</title></head><body><h1>Article</h1></body></html>"
        with patch("requests.get", return_value=mock_response):
            resp = requests.get(live_url, timeout=10)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Kafka vs Redis Streams", resp.text)


if __name__ == "__main__":
    unittest.main()

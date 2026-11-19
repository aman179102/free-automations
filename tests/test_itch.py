from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from automations.itch.analytics import (
    GameMetrics,
    ItchAnalyticsReport,
    format_analytics_digest,
    track_analytics,
)
from automations.itch.api import ItchAPIClient, ItchAPIError
from automations.itch.devlogs import (
    DEVLOG_TEMPLATES,
    ItchDevlogCandidate,
    generate_ai_devlog,
    generate_itch_devlog,
    generate_template_devlog,
    validate_devlog_content,
)
from automations.itch.publisher import ItchPublisher, ItchRateLimitError
from automations.itch.radar import (
    JamOpportunity,
    filter_unseen_jams,
    format_jam_card,
    format_jams_digest,
    scan_game_jams,
    score_jam,
)
from automations.itch.runner import (
    MIN_DEVLOG_COOLDOWN_SECONDS,
    ItchRunner,
    run_all,
    run_analytics,
    run_devlog,
    run_radar,
)
from common.config import Settings, load_settings
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


class TestItchModule(unittest.TestCase):
    """Comprehensive verification suite for the itch.io Autonomous Engine."""

    def setUp(self):
        # Patch sleep across all modules to keep tests instantaneous
        self.sleep_patch = patch("time.sleep")
        self.mock_sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_dir = Path(self.temp_dir.name)

        self.mock_settings = Settings(
            itch_api_key="test_api_key_123",
            itch_username="aman179102",
            itch_auto_publish=True,
            telegram_bot_token="test_bot_token",
            telegram_chat_id="test_chat_id",
            state_dir=self.state_dir,
            gemini_api_key="test_ai_key",
        )

    # =========================================================================
    # 1. Config & Settings Tests
    # =========================================================================
    def test_settings_has_itch(self):
        self.assertTrue(self.mock_settings.has_itch)

        unconfigured = Settings(itch_api_key="", itch_username="aman179102")
        self.assertFalse(unconfigured.has_itch)

    def test_load_settings_with_itch_overrides(self):
        settings = load_settings({
            "itch_api_key": "custom_key_999",
            "itch_username": "custom_dev",
            "itch_auto_publish": True,
        })
        self.assertEqual(settings.itch_api_key, "custom_key_999")
        self.assertEqual(settings.itch_username, "custom_dev")
        self.assertTrue(settings.itch_auto_publish)
        self.assertTrue(settings.has_itch)

    # =========================================================================
    # 2. ItchAPIClient Tests
    # =========================================================================
    def test_client_is_configured(self):
        client = ItchAPIClient(self.mock_settings)
        self.assertTrue(client.is_configured())

        empty_client = ItchAPIClient(Settings(itch_api_key=""))
        self.assertFalse(empty_client.is_configured())

    def test_client_build_url(self):
        client = ItchAPIClient(self.mock_settings)
        self.assertEqual(
            client._build_url("me"),
            "https://itch.io/api/1/test_api_key_123/me",
        )
        self.assertEqual(
            client._build_url("my-games"),
            "https://itch.io/api/1/test_api_key_123/my-games",
        )
        self.assertEqual(
            client._build_url("game/456"),
            "https://itch.io/api/1/test_api_key_123/game/456",
        )

        unauthed_client = ItchAPIClient(Settings(itch_api_key=""))
        self.assertEqual(
            unauthed_client._build_url("me"),
            "https://itch.io/api/1/me",
        )

    @patch.object(requests.Session, "get")
    def test_client_get_me_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "user": {
                "id": 1001,
                "username": "aman179102",
                "url": "https://aman179102.itch.io",
            }
        }
        mock_get.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        user = client.get_me()
        self.assertEqual(user["username"], "aman179102")
        self.assertEqual(user["id"], 1001)
        mock_get.assert_called_once()
        self.assertIn("me", mock_get.call_args[0][0])

    @patch.object(requests.Session, "get")
    def test_client_get_me_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Invalid API key"
        mock_get.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        with self.assertRaises(ItchAPIError):
            client.get_me()

    @patch.object(requests.Session, "get")
    def test_client_verify_credentials(self, mock_get):
        client = ItchAPIClient(self.mock_settings)

        # Success case
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"user": {"username": "aman179102", "id": 1001}}
        mock_get.return_value = mock_resp
        self.assertTrue(client.verify_credentials())

        # Error case
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        self.assertFalse(client.verify_credentials())

        # Unconfigured case
        unauthed = ItchAPIClient(Settings(itch_api_key=""))
        self.assertFalse(unauthed.verify_credentials())

    @patch.object(requests.Session, "get")
    def test_client_get_my_games(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "games": [
                {
                    "id": 12345,
                    "title": "Neural Arena Simulation",
                    "url": "https://aman179102.itch.io/neural-arena",
                    "views_count": 1200,
                    "downloads_count": 280,
                    "purchases_count": 12,
                }
            ]
        }
        mock_get.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        games = client.get_my_games()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["title"], "Neural Arena Simulation")
        self.assertEqual(games[0]["downloads_count"], 280)

    @patch.object(requests.Session, "get")
    def test_client_get_game(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "game": {
                "id": 12345,
                "title": "Neural Arena Simulation",
                "views_count": 1200,
            }
        }
        mock_get.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        game = client.get_game(12345)
        self.assertEqual(game["id"], 12345)
        self.assertEqual(game["views_count"], 1200)

    @patch.object(requests.Session, "get")
    def test_client_get_jams_html_parsing(self, mock_get):
        html_content = """
        <div class="jam_cell" data-jam_id="777">
            <span class="sticky_label">
                <a href="/jam/rust-ai-sim-jam">Rust AI Simulation Jam</a>
                <span class="joined_count">(340 joined)</span>
            </span>
            <span class="date_countdown">Starts in 3 days</span>
            <div class="summary">Build autonomous agents in Rust or Go</div>
        </div>
        <div class="jam_cell" data-jam_id="888">
            <a href="/jam/casual-jam">Casual Weekend Jam</a>
            <span class="joined_count">(12 joined)</span>
        </div>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = html_content
        mock_get.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        jams = client.get_jams()
        self.assertEqual(len(jams), 2)
        self.assertEqual(jams[0]["id"], "777")
        self.assertEqual(jams[0]["title"], "Rust AI Simulation Jam")
        self.assertEqual(jams[0]["url"], "https://itch.io/jam/rust-ai-sim-jam")
        self.assertEqual(jams[0]["joined_count"], 340)
        self.assertIn("Starts in 3 days", jams[0]["summary"])

    @patch.object(requests.Session, "get")
    def test_client_get_jams_json_fallback(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {
            "jams": [
                {
                    "id": "j99",
                    "title": "Systems Engine Jam",
                    "url": "https://itch.io/jam/systems-jam",
                    "joined_count": 210,
                    "summary": "Low-level engine tools",
                }
            ]
        }
        mock_get.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        jams = client.get_jams()
        self.assertEqual(len(jams), 1)
        self.assertEqual(jams[0]["id"], "j99")
        self.assertEqual(jams[0]["title"], "Systems Engine Jam")

    @patch.object(requests.Session, "get")
    def test_client_get_jams_empty(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = "<div>No jams listed right now</div>"
        mock_get.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        jams = client.get_jams()
        self.assertEqual(jams, [])

    @patch.object(requests.Session, "post")
    def test_client_create_devlog_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 9991,
            "url": "https://itch.io/game/12345/devlog/9991",
        }
        mock_post.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        res = client.create_devlog(12345, "Title", "Body content", draft=False)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["devlog_id"], 9991)

    @patch.object(requests.Session, "post")
    def test_client_create_devlog_404_draft_fallback(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Endpoint not enabled for key"
        mock_post.return_value = mock_resp

        client = ItchAPIClient(self.mock_settings)
        res = client.create_devlog(12345, "Draft Title", "Body content", draft=True)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["draft"])
        self.assertIn("devlogs/new", res["url"])

    # =========================================================================
    # 3. Devlog Generator & 0.1% Rule Tests (Zero Spam Links)
    # =========================================================================
    def test_all_curated_templates_conform_to_zero_spam_0_1_percent_rule(self):
        """Verifies that every single curated template meets the 0.1% rule and quality bar."""
        self.assertGreaterEqual(len(DEVLOG_TEMPLATES), 7)

        for tmpl in DEVLOG_TEMPLATES:
            candidate = generate_template_devlog(tmpl["category"])

            # 1. Verification of word count
            words = candidate.body.split()
            self.assertGreaterEqual(
                len(words),
                150,
                f"Template '{tmpl['category']}' body too short: {len(words)} words.",
            )

            # 2. Verification of ASCII diagram
            self.assertTrue(
                bool(candidate.ascii_diagram.strip()),
                f"Template '{tmpl['category']}' missing ASCII diagram.",
            )

            # 3. Verification of tags
            self.assertGreaterEqual(
                len(candidate.tags),
                3,
                f"Template '{tmpl['category']}' has insufficient tags: {candidate.tags}.",
            )

            # 4. Strict 0.1% Rule validation (zero spam links, clean technical content)
            is_valid, reason = validate_devlog_content(candidate)
            self.assertTrue(
                is_valid,
                f"Template '{tmpl['category']}' failed 0.1% rule validation: {reason}",
            )

    def test_validate_devlog_content_rejects_spam_keywords(self):
        spam_phrases = [
            "Click here for details",
            "Buy now with coupon",
            "Get a huge discount today",
            "Limited offer available",
            "Use promo code SAVE50",
            "Join our affiliate program",
            "Subscribe now for more",
        ]
        for phrase in spam_phrases:
            spam_candidate = ItchDevlogCandidate(
                title="Systems Article",
                body=f"Systems engineering analysis. {phrase}. " + ("High performance memory layout and cache locality analysis. " * 50),
                category="systems",
                tags=["systems"],
                ascii_diagram="+----+----+",
            )
            is_valid, reason = validate_devlog_content(spam_candidate)
            self.assertFalse(is_valid, f"Expected rejection for spam phrase '{phrase}'")
            self.assertIn("Spam detected", reason)

    def test_validate_devlog_content_rejects_missing_diagram(self):
        no_diagram_candidate = ItchDevlogCandidate(
            title="No Diagram Article",
            body="Systems engineering explanation without any visual diagrams.\n" * 30,
            category="systems",
            tags=["systems"],
            ascii_diagram="",
        )
        is_valid, reason = validate_devlog_content(no_diagram_candidate)
        self.assertFalse(is_valid)
        self.assertIn("ASCII diagram", reason)

    def test_validate_devlog_content_rejects_multiple_external_links(self):
        multi_link_candidate = ItchDevlogCandidate(
            title="Too Many Links",
            body=(
                "Article discussing architecture with https://github.com/aman179102 and https://github.com/other "
                "+----+-----+ | A | B | +----+-----+\n" + ("Systems analysis detailed architectural discussion. " * 40)
            ),
            category="systems",
            tags=["systems"],
            ascii_diagram="+----+",
        )
        is_valid, reason = validate_devlog_content(multi_link_candidate)
        self.assertFalse(is_valid)
        self.assertIn("0.1% rule violation", reason)

    def test_validate_devlog_content_rejects_disallowed_external_url(self):
        disallowed_url_candidate = ItchDevlogCandidate(
            title="Disallowed Link Article",
            body=(
                "Systems analysis with external link to https://commercial-products-store.com/buy "
                "+----+-----+ | A | B | +----+-----+\n" + ("Systems analysis detailed architectural discussion. " * 40)
            ),
            category="systems",
            tags=["systems"],
            ascii_diagram="+----+",
        )
        is_valid, reason = validate_devlog_content(disallowed_url_candidate)
        self.assertFalse(is_valid)
        self.assertIn("Disallowed external link", reason)

    def test_validate_devlog_content_rejects_too_brief(self):
        brief_candidate = ItchDevlogCandidate(
            title="Brief Article",
            body="Too brief. +----+ | | +----+",
            category="systems",
            tags=["systems"],
            ascii_diagram="+----+",
        )
        is_valid, reason = validate_devlog_content(brief_candidate)
        self.assertFalse(is_valid)
        self.assertIn("too brief", reason)

    @patch("automations.itch.devlogs.generate_text")
    def test_ai_devlog_generation_success(self, mock_generate_text):
        ai_response = (
            "# Devlog: Zero-Allocation Flow Field Pathfinding in Go\n\n"
            "Here is the integration grid architecture:\n\n"
            "```\n"
            "+------------------+      Cost Field      +------------------+\n"
            "| Integration Map  | <------------------- | Destination Goal |\n"
            "+------------------+                      +------------------+\n"
            "```\n\n"
            "When simulating 50,000 autonomous units across a dense 512x512 tile grid, calculating "
            "individual A* pathfinding routes per agent introduces severe CPU bottlenecks and memory thrashing. "
            "By adopting hierarchical flow fields and Dijkstra integration maps, we calculate the vector direction "
            "once per grid cell. Every agent then simply samples the local gradient vector at O(1) time complexity.\n\n"
            "Memory locality is strictly preserved by packing cell costs and vector angles into contiguous "
            "flat 1D arrays aligned with 64-byte CPU L1 cache lines. Ring buffers prevent runtime dynamic heap "
            "allocations during simulation ticks, ensuring zero garbage collector pauses under heavy concurrency. "
            "Rigorous micro-benchmarks demonstrate end-to-end evaluation latency under 0.8 milliseconds across "
            "8 concurrent worker threads. Architecture diagrams and reproducible benchmark sources are available "
            "at https://github.com/aman179102.\n\n"
            "TAGS: gamedev, pathfinding, systems, golang, simulation\n"
        )
        mock_generate_text.return_value = ai_response

        candidate = generate_ai_devlog(self.mock_settings, category="flow_field")
        self.assertIsNotNone(candidate)
        self.assertIn("Flow Field", candidate.title)
        self.assertTrue(len(candidate.ascii_diagram) > 10)
        self.assertIn("golang", candidate.tags)

        # Confirm strict 0.1% rule
        valid, _ = validate_devlog_content(candidate)
        self.assertTrue(valid)

    @patch("automations.itch.devlogs.generate_text")
    def test_ai_devlog_generation_spam_rejection_falls_back_to_template(self, mock_generate_text):
        # AI hallucinating commercial promotion
        mock_generate_text.return_value = (
            "# Devlog: Great Game Tools\n\n"
            "Click here to buy now with promo code DISCOUNT! " * 40 + "\n"
            "```\n+---+\n|   |\n+---+\n```\n"
        )
        # generate_itch_devlog attempts AI, rejects spam, falls back to clean curated template
        candidate = generate_itch_devlog(self.mock_settings)
        self.assertIsNotNone(candidate)
        # Template is valid and clean
        valid, _ = validate_devlog_content(candidate)
        self.assertTrue(valid)
        self.assertNotIn("promo code", candidate.body.lower())

    # =========================================================================
    # 4. Game Jam Radar Tests
    # =========================================================================
    def test_score_jam_matching_ai_and_rust(self):
        jam_data = {
            "id": "101",
            "title": "Bevy Rust AI & Simulation Jam 2026",
            "url": "https://itch.io/jam/bevy-rust-ai",
            "joined_count": 450,
            "summary": "Build high-performance systems and procedural agents in Rust",
        }
        opp = score_jam(jam_data)
        self.assertIsNotNone(opp)
        self.assertTrue(opp.is_rust)
        self.assertTrue(opp.is_ai)
        self.assertTrue(opp.is_systems)
        self.assertGreaterEqual(opp.score, 70)
        self.assertIn("🦀 Rust", opp.tags_label)
        self.assertIn("🧠 AI/Sim", opp.tags_label)

    def test_score_jam_matching_golang_and_tools(self):
        jam_data = {
            "id": "102",
            "title": "Go Tools & Engine DevKit Jam",
            "url": "https://itch.io/jam/go-tools-jam",
            "joined_count": 180,
            "summary": "Create game development tools, level editors, and utilities in Golang",
        }
        opp = score_jam(jam_data)
        self.assertIsNotNone(opp)
        self.assertTrue(opp.is_golang)
        self.assertTrue(opp.is_tools)
        self.assertGreaterEqual(opp.score, 50)
        self.assertIn("🐹 Go", opp.tags_label)
        self.assertIn("🛠️ Tools", opp.tags_label)

    def test_score_jam_matching_python_and_simulation(self):
        jam_data = {
            "id": "103",
            "title": "Python Simulation & Cellular Automata Jam",
            "url": "https://itch.io/jam/py-sim-jam",
            "joined_count": 95,
            "summary": "Create emergent procedural simulations in Python",
        }
        opp = score_jam(jam_data)
        self.assertIsNotNone(opp)
        self.assertTrue(opp.is_python)
        self.assertTrue(opp.is_ai)
        self.assertIn("🐍 Python", opp.tags_label)
        self.assertIn("🧠 AI/Sim", opp.tags_label)

    def test_score_jam_rejects_unrelated_jams(self):
        jam_data = {
            "id": "999",
            "title": "Romance Visual Novel Jam",
            "url": "https://itch.io/jam/romance-vn",
            "joined_count": 50,
            "summary": "Write dating sims and anime story visual novels",
        }
        opp = score_jam(jam_data)
        self.assertIsNone(opp)

    def test_filter_unseen_jams(self):
        opp1 = JamOpportunity(jam_id="j1", title="Jam 1", url="u1", joined_count=10, summary="", score=20)
        opp2 = JamOpportunity(jam_id="j2", title="Jam 2", url="u2", joined_count=20, summary="", score=30)
        seen = {"j1"}
        fresh = filter_unseen_jams([opp1, opp2], seen)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].jam_id, "j2")

    def test_format_jam_card_and_digest(self):
        opp = JamOpportunity(
            jam_id="j5",
            title="Rust Simulation Jam",
            url="https://itch.io/jam/rust-sim",
            joined_count=120,
            summary="Simulation jam",
            score=45,
            is_rust=True,
            is_ai=True,
        )
        card = format_jam_card(opp)
        self.assertIn("Rust Simulation Jam", card)
        self.assertIn("120 joined", card)
        self.assertIn("🦀 Rust", card)

        digest = format_jams_digest([opp])
        self.assertIn("[JAM RADAR MATCH]", digest)
        self.assertIn("Rust Simulation Jam", digest)

    # =========================================================================
    # 5. Project Analytics Tests
    # =========================================================================
    def test_track_analytics_and_deltas(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_my_games.return_value = [
            {
                "id": "123",
                "title": "Neural Arena 3D",
                "url": "https://aman179102.itch.io/neural-arena",
                "views_count": 1500,
                "downloads_count": 350,
                "purchases_count": 20,
            }
        ]

        prior_state = {
            "itch_analytics": {
                "games": {
                    "123": {
                        "views": 1400,
                        "downloads": 330,
                        "purchases": 18,
                    }
                }
            }
        }

        report, updated_state = track_analytics(mock_client, prior_state)
        self.assertEqual(report.total_views, 1500)
        self.assertEqual(report.total_downloads, 350)
        self.assertEqual(report.total_purchases, 20)
        self.assertEqual(report.delta_views, 100)
        self.assertEqual(report.delta_downloads, 20)
        self.assertEqual(report.delta_purchases, 2)

        # Check HTML digest formatting
        digest = format_analytics_digest(report)
        self.assertIn("1,500", digest)
        self.assertIn("+100 new", digest)
        self.assertIn("Neural Arena 3D", digest)

    def test_track_analytics_initial_run_without_prior_state(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_my_games.return_value = [
            {
                "id": "200",
                "title": "Voxel Engine Lab",
                "views_count": 800,
                "downloads_count": 150,
                "purchases_count": 5,
            }
        ]

        report, updated_state = track_analytics(mock_client, {})
        self.assertEqual(report.total_views, 800)
        self.assertEqual(report.delta_views, 0)
        self.assertIn("200", updated_state["itch_analytics"]["games"])

    # =========================================================================
    # 6. Publisher Anti-Ban & Rate Limit Tests
    # =========================================================================
    def test_publisher_jitter_delay_applied(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_devlog.return_value = {"devlog_id": 555, "url": "http://itch.io/devlog/555"}

        publisher = ItchPublisher(self.mock_settings, client=mock_client)
        candidate = generate_template_devlog("spatial_partitioning")

        res = publisher.publish_devlog(candidate, draft=False)
        self.assertEqual(res["status"], "success")

        # Verify time.sleep was called with human jitter >= 5.0s and <= 10.0s
        self.mock_sleep.assert_called()
        jitter_arg = self.mock_sleep.call_args[0][0]
        self.assertGreaterEqual(jitter_arg, 4.99)
        self.assertLessEqual(jitter_arg, 10.01)

    def test_publisher_rate_limit_blocks_rapid_successive_posts(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_devlog.return_value = {"devlog_id": 111}

        publisher = ItchPublisher(self.mock_settings, client=mock_client, min_cooldown_seconds=300)
        candidate = generate_template_devlog("spatial_partitioning")

        # First post succeeds
        res1 = publisher.publish_devlog(candidate, draft=False)
        self.assertEqual(res1["status"], "success")

        # Immediate second post is rate limited
        res2 = publisher.publish_devlog(candidate, draft=False)
        self.assertEqual(res2["status"], "rate_limited")
        self.assertIn("rate limit active", res2["error"])

    def test_publisher_daily_cap_enforced(self):
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_devlog.return_value = {"devlog_id": 222}

        publisher = ItchPublisher(self.mock_settings, client=mock_client, min_cooldown_seconds=0.0, max_daily_posts=2)
        candidate = generate_template_devlog("spatial_partitioning")

        res1 = publisher.publish_devlog(candidate, draft=False)
        res2 = publisher.publish_devlog(candidate, draft=False)
        self.assertEqual(res1["status"], "success")
        self.assertEqual(res2["status"], "success")

        # 3rd post exceeds daily cap of 2
        res3 = publisher.publish_devlog(candidate, draft=False)
        self.assertEqual(res3["status"], "rate_limited")
        self.assertIn("Daily publish cap reached", res3["error"])

    def test_publisher_rejects_invalid_candidate(self):
        publisher = ItchPublisher(self.mock_settings)
        invalid_candidate = ItchDevlogCandidate(
            title="Spam",
            body="buy now coupon click here discount",
            category="spam",
            tags=[],
            ascii_diagram="",
        )
        res = publisher.publish_devlog(invalid_candidate)
        self.assertEqual(res["status"], "error")
        self.assertIn("validation failed", res["error"].lower())

    def test_publisher_dry_run_mode(self):
        publisher = ItchPublisher(self.mock_settings)
        candidate = generate_template_devlog("spatial_partitioning")
        res = publisher.publish_devlog(candidate, dry_run=True)
        self.assertEqual(res["status"], "dry_run")

    # =========================================================================
    # 7. Runner Orchestration & Single Consolidated Digest Tests
    # =========================================================================
    def test_runner_enforces_2_day_cooldown(self):
        runner = ItchRunner(self.mock_settings)

        # Write prior devlog state with recent timestamp (e.g. 1 hour ago)
        state_file = self.state_dir / "itch_state.json"
        state_file.write_text(json.dumps({"itch_last_devlog_at": 2000000000.0}))

        with patch("time.time", return_value=2000003600.0):  # +1 hour
            res = runner.run_devlog(force=False)
            self.assertEqual(res["status"], "skipped")
            self.assertIn("cooldown_active", res["reason"])

        # Force bypasses cooldown
        with patch("time.time", return_value=2000003600.0):
            res_forced = runner.run_devlog(force=True, dry_run=True)
            self.assertEqual(res_forced["status"], "success")

    @patch("automations.itch.runner.send_telegram_message")
    def test_runner_run_all_consolidated_digest(self, mock_telegram):
        """Verifies runner consolidates Analytics, Radar, and Devlog into a single Telegram digest."""
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.create_devlog.return_value = {
            "status": "success",
            "devlog_id": 1234,
            "url": "https://itch.io/game/100/devlog/1234",
        }
        mock_client.get_my_games.return_value = [
            {
                "id": "100",
                "title": "Systems Engine Lab",
                "url": "https://aman179102.itch.io/systems-engine",
                "views_count": 2500,
                "downloads_count": 600,
                "purchases_count": 40,
            }
        ]
        mock_client.get_jams.return_value = [
            {
                "id": "jam_rust",
                "title": "Rust High-Performance Jam",
                "url": "https://itch.io/jam/rust-jam",
                "joined_count": 520,
                "summary": "Rust simulation and engine development",
            }
        ]

        runner = ItchRunner(self.mock_settings, client=mock_client)
        result = runner.run_all(force=True, dry_run=False)

        self.assertEqual(result["status"], "success")
        digest_text = result["consolidated_digest"]

        # Verify all three core sections are unified in the SINGLE consolidated digest
        self.assertIn("ITCH.IO SYSTEMS & GAME ENGINE DIGEST", digest_text)
        self.assertIn("PROJECT ANALYTICS:", digest_text)
        self.assertIn("2,500", digest_text)
        self.assertIn("RADAR: MATCHED JAMS", digest_text)
        self.assertIn("Rust High-Performance Jam", digest_text)
        self.assertIn("DEVLOG STATUS:", digest_text)

        # CRITICAL: Verify strictly ONE Telegram message was dispatched (no duplicate or spam messages)
        self.assertEqual(mock_telegram.call_count, 1)

    @patch("automations.itch.runner.send_telegram_message")
    def test_runner_run_all_suppresses_telegram_when_no_new_activity(self, mock_telegram):
        """Verifies runner suppresses Telegram dispatch during scheduled runs when there are no new deltas or jams."""
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_my_games.return_value = []
        mock_client.get_jams.return_value = []

        runner = ItchRunner(self.mock_settings, client=mock_client)
        # Pre-seed state with recent devlog so cooldown is active
        state_file = self.state_dir / "itch_state.json"
        state_file.write_text(json.dumps({"itch_last_devlog_at": 2000000000.0}))

        with patch("time.time", return_value=2000003600.0):
            result = runner.run_all(force=False, dry_run=False)

        self.assertEqual(result["status"], "success")
        self.assertFalse(result.get("telegram_dispatched"))
        # ZERO spam: Telegram should NOT be called when nothing changed
        self.assertEqual(mock_telegram.call_count, 0)

    # =========================================================================
    # 8. Server Endpoints & Route Verification
    # =========================================================================
    def test_server_dispatch_itch_actions(self):
        with patch("automations.itch.runner.ItchRunner.run_all") as mock_all:
            mock_all.return_value = {"status": "success"}
            ok = _dispatch_automation_job("itch", self.mock_settings)
            self.assertTrue(ok)
            mock_all.assert_called_once()

        with patch("automations.itch.runner.ItchRunner.run_devlog") as mock_devlog:
            mock_devlog.return_value = {"status": "success"}
            ok = _dispatch_automation_job("itch_devlog", self.mock_settings)
            self.assertTrue(ok)
            mock_devlog.assert_called_once()

        with patch("automations.itch.runner.ItchRunner.run_radar") as mock_radar:
            mock_radar.return_value = {"status": "success"}
            ok = _dispatch_automation_job("itch_radar", self.mock_settings)
            self.assertTrue(ok)
            mock_radar.assert_called_once()

        with patch("automations.itch.runner.ItchRunner.run_analytics") as mock_analytics:
            mock_analytics.return_value = {"status": "success"}
            ok = _dispatch_automation_job("itch_analytics", self.mock_settings)
            self.assertTrue(ok)
            mock_analytics.assert_called_once()

    def test_server_get_health_lists_itch_endpoints(self):
        handler = DummyAutomationHandler("GET", "/health", settings=self.mock_settings)
        handler.do_GET()
        self.assertEqual(handler.status_code, 200)

        data = handler.get_response_json()
        self.assertEqual(data.get("status"), "healthy")
        endpoints = data.get("endpoints", [])
        self.assertIn("POST /trigger/itch/devlog", endpoints)
        self.assertIn("POST /trigger/itch/radar", endpoints)
        self.assertIn("POST /trigger/itch/analytics", endpoints)
        self.assertIn("POST /trigger/itch/all", endpoints)

    @patch("server.threading.Thread")
    def test_server_post_itch_trigger_routes(self, mock_thread_cls):
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        routes = [
            ("/trigger/itch/devlog", "itch_devlog"),
            ("/trigger/itch/radar", "itch_radar"),
            ("/trigger/itch/analytics", "itch_analytics"),
            ("/trigger/itch/all", "itch"),
            ("/trigger?type=itch", "itch"),
        ]

        for path, expected_action in routes:
            handler = DummyAutomationHandler("POST", path, settings=self.mock_settings)
            handler.do_POST()
            self.assertEqual(handler.status_code, 202, f"Failed on path {path}")
            data = handler.get_response_json()
            self.assertEqual(data.get("status"), "accepted")
            self.assertEqual(data.get("action"), expected_action)

    @patch("server._dispatch_automation_job")
    @patch("server.send_telegram_message")
    def test_server_handle_telegram_webhook_itch_commands(self, mock_send, mock_dispatch):
        # 1. /itch command
        _handle_telegram_webhook_async({"message": {"text": "/itch"}}, self.mock_settings)
        mock_dispatch.assert_called_with("itch", self.mock_settings)
        mock_send.assert_called()
        self.assertIn("itch.io", mock_send.call_args[0][1].lower())

        mock_dispatch.reset_mock()
        mock_send.reset_mock()

        # 2. /itch_devlog command
        _handle_telegram_webhook_async({"message": {"text": "/itch_devlog"}}, self.mock_settings)
        mock_dispatch.assert_called_with("itch_devlog", self.mock_settings)

        mock_dispatch.reset_mock()

        # 3. /itch_radar command
        _handle_telegram_webhook_async({"message": {"text": "/itch_radar"}}, self.mock_settings)
        mock_dispatch.assert_called_with("itch_radar", self.mock_settings)

        mock_dispatch.reset_mock()

        # 4. /itch_analytics command
        _handle_telegram_webhook_async({"message": {"text": "/itch_analytics"}}, self.mock_settings)
        mock_dispatch.assert_called_with("itch_analytics", self.mock_settings)

        mock_dispatch.reset_mock()
        mock_send.reset_mock()

        # 5. /status command includes itch.io engine section
        _handle_telegram_webhook_async({"message": {"text": "/status"}}, self.mock_settings)
        status_text = mock_send.call_args[0][1]
        self.assertIn("itch.io Engine:", status_text)
        self.assertIn("aman179102", status_text)
        self.assertIn("/itch", status_text)

    @patch("automations.hn.runner.run_all")
    @patch("automations.itch.runner.run_all")
    def test_server_all_trigger_invokes_itch_when_configured(self, mock_itch_run_all, mock_hn_run_all):
        ok = _dispatch_automation_job("all", self.mock_settings)
        self.assertTrue(ok)
        mock_itch_run_all.assert_called_once_with(self.mock_settings)


if __name__ == "__main__":
    unittest.main()

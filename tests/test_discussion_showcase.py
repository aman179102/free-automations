"""Unit and integration tests for Hacker News Discussion Engine and Showcase Manager.
Tests Ask HN architecture response drafting, Show HN formatting, and runner pipeline integration.
"""

from __future__ import annotations

import glob
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure workspace root and virtualenv site-packages are in path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

for site in glob.glob(str(WORKSPACE_ROOT / ".venv" / "lib" / "python*" / "site-packages")):
    if site not in sys.path:
        sys.path.insert(0, site)

from automations.hn.discussion import (
    ARCHITECTURE_TOPICS,
    DiscussionEngine,
    draft_architect_response,
    score_discussion_candidate,
)
from automations.hn.models import DiscussionTopic, HNStory, ShowcaseProject
from automations.hn.runner import (
    HNRunner,
    run_all,
    run_discussion,
    run_hiring_radar,
    run_showcase,
)
from automations.hn.showcase import PROJECT_REGISTRY, ShowcaseManager


class TestDiscussionEngine(unittest.TestCase):
    """Tests for Ask HN discussion candidate scoring and senior systems response drafting."""

    def test_score_discussion_candidate_distributed_storage(self):
        story = HNStory(
            id=6001,
            title="Ask HN: Designing distributed object storage vs S3 blob replication?",
            text="<p>How do people handle erasure coding vs 3x replication on bare metal?</p>",
            score=45,
            descendants=22,
        )
        score, category, tags = score_discussion_candidate(story)
        self.assertGreater(score, 25.0)
        self.assertEqual(category, "Distributed Storage & Reliability")
        self.assertTrue(any(t in ["storage", "s3", "blob", "erasure coding", "replication"] for t in tags))

    def test_score_discussion_candidate_streaming_pipelines(self):
        story = HNStory(
            id=6002,
            title="Ask HN: High throughput event-driven pipeline with Redis Streams or Kafka?",
            text="<p>We are ingesting time series telemetry data into TimescaleDB.</p>",
            score=60,
            descendants=35,
        )
        score, category, tags = score_discussion_candidate(story)
        self.assertGreater(score, 25.0)
        self.assertEqual(category, "Real-Time Streaming & High-Throughput Ingestion")
        self.assertTrue(any(t in ["streaming", "kafka", "redis streams", "timescaledb"] for t in tags))

    def test_score_discussion_candidate_databases_io(self):
        story = HNStory(
            id=6003,
            title="Ask HN: PostgreSQL write amplification and LSM-tree vs B-Tree indexing",
            text="<p>Struggling with database compaction and random disk writes.</p>",
            score=75,
            descendants=40,
        )
        score, category, tags = score_discussion_candidate(story)
        self.assertGreater(score, 25.0)
        self.assertEqual(category, "Databases & I/O Internals")

    def test_score_discussion_candidate_concurrency(self):
        story = HNStory(
            id=6004,
            title="Ask HN: Goroutines worker pool vs OS threading for high concurrency",
            text="<p>Managing go runtime memory allocation under spike traffic.</p>",
            score=50,
            descendants=18,
        )
        score, category, tags = score_discussion_candidate(story)
        self.assertGreater(score, 25.0)
        self.assertEqual(category, "Concurrency & Runtime Optimization")

    def test_score_discussion_candidate_unrelated(self):
        story = HNStory(
            id=6005,
            title="Ask HN: Favorite books you read this summer?",
            text="<p>Looking for fiction and non-fiction recommendations.</p>",
            score=120,
            descendants=150,
        )
        score, category, tags = score_discussion_candidate(story)
        self.assertEqual(score, 0.0)
        self.assertEqual(tags, [])

    def test_draft_architect_response_distributed_storage(self):
        story = HNStory(id=7001, title="Ask HN: Object storage architecture tradeoffs")
        topic = DiscussionTopic(
            story=story,
            category="Distributed Storage & Reliability",
            relevance_score=45.0,
        )
        response = draft_architect_response(topic, author_name="Aman Kumar")
        self.assertIn("Reed-Solomon erasure coding", response)
        self.assertIn("3x replication", response)
        self.assertIn("1.5x", response)
        self.assertIn("SHA-256", response)

    def test_draft_architect_response_streaming(self):
        story = HNStory(id=7002, title="Ask HN: Scaling ingestion pipelines")
        topic = DiscussionTopic(
            story=story,
            category="Real-Time Streaming & High-Throughput Ingestion",
            relevance_score=50.0,
        )
        response = draft_architect_response(topic, author_name="Aman Kumar")
        self.assertIn("Redis Streams", response)
        self.assertIn("TimescaleDB", response)
        self.assertIn("Backpressure & Sampling", response)

    def test_draft_architect_response_concurrency(self):
        story = HNStory(id=7003, title="Ask HN: High concurrency servers")
        topic = DiscussionTopic(
            story=story,
            category="Concurrency & Runtime Optimization",
            relevance_score=40.0,
        )
        response = draft_architect_response(topic, author_name="Aman Kumar")
        self.assertIn("Go's GMP scheduler", response)
        self.assertIn("Worker Pool Sizing", response)
        self.assertIn("Backpressure", response)

    def test_draft_architect_response_ai_path(self):
        mock_settings = MagicMock()
        mock_settings.has_ai = True

        ai_text = (
            "From an engineering standpoint, chunk size directly dictates the ratio of metadata table rows to storage blocks. "
            "Using 4MB chunks with Reed-Solomon 4+2 provides balanced I/O alignment."
        )
        story = HNStory(id=7004, title="Ask HN: Chunk sizing in object storage")
        topic = DiscussionTopic(story=story, category="Distributed Storage & Reliability")

        with patch("automations.hn.discussion.generate_text", return_value=ai_text):
            response = draft_architect_response(topic, author_name="Aman Kumar", settings=mock_settings)
            self.assertEqual(response, ai_text)

    def test_discussion_engine_find_architecture_discussions(self):
        mock_api = MagicMock()
        mock_api.search_algolia_by_date.return_value = {
            "hits": [
                {
                    "objectID": "8001",
                    "title": "Ask HN: How do you architect distributed storage queues?",
                    "story_text": "Evaluating Kafka vs Redis Streams for high throughput.",
                    "points": 50,
                    "num_comments": 15,
                    "created_at_i": 1700600000,
                }
            ]
        }
        mock_api.fetch_askstories.return_value = []

        engine = DiscussionEngine(api_client=mock_api)
        topics = engine.find_architecture_discussions(limit=5)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].story.id, 8001)
        self.assertGreater(topics[0].relevance_score, 0.0)
        self.assertTrue(len(topics[0].draft_response) > 50)


class TestShowcaseManager(unittest.TestCase):
    """Tests for Show HN project registry, formatting, and drafting."""

    def setUp(self):
        self.manager = ShowcaseManager()

    def test_registry_contains_flagship_projects(self):
        expected_slugs = {
            "distributed-file-storage",
            "real-time-analytics-pipeline",
            "podvoice",
        }
        self.assertTrue(expected_slugs.issubset(set(PROJECT_REGISTRY.keys())))

    def test_list_projects(self):
        projects = self.manager.list_projects()
        self.assertGreaterEqual(len(projects), 3)
        slugs = [p.slug for p in projects]
        self.assertIn("distributed-file-storage", slugs)
        self.assertIn("real-time-analytics-pipeline", slugs)
        self.assertIn("podvoice", slugs)

    def test_get_project_by_slug_and_partial(self):
        p1 = self.manager.get_project("distributed-file-storage")
        self.assertIsNotNone(p1)
        self.assertEqual(p1.slug, "distributed-file-storage")
        self.assertTrue(p1.suggested_title.startswith("Show HN:"))
        self.assertIn("Reed-Solomon", p1.suggested_title)

        p2 = self.manager.get_project("analytics")
        self.assertIsNotNone(p2)
        self.assertEqual(p2.slug, "real-time-analytics-pipeline")

        p3 = self.manager.get_project("podvoice")
        self.assertIsNotNone(p3)
        self.assertIn("Local-first", p3.suggested_title)

        self.assertIsNone(self.manager.get_project("nonexistent-project-xyz"))

    def test_draft_show_hn_structure(self):
        project = self.manager.draft_show_hn("distributed-file-storage")
        self.assertIsNotNone(project)
        self.assertTrue(project.suggested_title.startswith("Show HN:"))
        self.assertIn("https://github.com/aman179102/distributed-file-storage-service", project.github_url)

        body = project.body_markdown
        self.assertIn("### Motivation", body)
        self.assertIn("### Architecture Highlights", body)
        self.assertIn("Reed-Solomon Erasure Coding (4+2)", body)
        self.assertIn("### Trade-offs & Lessons Learned", body)
        self.assertIn("https://github.com/aman179102/distributed-file-storage-service", body)

    def test_draft_show_hn_ai_custom_angle(self):
        mock_settings = MagicMock()
        mock_settings.has_ai = True

        custom_body = (
            "Show HN: Benchmarking Reed-Solomon Erasure Coding against 3x replication on commodity NVMe drives.\n"
            "GitHub: https://github.com/aman179102/distributed-file-storage-service"
        )
        with patch("automations.hn.showcase.generate_text", return_value=custom_body):
            project = self.manager.draft_show_hn(
                "distributed-file-storage",
                custom_angle="Focus heavily on NVMe IOPS benchmarks and SIMD CPU overhead",
                settings=mock_settings,
            )
            self.assertEqual(project.body_markdown, custom_body)


class TestHNRunnerAndIntegration(unittest.TestCase):
    """Tests for HNRunner pipeline orchestration, deduplication, and module-level functions."""

    def setUp(self):
        self.mock_settings = MagicMock()
        self.mock_settings.has_telegram = False
        self.mock_settings.has_ai = False
        self.mock_settings.state_dir = "/tmp/test_hn_state"

        # Mock runner with mock engines
        self.runner = HNRunner(settings=self.mock_settings, state_file="/tmp/test_hn_state.json")
        self.runner.hiring_radar = MagicMock()
        self.runner.discussion_engine = MagicMock()
        self.runner.showcase_manager = ShowcaseManager()

    def test_run_hiring_radar_deduplication(self):
        lead1 = MagicMock()
        lead1.id = 901
        lead1.company = "Stripe"
        lead1.role = "Go Engineer"
        lead1.founder_or_contact = "Patrick"
        lead1.email = "p@stripe.com"
        lead1.apply_url = "https://stripe.com/apply"
        lead1.location = "100% Remote"
        lead1.clean_text = "Senior Go engineer remote"
        lead1.matched_keywords = ["Golang"]
        lead1.pitch_draft = "Hi Patrick..."

        lead2 = MagicMock()
        lead2.id = 902
        lead2.company = "Linear"
        lead2.role = "Fullstack Dev"
        lead2.clean_text = "Remote fullstack"
        lead2.matched_keywords = ["Web Dev"]

        self.runner.hiring_radar.scan_latest_thread.return_value = [lead1, lead2]

        with patch.object(self.runner, "_load_state", return_value={"seen_hiring_ids": ["901"]}):
            with patch.object(self.runner, "_save_state") as mock_save:
                new_leads = self.runner.run_hiring_radar(max_leads=5, send_telegram=False)
                # lead1 (901) was already seen, so only lead2 (902) should be processed
                self.assertEqual(len(new_leads), 1)
                self.assertEqual(new_leads[0].id, 902)
                mock_save.assert_called_once()

    def test_run_discussion_pipeline(self):
        topic = MagicMock()
        topic.story.id = 4444
        topic.story.title = "Ask HN: Event streaming architecture"
        topic.story.score = 50
        topic.story.descendants = 12
        topic.category = "Real-Time Streaming & High-Throughput Ingestion"
        topic.draft_response = "Decouple HTTP ingestion from persistence..."

        self.runner.discussion_engine.find_architecture_discussions.return_value = [topic]

        with patch.object(self.runner, "_load_state", return_value={"seen_discussion_ids": []}):
            with patch.object(self.runner, "_save_state"):
                topics = self.runner.run_discussion(max_discussions=3, send_telegram=False)
                self.assertEqual(len(topics), 1)
                self.assertEqual(topics[0].story.id, 4444)

    def test_run_showcase_pipeline(self):
        with patch.object(self.runner, "_load_state", return_value={}):
            with patch.object(self.runner, "_save_state"):
                projects = self.runner.run_showcase(project_slug="podvoice", send_telegram=False)
                self.assertEqual(len(projects), 1)
                self.assertEqual(projects[0].slug, "podvoice")

    def test_run_all_master_orchestrator(self):
        self.runner.run_hiring_radar = MagicMock(return_value=[MagicMock(company="Acme")])
        self.runner.run_discussion = MagicMock(return_value=[MagicMock(story=MagicMock(title="Ask HN Test"))])
        self.runner.run_showcase = MagicMock(return_value=[MagicMock(title="Show HN Go")])

        res = self.runner.run_all(send_telegram=False)
        self.assertEqual(res["hiring_leads_count"], 1)
        self.assertEqual(res["discussions_count"], 1)
        self.assertEqual(res["showcases_count"], 1)

    @patch("automations.hn.runner.HNRunner")
    def test_module_level_convenience_functions(self, mock_runner_cls):
        mock_instance = MagicMock()
        mock_runner_cls.return_value = mock_instance

        run_hiring_radar(dry_run=True, max_leads=2)
        mock_instance.run_hiring_radar.assert_called_with(max_leads=2, send_telegram=False)

        run_discussion(dry_run=False, max_discussions=4)
        mock_instance.run_discussion.assert_called_with(max_discussions=4, send_telegram=True)

        run_showcase(dry_run=True, project_slug="podvoice")
        mock_instance.run_showcase.assert_called_with(project_slug="podvoice", send_telegram=False)

        run_all(dry_run=False)
        mock_instance.run_all.assert_called_with(send_telegram=True)


if __name__ == "__main__":
    unittest.main()

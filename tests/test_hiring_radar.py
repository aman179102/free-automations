"""Unit and integration tests for Hacker News Hiring Radar.
Tests role classification, remote filtering, contact & budget extraction, and pitch generation.
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

from automations.hn.hiring_radar import (
    HiringRadar,
    classify_tech_stack,
    draft_tailored_pitch,
    extract_apply_urls,
    extract_budget,
    extract_emails,
    is_remote_eligible,
    parse_header_tokens,
)
from automations.hn.models import HNComment, HNStory, HiringLead


class TestHiringRadarClassification(unittest.TestCase):
    """Tests for role classification, header parsing, and remote eligibility filters."""

    def test_remote_eligible_positive(self):
        self.assertTrue(is_remote_eligible("We are hiring a 100% remote Golang backend engineer."))
        self.assertTrue(is_remote_eligible("Role is fully remote anywhere in the world."))
        self.assertTrue(is_remote_eligible("Remote ok | US & EU timezones"))
        self.assertTrue(is_remote_eligible("Remote friendly startup building next-gen storage."))
        self.assertTrue(is_remote_eligible("WFH / Work from home anywhere"))
        self.assertTrue(is_remote_eligible("Remote (Worldwide) | Python dev"))

    def test_remote_eligible_disqualifiers(self):
        self.assertFalse(is_remote_eligible("Senior Go Engineer | In-office only | NYC"))
        self.assertFalse(is_remote_eligible("Python Dev | On-site only | San Francisco"))
        self.assertFalse(is_remote_eligible("Backend Lead | No remote | London"))
        self.assertFalse(is_remote_eligible("Relocation required to Berlin"))
        self.assertFalse(is_remote_eligible("Must relocate to Austin, TX"))
        self.assertFalse(is_remote_eligible("Hybrid only (must be in office 3 days/week)"))
        self.assertFalse(is_remote_eligible("Onsite required at headquarters"))

    def test_remote_eligible_no_remote_keywords(self):
        self.assertFalse(is_remote_eligible("Acme Corp is hiring a Java developer in Boston."))

    def test_classify_tech_stack_golang(self):
        text = "Looking for a Go developer experienced with goroutines and gRPC microservices."
        tech = classify_tech_stack(text)
        self.assertIn("Golang", tech)

        # Ensure common phrases like 'go to' don't trigger false positives if no other go keywords
        unrelated = "We want you to go to the store and build PHP pages."
        self.assertNotIn("Golang", classify_tech_stack(unrelated))

    def test_classify_tech_stack_fastapi_and_python(self):
        text = "We build async REST APIs using Python, FastAPI, and Pydantic."
        tech = classify_tech_stack(text)
        self.assertIn("Python", tech)
        self.assertIn("FastAPI", tech)

    def test_classify_tech_stack_web_dev(self):
        text = "Frontend / Fullstack engineer skilled in React, Next.js, and TypeScript."
        tech = classify_tech_stack(text)
        self.assertIn("Web Dev", tech)

    def test_classify_tech_stack_mvp_founding_dev(self):
        text = "Early stage startup seeking a founding engineer to build an MVP from 0 to 1."
        tech = classify_tech_stack(text)
        self.assertIn("MVP / Founding Dev", tech)

    def test_classify_tech_stack_distributed_systems(self):
        text = "Architecting distributed object storage, Kafka event streaming, and Timescale hypertables."
        tech = classify_tech_stack(text)
        self.assertIn("Distributed Systems", tech)

    def test_classify_tech_stack_unrelated(self):
        text = "Hiring an iOS Swift and Objective-C mobile engineer."
        self.assertEqual(classify_tech_stack(text), [])

    def test_parse_header_tokens_pipe(self):
        header = "Supabase | Senior Backend Engineer (Go) | Remote | Full-time"
        info = parse_header_tokens(header)
        self.assertEqual(info["company"], "Supabase")
        self.assertEqual(info["role"], "Senior Backend Engineer (Go)")
        self.assertEqual(info["location"], "Remote")

    def test_parse_header_tokens_dash(self):
        header = "Vercel - Fullstack Developer - 100% Remote"
        info = parse_header_tokens(header)
        self.assertEqual(info["company"], "Vercel")
        self.assertEqual(info["role"], "Fullstack Developer")
        self.assertEqual(info["location"], "100% Remote")

    def test_parse_header_tokens_markdown_link(self):
        header = "[Acme Storage](https://acme.io) | Go Engineer | Remote"
        info = parse_header_tokens(header)
        self.assertEqual(info["company"], "Acme Storage")
        self.assertEqual(info["role"], "Go Engineer")

    def test_parse_header_tokens_swapped_location(self):
        header = "CloudScale | Remote | Systems Architect"
        info = parse_header_tokens(header)
        self.assertEqual(info["company"], "CloudScale")
        self.assertEqual(info["role"], "Systems Architect")
        self.assertEqual(info["location"], "Remote")


class TestContactAndBudgetExtraction(unittest.TestCase):
    """Tests for email de-obfuscation, ATS apply URL extraction, and budget extraction."""

    def test_extract_emails_standard(self):
        text = "Send your resume to hiring@startup.io or dev-team@acme.co."
        emails = extract_emails(text)
        self.assertIn("hiring@startup.io", emails)
        self.assertIn("dev-team@acme.co", emails)

    def test_extract_emails_obfuscated_brackets(self):
        text = "Reach out directly: alex [at] techcorp [dot] com or founders (at) fastdev (dot) io"
        emails = extract_emails(text)
        self.assertIn("alex@techcorp.com", emails)
        self.assertIn("founders@fastdev.io", emails)

    def test_extract_emails_obfuscated_words(self):
        text = "Email us at jobs at modernai dot com for quick review."
        emails = extract_emails(text)
        self.assertIn("jobs@modernai.com", emails)

    def test_extract_emails_bracket_domain(self):
        text = "Contact me at sarah [at] venture.cc."
        emails = extract_emails(text)
        self.assertIn("sarah@venture.cc", emails)

    def test_extract_apply_urls_ats_prioritized(self):
        text = (
            "Learn more at https://mycompany.com and apply directly at "
            "https://jobs.lever.co/mycompany/backend-dev-123. Discuss on https://news.ycombinator.com/item?id=1."
        )
        urls = extract_apply_urls(text)
        self.assertIn("https://jobs.lever.co/mycompany/backend-dev-123", urls)
        self.assertIn("https://mycompany.com", urls)
        # HN links should be excluded
        self.assertFalse(any("news.ycombinator.com" in u for u in urls))
        # ATS domain should be prioritized first
        self.assertEqual(urls[0], "https://jobs.lever.co/mycompany/backend-dev-123")

    def test_extract_budget_dollar_ranges(self):
        self.assertEqual(extract_budget("Salary range: $140k - $180k plus equity"), "$140k - $180k")
        self.assertEqual(extract_budget("Paying $80/hr for senior contract work"), "$80/hr")
        self.assertEqual(extract_budget("Competitive rate: $150,000/yr"), "$150,000/yr")

    def test_extract_budget_currencies_and_equity(self):
        self.assertEqual(extract_budget("Compensation: €90k - €120k"), "€90k - €120k")
        self.assertEqual(extract_budget("Budget: £75k remote UK/EU"), "£75k")
        self.assertEqual(extract_budget("Founding role with 2.5% equity"), "2.5% equity")
        self.assertEqual(extract_budget("Monthly stipend: $2,000/mo"), "stipend: $2,000/mo")

    def test_extract_budget_none(self):
        self.assertIsNone(extract_budget("We offer great benefits and dental insurance."))


class TestPitchGeneration(unittest.TestCase):
    """Tests for deterministic and AI pitch generation tailored to Aman's portfolio."""

    def setUp(self):
        self.base_lead = HiringLead(
            id=1001,
            story_id=500,
            company="Distributed Labs",
            role="Distributed Systems Engineer",
            founder_or_contact="Dan",
            email="dan@distriblabs.com",
            apply_url="https://distriblabs.com/apply",
            matched_keywords=["Golang", "Distributed Systems"],
            clean_text="We need a Go engineer to scale our S3 storage nodes.",
        )

    def test_draft_tailored_pitch_golang(self):
        pitch = draft_tailored_pitch(
            self.base_lead,
            author_name="Aman Kumar",
            github_url="https://github.com/aman179102",
        )
        self.assertIn("Hi Dan", pitch)
        self.assertIn("Distributed Systems Engineer", pitch)
        self.assertIn("Distributed File Storage Service in Go", pitch)
        self.assertIn("Reed-Solomon 4+2", pitch)
        self.assertIn("https://github.com/aman179102", pitch)
        self.assertIn("Aman Kumar", pitch)

    def test_draft_tailored_pitch_python_fastapi(self):
        lead = HiringLead(
            id=1002,
            story_id=500,
            company="StreamMetrics",
            role="Backend Engineer",
            founder_or_contact="Elena",
            email="elena@streammetrics.io",
            matched_keywords=["Python", "FastAPI"],
            clean_text="FastAPI backend engineer for real-time analytics stream.",
        )
        pitch = draft_tailored_pitch(
            lead,
            author_name="Aman Kumar",
            github_url="https://github.com/aman179102",
        )
        self.assertIn("Hi Elena", pitch)
        self.assertIn("Real-Time Streaming Analytics Pipeline", pitch)
        self.assertIn("TimescaleDB hypertables", pitch)
        self.assertIn("https://github.com/aman179102", pitch)

    def test_draft_tailored_pitch_fullstack_mvp(self):
        lead = HiringLead(
            id=1003,
            story_id=500,
            company="NextGen Studio",
            role="Founding Engineer",
            founder_or_contact="Mark",
            matched_keywords=["MVP / Founding Dev", "Web Dev"],
            clean_text="Looking for a founding developer to build an MVP.",
        )
        pitch = draft_tailored_pitch(
            lead,
            author_name="Aman Kumar",
            github_url="https://github.com/aman179102",
        )
        self.assertIn("Hi Mark", pitch)
        self.assertIn("0-to-1 MVPs", pitch)
        self.assertIn("https://github.com/aman179102", pitch)

    def test_draft_tailored_pitch_ai_path(self):
        mock_settings = MagicMock()
        mock_settings.has_ai = True

        ai_response = (
            "Hi Dan, I saw your Go storage opening. I authored an S3-compatible Distributed File Storage Service "
            "in Go with Reed-Solomon 4+2 encoding. Available 100% remote. GitHub: https://github.com/aman179102"
        )
        with patch("automations.hn.hiring_radar.generate_text", return_value=ai_response):
            pitch = draft_tailored_pitch(
                self.base_lead,
                author_name="Aman Kumar",
                github_url="https://github.com/aman179102",
                settings=mock_settings,
            )
            self.assertEqual(pitch, ai_response)

    def test_draft_tailored_pitch_ai_fallback_on_failure(self):
        mock_settings = MagicMock()
        mock_settings.has_ai = True

        with patch("automations.hn.hiring_radar.generate_text", return_value=None):
            pitch = draft_tailored_pitch(
                self.base_lead,
                author_name="Aman Kumar",
                github_url="https://github.com/aman179102",
                settings=mock_settings,
            )
            self.assertIn("Distributed File Storage Service in Go", pitch)


class TestHiringRadarEngine(unittest.TestCase):
    """Tests for HiringRadar comment parsing and thread scanning."""

    def setUp(self):
        self.mock_api = MagicMock()
        self.radar = HiringRadar(api_client=self.mock_api)

    def test_parse_comment_lead_eligible(self):
        comment = HNComment(
            id=7701,
            by="tech_founder",
            text=(
                "Nexus Labs | Senior Go &amp; Python Engineer | 100% Remote | $150k - $180k<p>"
                "We are building distributed storage nodes. Contact Marcus at marcus [at] nexuslabs [dot] io "
                "or apply at https://jobs.ashbyhq.com/nexuslabs/123."
            ),
            time=1700500000,
            story_id=900,
        )
        lead = self.radar.parse_comment_lead(comment, story_id=900)
        self.assertIsNotNone(lead)
        self.assertEqual(lead.id, 7701)
        self.assertEqual(lead.company, "Nexus Labs")
        self.assertEqual(lead.location, "100% Remote")
        self.assertEqual(lead.email, "marcus@nexuslabs.io")
        self.assertEqual(lead.founder_or_contact, "Marcus")
        self.assertEqual(lead.apply_url, "https://jobs.ashbyhq.com/nexuslabs/123")
        self.assertEqual(lead.budget, "$150k - $180k")
        self.assertIn("Golang", lead.matched_keywords)
        self.assertIn("Python", lead.matched_keywords)
        self.assertIn("mailto:marcus@nexuslabs.io", lead.mailto_url)
        self.assertGreater(lead.score, 30.0)

    def test_parse_comment_lead_disqualified_onsite(self):
        comment = HNComment(
            id=7702,
            by="corporate_recruiter",
            text="BigCorp | Backend Go Dev | In-office only NYC | $120k<p>Send CV to hr@bigcorp.com</p>",
            time=1700500100,
            story_id=900,
        )
        lead = self.radar.parse_comment_lead(comment, story_id=900)
        self.assertIsNone(lead)

    def test_parse_comment_lead_disqualified_no_tech_match(self):
        comment = HNComment(
            id=7703,
            by="mobile_shop",
            text="AppCo | iOS Swift Dev | Fully Remote<p>Contact swift@appco.com</p>",
            time=1700500200,
            story_id=900,
        )
        lead = self.radar.parse_comment_lead(comment, story_id=900)
        self.assertIsNone(lead)

    def test_scan_latest_thread(self):
        mock_thread = HNStory(id=900, title="Ask HN: Who is hiring? (November 2026)")
        self.mock_api.find_latest_hiring_thread.return_value = mock_thread

        comm1 = HNComment(
            id=101,
            by="lead_a",
            text="CloudAI | Go / FastAPI Dev | Remote | $160k<p>Reach out to Alice at alice@cloudai.com</p>",
        )
        comm2 = HNComment(
            id=102,
            by="lead_b",
            text="BankTech | Java Dev | Onsite Only London",
        )
        self.mock_api.fetch_thread_comments.return_value = [comm1, comm2]

        leads = self.radar.scan_latest_thread(max_comments=10)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].company, "CloudAI")
        self.assertEqual(leads[0].founder_or_contact, "Alice")
        self.assertEqual(leads[0].email, "alice@cloudai.com")


if __name__ == "__main__":
    unittest.main()

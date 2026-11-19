"""Hacker News 'Who is Hiring?' Radar: Parses monthly threads, filters 100% remote roles,
extracts founder contacts/links, and drafts high-signal tailored pitches citing Aman's GitHub.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Optional

from automations.hn.api import HNAPIClient
from automations.hn.models import HNComment, HNStory, HiringLead

try:
    from common.ai import generate_text
    from common.config import Settings, load_settings
except ImportError:
    Settings = None  # type: ignore
    load_settings = None  # type: ignore
    generate_text = None  # type: ignore

logger = logging.getLogger(__name__)

# Target Tech Stacks for Aman Kumar
TECH_KEYWORDS = {
    "golang": ["golang", "go", "go lang", "goroutine", "grpc"],
    "python": ["python", "python3", "asyncio", "django", "flask", "celery", "pydantic"],
    "fastapi": ["fastapi", "starlette", "uvicorn"],
    "web_dev": ["web dev", "fullstack", "full-stack", "frontend", "front-end", "react", "typescript", "node", "next.js", "vue", "javascript"],
    "mvp": ["mvp", "0 to 1", "0-to-1", "founding engineer", "founding dev", "build from scratch", "early stage"],
    "systems": ["distributed systems", "storage", "backend engineer", "infrastructure", "redis", "kafka", "postgresql", "timescale"],
}

# Strict On-Site / In-Office Disqualifiers
ONSITE_DISQUALIFIERS = [
    "in-office only",
    "on-site only",
    "onsite only",
    "no remote",
    "not remote",
    "relocation required",
    "must relocate",
    "must be in office",
    "office based only",
    "hybrid (must be",
    "hybrid only",
    "must be local to",
    "onsite required",
]

# Remote keywords indicating 100% remote availability
REMOTE_PATTERNS = [
    r"\bremote\b",
    r"\b100%\s*remote\b",
    r"\bfully\s*remote\b",
    r"\bremote\s*ok\b",
    r"\bremote\s*first\b",
    r"\bremote\s*friendly\b",
    r"\banywhere\b",
    r"\bworldwide\b",
    r"\bwfh\b",
    r"\bwork\s*from\s*home\b",
    r"\bglobal\s*remote\b",
    r"\bremote\s*\(anywhere\)\b",
    r"\bremote\s*\(worldwide\)\b",
]

# De-obfuscation patterns for emails
OBFUSCATED_EMAIL_PATTERNS = [
    # user [at] example [dot] com
    r"([a-zA-Z0-9_.+-]+)\s*\[\s*at\s*\]\s*([a-zA-Z0-9-]+)\s*\[\s*dot\s*\]\s*([a-zA-Z0-9-.]+)",
    # user (at) example (dot) com
    r"([a-zA-Z0-9_.+-]+)\s*\(\s*at\s*\)\s*([a-zA-Z0-9-]+)\s*\(\s*dot\s*\)\s*([a-zA-Z0-9-.]+)",
    # user at example dot com
    r"([a-zA-Z0-9_.+-]+)\s+at\s+([a-zA-Z0-9-]+)\s+dot\s+([a-zA-Z0-9-.]+)",
    # user [at] example.com
    r"([a-zA-Z0-9_.+-]+)\s*\[\s*at\s*\]\s*([a-zA-Z0-9-.]+)",
    # user (at) example.com
    r"([a-zA-Z0-9_.+-]+)\s*\(\s*at\s*\)\s*([a-zA-Z0-9-.]+)",
]


def extract_emails(text: str) -> list[str]:
    """Extract standard and de-obfuscated email addresses from text."""
    if not text:
        return []

    emails: list[str] = []

    # 1. Standard emails: foo@bar.com
    standard_matches = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", text)
    for m in standard_matches:
        cleaned = m.strip(".,;:()")
        if cleaned and cleaned not in emails:
            emails.append(cleaned)

    # 2. De-obfuscate common patterns
    for pat in OBFUSCATED_EMAIL_PATTERNS:
        matches = re.findall(pat, text, flags=re.IGNORECASE)
        for match in matches:
            if len(match) == 3:
                user, domain, tld = match
                tld = tld.replace(" ", "").replace("dot", ".").strip(".,;")
                email = f"{user.strip()}@{domain.strip()}.{tld.strip()}"
                if email not in emails:
                    emails.append(email)
            elif len(match) == 2:
                user, host = match
                host_clean = host.strip().rstrip(".,;:")
                if "." in host_clean:
                    email = f"{user.strip()}@{host_clean}"
                    if email not in emails:
                        emails.append(email)

    return emails


def extract_apply_urls(text: str) -> list[str]:
    """Extract apply URLs, ATS links, and website links from text."""
    if not text:
        return []

    # Find all HTTP / HTTPS urls
    raw_urls = re.findall(r"https?://[^\s<>\"\'\)]+", text)
    apply_urls: list[str] = []
    ats_domains = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "breezy.hr", "notion.site", "apply", "jobs", "careers")

    for url in raw_urls:
        url_clean = url.rstrip(".,;)>")
        # Prioritize job / careers / apply links
        is_job_link = any(d in url_clean.lower() for d in ats_domains)
        if is_job_link and url_clean not in apply_urls:
            apply_urls.insert(0, url_clean)
        elif url_clean not in apply_urls and not url_clean.startswith("https://news.ycombinator.com"):
            apply_urls.append(url_clean)

    return apply_urls


def extract_budget(text: str) -> Optional[str]:
    """Extract budget, compensation, hourly rate, or equity details from text."""
    if not text:
        return None
    match = re.search(
        r"([\$€£₹]\s?\d+[\d,k]*(?:\s?-\s?[\$€£₹]?\s?\d+[\d,k]*)?(?:\s?/\s?(?:hr|mo|yr|year)|k)?|\d+(?:\.\d+)?%\s?equity|\bstipend\s*[:=-]?\s*[\$€£₹]?\d+[\d,k]*(?:\s?/\s?mo)?)",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None


def is_remote_eligible(text: str) -> bool:
    """Strictly checks whether a posting is 100% remote and free of in-office requirements."""
    lower = text.lower()

    # Disqualify if explicit onsite/in-office required
    for disq in ONSITE_DISQUALIFIERS:
        if disq in lower:
            return False

    # Check for presence of remote pattern
    return any(re.search(pat, lower) for pat in REMOTE_PATTERNS)


def classify_tech_stack(text: str) -> list[str]:
    """Detects target technologies (Golang, Python, FastAPI, Web Dev, MVP, Systems)."""
    lower = text.lower()
    matched = []

    # Check Golang
    if re.search(r"\b(golang|go)\b", lower) and not re.search(r"\bgo to\b|\bgo with\b|\blet's go\b", lower):
        matched.append("Golang")
    elif "golang" in lower:
        matched.append("Golang")

    # Check FastAPI
    if "fastapi" in lower or "starlette" in lower:
        matched.append("FastAPI")

    # Check Python
    if "python" in lower or "django" in lower or "flask" in lower:
        matched.append("Python")

    # Check Web Dev
    if any(k in lower for k in ["react", "typescript", "next.js", "frontend", "fullstack", "full-stack", "web dev", "node"]):
        matched.append("Web Dev")

    # Check MVP / Founding Dev
    if any(k in lower for k in ["mvp", "0 to 1", "0-to-1", "founding engineer", "founding dev"]):
        matched.append("MVP / Founding Dev")

    # Check Distributed Systems / Storage
    if any(k in lower for k in ["distributed", "storage", "timescale", "redis streams", "kafka", "high throughput", "concurrency"]):
        matched.append("Distributed Systems")

    return matched


def parse_header_tokens(first_line: str) -> dict[str, str]:
    """
    Parses standard HN pipe/dash delimited headers:
    e.g. 'Supabase | Senior Backend Engineer (Go) | Remote | Full-time'
    """
    delimiters = ["|", " - ", " – ", " / "]
    tokens: list[str] = []

    for d in delimiters:
        if d in first_line:
            tokens = [t.strip() for t in first_line.split(d) if t.strip()]
            break

    if not tokens:
        tokens = [first_line.strip()]

    company = tokens[0] if len(tokens) >= 1 else "Startup"
    role = tokens[1] if len(tokens) >= 2 else ""
    location = tokens[2] if len(tokens) >= 3 else "Remote"

    # If role looks like a location, swap or clean
    if any(r in role.lower() for r in ["remote", "onsite", "hybrid", "san francisco", "new york", "london", "europe", "us"]):
        if len(tokens) >= 3 and not any(r in tokens[2].lower() for r in ["remote", "onsite"]):
            role, location = tokens[2], role
        else:
            role = "Software Engineer"

    # Strip clean company name if markdown link
    company = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", company).strip()

    return {
        "company": company[:60],
        "role": role[:80] or "Software Engineer",
        "location": location[:60],
    }


def draft_tailored_pitch(
    lead: HiringLead,
    author_name: str = "Aman Kumar",
    github_url: str = "https://github.com/aman179102",
    settings: Optional[Any] = None,
) -> str:
    """
    Drafts a high-signal, punchy pitch citing Aman's GitHub and relevant production projects.
    Uses AI if settings and common.ai are available; otherwise uses high-converting deterministic templates.
    """
    # Check if AI can be used
    if settings and generate_text and getattr(settings, "has_ai", False):
        system_prompt = (
            "You are drafting a high-signal, punchy outreach pitch for Aman Kumar to apply to a remote Hacker News opportunity.\n"
            "Aman's Profile:\n"
            "- Systems & AI Engineer, versatile fullstack & backend developer.\n"
            "- Tech Stack: Go, Python, FastAPI, Distributed Storage, Streaming Pipelines, APIs, Modern Web.\n"
            "- Key Open Source Projects on GitHub (https://github.com/aman179102):\n"
            "  * Distributed File Storage in Go (Reed-Solomon 4+2 erasure coding, SHA-256 deduplication, S3 REST API)\n"
            "  * Real-Time Streaming Analytics Pipeline (FastAPI, TimescaleDB hypertables, Redis Streams, WebSockets)\n"
            "  * PodVoice (Local-first offline multi-speaker AI podcast studio with FastAPI UI)\n"
            "- Available: 100% remote, immediately, zero corporate fluff, fast shipper, production quality.\n"
            "Keep the pitch under 150 words. Be confident, direct, and mention the specific matching project and GitHub."
        )
        user_prompt = (
            f"Opportunity Details:\n"
            f"Company: {lead.company}\n"
            f"Role: {lead.role}\n"
            f"Contact: {lead.founder_or_contact or 'Team'}\n"
            f"Detected Tech: {', '.join(lead.matched_keywords)}\n"
            f"Posting snippet: {lead.clean_text[:400]}\n\n"
            f"Draft the exact pitch message:"
        )
        ai_pitch = generate_text(settings, system_prompt, user_prompt, temperature=0.3)
        if ai_pitch and len(ai_pitch.strip()) > 40:
            return ai_pitch.strip()

    # Deterministic High-Signal Fallbacks
    contact_name = lead.founder_or_contact or "Team"
    company_name = lead.company or "your team"
    role_name = lead.role or "Software Engineer"
    matched_lower = [k.lower() for k in lead.matched_keywords]

    # Case 1: Golang / Distributed Systems
    if "golang" in matched_lower or "distributed systems" in matched_lower:
        return (
            f"Hi {contact_name},\n\n"
            f"I saw your posting for {role_name} at {company_name}. I'm a Systems Engineer specializing in Go, "
            f"distributed systems, and high-throughput backend services.\n\n"
            f"Relevant production project: I built an S3-compatible Distributed File Storage Service in Go "
            f"(https://github.com/aman179102/distributed-file-storage-service) featuring Reed-Solomon 4+2 erasure coding, "
            f"SHA-256 chunk deduplication, and PostgreSQL metadata indexing.\n\n"
            f"I am available for 100% remote execution and can hit the ground running immediately with clean architecture and zero hand-holding.\n\n"
            f"GitHub: {github_url}\n"
            f"Best,\n{author_name}"
        )

    # Case 2: Python / FastAPI / Streaming
    if "fastapi" in matched_lower or "python" in matched_lower:
        return (
            f"Hi {contact_name},\n\n"
            f"I came across your remote {role_name} posting at {company_name}. I specialize in Python and FastAPI backend architectures, "
            f"event-driven pipelines, and async performance optimization.\n\n"
            f"Relevant project: I authored the Real-Time Streaming Analytics Pipeline "
            f"(https://github.com/aman179102/real-time-analytics-pipeline) using FastAPI, TimescaleDB hypertables, "
            f"Redis Streams/Kafka, and WebSockets live push.\n\n"
            f"I work 100% remotely with fast turnaround, strict git hygiene, and production reliability.\n\n"
            f"GitHub: {github_url}\n"
            f"Best,\n{author_name}"
        )

    # Case 3: MVP / Web Dev / Fullstack
    return (
        f"Hi {contact_name},\n\n"
        f"I saw your posting for {role_name} at {company_name}. I am a versatile Systems & Fullstack Engineer "
        f"experienced in building 0-to-1 MVPs and high-performance web applications across Go, Python, FastAPI, and modern frontend stacks.\n\n"
        f"You can review my production codebases and architectures directly on GitHub: {github_url}\n\n"
        f"I'm available 100% remotely to start immediately, with high accountability and rapid shipping velocity.\n\n"
        f"Best,\n{author_name}"
    )


class HiringRadar:
    """Scans and analyzes Hacker News hiring threads for remote opportunities."""

    def __init__(self, api_client: Optional[HNAPIClient] = None, settings: Optional[Any] = None):
        self.api = api_client or HNAPIClient()
        self.settings = settings
        self.github_url = "https://github.com/aman179102"
        self.author_name = "Aman Kumar"
        if self.settings:
            self.github_url = getattr(self.settings, "portfolio_url", self.github_url)
            self.author_name = getattr(self.settings, "author_name", self.author_name)

    def parse_comment_lead(self, comment: HNComment, story_id: int) -> Optional[HiringLead]:
        """Parses a single HN comment into a HiringLead if it matches remote and tech filters."""
        clean_text = comment.clean_text
        if not clean_text or len(clean_text) < 30:
            return None

        # Check remote eligibility strictly
        if not is_remote_eligible(clean_text):
            return None

        # Check tech stack keywords
        matched_tech = classify_tech_stack(clean_text)
        if not matched_tech:
            return None

        # Parse first line / headers
        lines = [line.strip() for line in clean_text.split("\n") if line.strip()]
        first_line = lines[0] if lines else ""
        header_info = parse_header_tokens(first_line)

        # Extract contact information
        emails = extract_emails(clean_text)
        apply_urls = extract_apply_urls(clean_text)
        budget = extract_budget(clean_text)

        founder_or_contact = comment.by or ""
        # Check if text mentions a person's name
        name_match = re.search(r"\b(?:I'm|I am|Contact|Reach out to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", clean_text)
        if name_match:
            founder_or_contact = name_match.group(1)

        primary_email = emails[0] if emails else None
        primary_apply_url = apply_urls[0] if apply_urls else None

        lead = HiringLead(
            id=comment.id,
            story_id=story_id,
            company=header_info["company"],
            role=header_info["role"],
            location=header_info["location"] or "100% Remote",
            remote_status="100% Remote",
            tech_stack=matched_tech,
            founder_or_contact=founder_or_contact,
            email=primary_email,
            apply_url=primary_apply_url,
            budget=budget,
            raw_text=comment.text,
            clean_text=clean_text,
            score=float(len(matched_tech) * 10 + (20 if primary_email else 0) + (10 if primary_apply_url else 0)),
            matched_keywords=matched_tech,
            posted_at=comment.created_at.isoformat(),
        )

        # Draft initial tailored pitch using deterministic high-signal template
        lead.pitch_draft = draft_tailored_pitch(
            lead,
            author_name=self.author_name,
            github_url=self.github_url,
            settings=None,
        )

        return lead

    def scan_latest_thread(self, max_comments: int = 150) -> list[HiringLead]:
        """Scans the latest monthly 'Ask HN: Who is hiring?' thread."""
        thread = self.api.find_latest_hiring_thread()
        if not thread:
            logger.warning("Could not find latest Who is hiring thread.")
            return []

        logger.info("Found hiring thread: %s (ID: %d)", thread.title, thread.id)
        comments = self.api.fetch_thread_comments(thread.id, max_comments=max_comments)
        logger.info("Fetched %d comments from thread %d", len(comments), thread.id)

        leads: list[HiringLead] = []
        for comm in comments:
            lead = self.parse_comment_lead(comm, story_id=thread.id)
            if lead:
                leads.append(lead)

        # Sort leads by relevance score descending
        leads.sort(key=lambda x: x.score, reverse=True)
        return leads

"""Data models for Hacker News automation module."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def strip_html_tags(text: str) -> str:
    """Strip HTML tags and convert standard HN paragraphs and entities to plain text."""
    if not text:
        return ""
    # Convert <p> tags to newlines
    text = re.sub(r"(?i)<\s*p\s*/?\s*>", "\n\n", text)
    # Convert <br\s*/?> to newline
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    # Extract links if formatted as <a href="...">...</a>
    def _link_sub(match: re.Match) -> str:
        href = match.group(1)
        label = match.group(2)
        if href == label or not label:
            return href
        return f"{label} ({href})"

    text = re.sub(r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', _link_sub, text, flags=re.DOTALL | re.IGNORECASE)
    # Remove remaining HTML tags
    text = re.sub(r"<[^<]+?>", "", text)
    # Unescape HTML entities
    text = html.unescape(text)
    # Normalize multiple newlines/spaces
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class HNStory:
    """Represents an item/story from Hacker News (Firebase or Algolia)."""

    id: int
    title: str
    url: str = ""
    by: str = ""
    score: int = 0
    time: int = 0
    text: str = ""
    descendants: int = 0
    type: str = "story"
    kids: list[int] = field(default_factory=list)

    @property
    def hn_url(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"

    @property
    def clean_text(self) -> str:
        return strip_html_tags(self.text)

    @property
    def created_at(self) -> datetime:
        if self.time > 0:
            return datetime.fromtimestamp(self.time, tz=timezone.utc)
        return datetime.now(timezone.utc)

    @classmethod
    def from_firebase(cls, data: dict[str, Any]) -> HNStory:
        return cls(
            id=int(data.get("id", 0)),
            title=str(data.get("title", "") or ""),
            url=str(data.get("url", "") or ""),
            by=str(data.get("by", "") or ""),
            score=int(data.get("score", 0) or 0),
            time=int(data.get("time", 0) or 0),
            text=str(data.get("text", "") or ""),
            descendants=int(data.get("descendants", 0) or 0),
            type=str(data.get("type", "story") or "story"),
            kids=[int(k) for k in data.get("kids", []) if isinstance(k, (int, str)) and str(k).isdigit()],
        )

    @classmethod
    def from_algolia(cls, hit: dict[str, Any]) -> HNStory:
        # Algolia uses objectID and created_at_i
        obj_id = hit.get("objectID") or hit.get("id") or 0
        try:
            item_id = int(obj_id)
        except (ValueError, TypeError):
            item_id = 0

        created_i = hit.get("created_at_i") or 0
        kids = [int(k) for k in hit.get("children", []) if isinstance(k, (int, str)) and str(k).isdigit()]

        return cls(
            id=item_id,
            title=str(hit.get("title", "") or hit.get("story_title", "") or ""),
            url=str(hit.get("url", "") or hit.get("story_url", "") or ""),
            by=str(hit.get("author", "") or hit.get("by", "") or ""),
            score=int(hit.get("points", 0) or hit.get("score", 0) or 0),
            time=int(created_i),
            text=str(hit.get("story_text", "") or hit.get("comment_text", "") or ""),
            descendants=int(hit.get("num_comments", 0) or 0),
            type="story",
            kids=kids,
        )


@dataclass
class HNComment:
    """Represents a comment from Hacker News."""

    id: int
    by: str = ""
    text: str = ""
    time: int = 0
    parent: int = 0
    kids: list[int] = field(default_factory=list)
    story_id: Optional[int] = None
    deleted: bool = False
    dead: bool = False

    @property
    def hn_url(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"

    @property
    def clean_text(self) -> str:
        return strip_html_tags(self.text)

    @property
    def created_at(self) -> datetime:
        if self.time > 0:
            return datetime.fromtimestamp(self.time, tz=timezone.utc)
        return datetime.now(timezone.utc)

    @classmethod
    def from_firebase(cls, data: dict[str, Any], story_id: Optional[int] = None) -> HNComment:
        return cls(
            id=int(data.get("id", 0)),
            by=str(data.get("by", "") or ""),
            text=str(data.get("text", "") or ""),
            time=int(data.get("time", 0) or 0),
            parent=int(data.get("parent", 0) or 0),
            kids=[int(k) for k in data.get("kids", []) if isinstance(k, (int, str)) and str(k).isdigit()],
            story_id=story_id or int(data.get("parent", 0) or 0),
            deleted=bool(data.get("deleted", False)),
            dead=bool(data.get("dead", False)),
        )

    @classmethod
    def from_algolia(cls, hit: dict[str, Any], story_id: Optional[int] = None) -> HNComment:
        obj_id = hit.get("objectID") or hit.get("id") or 0
        try:
            item_id = int(obj_id)
        except (ValueError, TypeError):
            item_id = 0

        created_i = hit.get("created_at_i") or 0
        return cls(
            id=item_id,
            by=str(hit.get("author", "") or ""),
            text=str(hit.get("comment_text", "") or hit.get("text", "") or ""),
            time=int(created_i),
            parent=int(hit.get("parent_id", 0) or 0),
            story_id=story_id or int(hit.get("story_id", 0) or 0),
            deleted=False,
            dead=False,
        )


@dataclass
class HiringLead:
    """Represents a filtered 100% remote hiring lead parsed from monthly Who is Hiring threads."""

    id: int
    story_id: int
    company: str = ""
    role: str = ""
    location: str = "100% Remote"
    remote_status: str = "100% Remote"
    tech_stack: list[str] = field(default_factory=list)
    founder_or_contact: str = ""
    email: Optional[str] = None
    apply_url: Optional[str] = None
    budget: Optional[str] = None
    raw_text: str = ""
    clean_text: str = ""
    pitch_draft: Optional[str] = None
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    posted_at: Optional[str] = None

    @property
    def hn_url(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"

    @property
    def mailto_url(self) -> Optional[str]:
        if not self.email:
            return None
        import urllib.parse
        subject = f"Application / Collaboration: {self.role or 'Software Engineer'} - Aman Kumar"
        body = self.pitch_draft or (
            f"Hi {self.founder_or_contact or 'Team'},\n\n"
            f"I saw your posting on Hacker News for {self.role or 'Software Engineer'} at {self.company or 'your team'}.\n"
            f"I am a Systems & Fullstack Engineer specializing in Go, Python, and FastAPI.\n"
            f"GitHub: https://github.com/aman179102\n\n"
            f"Best,\nAman Kumar"
        )
        return f"mailto:{self.email}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"


@dataclass
class DiscussionTopic:
    """Represents an Ask HN architecture discussion candidate."""

    story: HNStory
    category: str = "Architecture"
    relevance_score: float = 0.0
    matched_tags: list[str] = field(default_factory=list)
    draft_response: str = ""

    @property
    def hn_url(self) -> str:
        return self.story.hn_url


@dataclass
class ShowcaseProject:
    """Metadata and project architecture details for 'Show HN' posts."""

    slug: str
    title: str
    github_url: str
    summary: str
    tech_stack: list[str]
    architecture_points: list[str]
    tradeoffs: list[str]
    suggested_title: str
    body_markdown: str = ""

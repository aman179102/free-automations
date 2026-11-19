"""Hacker News Discussion Engine: Discovers rising Ask HN architecture questions
and drafts high-signal senior systems engineering responses.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from automations.hn.api import HNAPIClient
from automations.hn.models import DiscussionTopic, HNStory

try:
    from common.ai import generate_text
    from common.config import Settings
except ImportError:
    Settings = None  # type: ignore
    generate_text = None  # type: ignore

logger = logging.getLogger(__name__)

# Architecture & Systems Engineering Keywords
ARCHITECTURE_TOPICS = {
    "distributed_storage": {
        "keywords": ["storage", "s3", "blob", "distributed storage", "replication", "erasure coding", "object storage", "file system", "distributed file system"],
        "category": "Distributed Storage & Reliability",
        "key_tradeoffs": "Tradeoff between 3x replication (300% storage overhead) vs Reed-Solomon erasure coding (e.g., 4+2 gives 1.5x overhead tolerating 2 drive losses at the cost of CPU encode/decode reconstruction).",
    },
    "streaming_pipelines": {
        "keywords": ["streaming", "analytics pipeline", "kafka", "redis streams", "event-driven", "message queue", "pubsub", "pub/sub", "timescaledb", "time series"],
        "category": "Real-Time Streaming & High-Throughput Ingestion",
        "key_tradeoffs": "Decouple synchronous HTTP ingestion from persistence via memory-buffered append-only logs (Redis Streams/Kafka), adaptive batching into hypertables, and backpressure monitoring.",
    },
    "databases_io": {
        "keywords": ["database", "postgres", "postgresql", "lsm", "b-tree", "b+ tree", "write amplification", "compaction", "indexing", "sql vs nosql", "acid"],
        "category": "Databases & I/O Internals",
        "key_tradeoffs": "Sequential disk writes (LSM-trees) eliminate random disk writes for high ingest rates, paying the cost in background compaction and Bloom filter lookups versus B+ trees.",
    },
    "concurrency_runtime": {
        "keywords": ["concurrency", "goroutines", "asyncio", "threading", "threads", "worker pool", "gmp", "go runtime", "event loop", "parallelism", "multithreading"],
        "category": "Concurrency & Runtime Optimization",
        "key_tradeoffs": "User-space lightweight green threads (Go GMP work-stealing, starting at 2KB stacks) vs OS threads (kernel context switches and fixed 8MB stack allocation), and when bounded worker pools prevent CPU cache thrashing.",
    },
    "microservices_scaling": {
        "keywords": ["microservices", "monolith", "modular monolith", "scale", "scaling", "system design", "architecture", "distributed systems", "load balancer", "api gateway"],
        "category": "System Design & Scaling Trade-offs",
        "key_tradeoffs": "Boundaries should follow data ownership and failure domains rather than arbitrary feature splits. Avoid distributed transactions; favor eventual consistency with idempotency keys.",
    },
}

SYSTEMS_PROMPT = """You are a Principal Systems & Infrastructure Architect responding to an Ask HN question.
Your response must adhere to Hacker News engineering culture:
1. Deeply technical, practical, and grounded in production engineering reality (I/O patterns, memory allocations, network latencies, failure modes).
2. Explicitly analyze trade-offs (latency vs throughput, write amplification vs read penalty, operational simplicity vs scalability).
3. Grounded in concrete architectural patterns (e.g. LSM vs B-Trees, connection pooling, Kafka/Redis stream buffering, backpressure, chunk compression).
4. Humble, constructive, zero marketing jargon, zero buzzwords, highly readable in HN-style concise paragraphs.
"""


def score_discussion_candidate(story: HNStory) -> tuple[float, str, list[str]]:
    """
    Evaluates whether an HN story is an Ask HN architecture question and calculates
    a relevance score along with matched topic and tags.
    """
    title = story.title.lower()
    text = (story.clean_text or "").lower()
    full_text = f"{title} {text}"

    # Must be Ask HN or discussion query
    is_ask_hn = title.startswith("ask hn:") or title.startswith("ask hn") or story.type == "story"

    matched_tags: list[str] = []
    best_category = "General Architecture"
    max_matches = 0

    for topic_key, topic_data in ARCHITECTURE_TOPICS.items():
        matches = [kw for kw in topic_data["keywords"] if kw in full_text]
        if len(matches) > max_matches:
            max_matches = len(matches)
            best_category = topic_data["category"]
            matched_tags = matches

    if max_matches == 0:
        return 0.0, best_category, []

    # Score calculation based on matches, score, and discussion freshness
    score = float(max_matches * 15)
    if is_ask_hn:
        score += 25
    if story.score >= 5:
        score += min(story.score, 50)
    if 2 <= story.descendants <= 50:
        # Sweet spot: active discussion but not saturated with 500+ comments
        score += 20

    return score, best_category, matched_tags


def draft_architect_response(
    topic: DiscussionTopic,
    author_name: str = "Aman Kumar",
    settings: Optional[Any] = None,
) -> str:
    """
    Drafts an insightful senior systems engineering response.
    Uses AI if available; otherwise produces a well-structured technical analysis.
    """
    story = topic.story

    if settings and generate_text and getattr(settings, "has_ai", False):
        user_prompt = (
            f"Ask HN Title: {story.title}\n"
            f"Original Question / Context:\n{story.clean_text[:600]}\n\n"
            f"Domain Category: {topic.category}\n"
            f"Draft a senior engineering answer addressing the underlying trade-offs, practical failure modes, and architectural best practices."
        )
        ai_response = generate_text(settings, SYSTEMS_PROMPT, user_prompt, temperature=0.35)
        if ai_response and len(ai_response.strip()) > 80:
            return ai_response.strip()

    # Deterministic High-Signal Engineering Responses
    title = story.title
    cat = topic.category

    if "Distributed Storage" in cat:
        return (
            f"When evaluating storage architectures for this scale, the primary trade-off usually boils down to "
            f"durability versus storage expansion.\n\n"
            f"1. Replication vs Erasure Coding: Standard 3x replication is operationally simple with zero CPU decode overhead, "
            f"but imposes a 200% storage tax (3x raw disk footprint). Moving to Reed-Solomon erasure coding (such as 4 data + 2 parity shards) "
            f"cuts your storage overhead down to 1.5x while still tolerating 2 concurrent disk/node failures. The trade-off is CPU reconstructive "
            f"read penalty when a shard is degraded.\n\n"
            f"2. Metadata Separation: Keep the block/chunk storage decoupled from object metadata. Storing chunk hashes (SHA-256) in a relational "
            f"store like PostgreSQL gives you instant deduplication and transactional consistency for object manifests, while raw chunks stream directly to disk.\n\n"
            f"3. Failure Domains: Ensure your parity shards span independent failure domains (separate racks or availability zones) rather than just "
            f"different drives on the same machine."
        )

    if "Real-Time Streaming" in cat:
        return (
            f"A common bottleneck in high-throughput streaming architectures is tight coupling between ingest endpoints and persistence.\n\n"
            f"1. Decoupled Buffer: Put a low-latency append-only buffer (Redis Streams or Kafka) immediately behind your API layer. "
            f"The ingest worker simply writes to the stream buffer and returns HTTP 202/200 in sub-millisecond time, insulating your API from downstream database locks.\n\n"
            f"2. Adaptive Batching & Chunking: Rather than inserting row-by-row into your time-series store, consume events in micro-batches (e.g. 500-1000 items "
            f"or 50ms flush timeout). If you are using PostgreSQL with TimescaleDB, hypertable chunk compression can reduce disk footprint by 90%+ while keeping "
            f"recent chunks uncompressed in memory for fast writes.\n\n"
            f"3. Backpressure & Sampling: Always instrument consumer group lag. If consumer lag exceeds threshold under sudden traffic spikes, have an adaptive "
            f"rate-based sampler shed non-critical telemetry rather than letting memory queues saturate the worker pods."
        )

    if "Databases" in cat:
        return (
            f"From an infrastructure perspective, this comes down to I/O access patterns and write amplification.\n\n"
            f"1. LSM-Trees vs B+ Trees: B+ trees (used in standard PostgreSQL and MySQL InnoDB) are optimized for random read latency, but incur "
            f"random write I/O because dirty pages must be flushed across disk blocks. LSM-trees (RocksDB, Cassandra) convert random writes into sequential "
            f"log appends (MemTable -> SSTable), delivering massive write throughput at the expense of background compaction I/O and Bloom filter lookups.\n\n"
            f"2. Connection Pooling & Workload Splitting: Before re-architecting your schemas, inspect your connection saturation. A pooling layer like PgBouncer "
            f"in transaction pooling mode often triples effective throughput by avoiding PostgreSQL's process-per-connection fork overhead.\n\n"
            f"3. Indexing Hygiene: Every extra index on a write-heavy table linearly multiplies your write amplification. Audit unused indexes using pg_stat_user_indexes."
        )

    if "Concurrency" in cat:
        return (
            f"When scaling concurrent workloads, thread overhead and scheduling contention are the decisive factors:\n\n"
            f"1. OS Threads vs Lightweight Goroutines: Standard OS threads require an 8MB reserved stack and kernel context switching. "
            f"Go's GMP scheduler multiplexes thousands of user-space goroutines (starting at ~2KB dynamic stacks) across logical processors with work-stealing, "
            f"giving you high concurrency without memory exhaustion.\n\n"
            f"2. Worker Pool Sizing: Spawning unbounded goroutines or async tasks under load will eventually exhaust file descriptors or socket buffers. "
            f"A bounded worker pool or a buffered semaphore channel protects downstream services from saturation.\n\n"
            f"3. Backpressure: Ensure producers block or return HTTP 429 when processing queues fill up, rather than accumulating unbounded in-memory queues."
        )

    # General Systems Scaling
    return (
        f"In system design at this stage, the rule of thumb is to design for operational simplicity until data boundaries dictate splitting:\n\n"
        f"1. Monolith First with Clean Module Boundaries: Premature microservices introduce distributed tracing complexity, partial failure modes, "
        f"and the dual-write problem. Build a well-structured modular monolith with clear domain interfaces first.\n\n"
        f"2. Isolate Failure Domains: Split services only when their operational requirements diverge fundamentally (e.g. CPU-intensive background video processing "
        f"versus low-latency user auth, or distinct SLA/compliance requirements).\n\n"
        f"3. Idempotency & Observability: Make all state-changing endpoints idempotent with client-generated tokens, and propagate correlation IDs across all service calls."
    )


class DiscussionEngine:
    """Monitors Ask HN for architecture discussions and drafts senior systems responses."""

    def __init__(self, api_client: Optional[HNAPIClient] = None, settings: Optional[Any] = None):
        self.api = api_client or HNAPIClient()
        self.settings = settings
        self.author_name = "Aman Kumar"
        if self.settings:
            self.author_name = getattr(self.settings, "author_name", self.author_name)

    def find_architecture_discussions(self, limit: int = 10, search_fresh_hours: int = 48) -> list[DiscussionTopic]:
        """
        Discovers rising Ask HN architecture threads from Algolia and Firebase.
        """
        topics: list[DiscussionTopic] = []
        seen_ids = set()

        # 1. Search Algolia for Ask HN questions
        queries = ["Ask HN architecture", "Ask HN database", "Ask HN scaling", "Ask HN distributed", "Ask HN system design"]
        for q in queries:
            res = self.api.search_algolia_by_date(
                query=q,
                tags="story",
                hits_per_page=15,
            )
            for hit in res.get("hits", []):
                story = HNStory.from_algolia(hit)
                if story.id in seen_ids:
                    continue
                seen_ids.add(story.id)

                score, category, tags = score_discussion_candidate(story)
                if score >= 20:
                    topic = DiscussionTopic(
                        story=story,
                        category=category,
                        relevance_score=score,
                        matched_tags=tags,
                    )
                    topic.draft_response = draft_architect_response(
                        topic,
                        author_name=self.author_name,
                        settings=None,
                    )
                    topics.append(topic)

        # 2. Also check current Firebase Ask HN stories
        ask_ids = self.api.fetch_askstories(limit=25)
        for story_id in ask_ids:
            if story_id in seen_ids:
                continue
            seen_ids.add(story_id)
            story = self.api.get_story(story_id)
            if not story:
                continue

            score, category, tags = score_discussion_candidate(story)
            if score >= 20:
                topic = DiscussionTopic(
                    story=story,
                    category=category,
                    relevance_score=score,
                    matched_tags=tags,
                )
                topic.draft_response = draft_architect_response(
                    topic,
                    author_name=self.author_name,
                    settings=None,
                )
                topics.append(topic)

        # Sort topics by relevance score descending
        topics.sort(key=lambda t: t.relevance_score, reverse=True)
        return topics[:limit]

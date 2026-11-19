"""Hacker News 'Show HN' Showcase Manager: Formats and drafts comprehensive
high-signal Show HN submissions for Aman Kumar's production open-source systems.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from automations.hn.models import ShowcaseProject

try:
    from common.ai import generate_text
    from common.config import Settings
except ImportError:
    Settings = None  # type: ignore
    generate_text = None  # type: ignore

logger = logging.getLogger(__name__)

# Curated Project Details for Aman Kumar (@aman179102)
PROJECT_REGISTRY: dict[str, dict[str, Any]] = {
    "distributed-file-storage": {
        "slug": "distributed-file-storage",
        "title": "Distributed File Storage Service in Go",
        "github_url": "https://github.com/aman179102/distributed-file-storage-service",
        "suggested_title": "Show HN: Distributed File Storage in Go – S3-compatible with Reed-Solomon Erasure Coding",
        "summary": "An S3-compatible distributed object storage engine written in Go, featuring Reed-Solomon erasure coding (4+2), SHA-256 chunk deduplication, and PostgreSQL metadata indexing.",
        "tech_stack": ["Go", "Reed-Solomon Erasure Coding (4+2)", "PostgreSQL", "SHA-256 Dedup", "S3 REST API", "Prometheus"],
        "architecture_points": [
            "Reed-Solomon (4 data + 2 parity) erasure coding tolerating up to 2 simultaneous drive failures at 1.5x storage expansion versus 3.0x for standard replication.",
            "Content-addressable SHA-256 block chunking with automatic deduplication across identical files.",
            "Decoupled metadata layer in PostgreSQL providing ACID consistency for manifests while streaming binary payloads directly to disk.",
            "Native S3 REST API compliance supporting multipart uploads, chunk streaming, and range requests.",
        ],
        "tradeoffs": [
            "Storage Expansion vs CPU: 1.5x storage expansion saves 50% raw disk cost compared to 3x replication, but incurs CPU reconstruction overhead on degraded reads.",
            "Metadata Separation: Centralized PostgreSQL metadata allows fast SQL queries and dedup checks, but requires connection pooling (PgBouncer) under high concurrency.",
        ],
        "default_body": """Hey HN,

I built this S3-compatible distributed object storage engine in Go:
https://github.com/aman179102/distributed-file-storage-service

### Motivation
Standard object stores frequently default to 3x replication for durability. While conceptually simple, it imposes a 200% storage overhead tax. I wanted to build an engine that provides equivalent fault-tolerance with a fraction of the raw storage footprint, while maintaining standard S3 API compatibility.

### Architecture Highlights
- **Reed-Solomon Erasure Coding (4+2)**: Splits incoming objects into 4 data shards and 2 parity shards. Any 4 shards can reconstruct the original payload, tolerating 2 simultaneous disk or node failures while keeping storage expansion at only 1.5x (compared to 3.0x for replication).
- **Chunk-Level Deduplication**: Uses SHA-256 content hashes to identify identical chunks across different uploads, eliminating redundant disk I/O.
- **Decoupled Metadata**: Object keys, chunk manifests, and access policies live in PostgreSQL, ensuring ACID consistency while binary blocks stream asynchronously directly to disk.
- **S3 API Compatibility**: Implements standard multipart upload protocol, byte-range queries, and Prometheus observability metrics out of the box.

### Trade-offs & Lessons Learned
- *CPU vs Storage*: Reconstructing missing shards during degraded reads requires finite Galois Field arithmetic. On modern Go runtimes, SIMD-accelerated Reed-Solomon performs well, but raw sequential throughput during disk failure is bounded by CPU rather than disk bus.
- *Chunk Sizing*: Settled on 4MB chunks as the optimal equilibrium between deduplication granularity and PostgreSQL metadata table row growth.

Code and setup instructions are on GitHub: https://github.com/aman179102/distributed-file-storage-service

I would love to get your thoughts, architectural feedback, and critique on chunk sizing and recovery protocols!
""",
    },
    "real-time-analytics-pipeline": {
        "slug": "real-time-analytics-pipeline",
        "title": "Real-Time Streaming Analytics Pipeline",
        "github_url": "https://github.com/aman179102/real-time-analytics-pipeline",
        "suggested_title": "Show HN: Real-Time Analytics Pipeline – FastAPI, TimescaleDB, and Redis Streams",
        "summary": "An open-source streaming analytics pipeline built with FastAPI, TimescaleDB hypertables, Redis Streams / Kafka queue backends, and live WebSocket dashboards.",
        "tech_stack": ["Python", "FastAPI", "TimescaleDB", "Redis Streams", "Kafka", "WebSockets", "OpenTelemetry"],
        "architecture_points": [
            "Decoupled ingest layer accepting high-throughput event telemetry with sub-millisecond HTTP 202 responses.",
            "Append-only memory queue buffering via Redis Streams / Kafka with consumer group parallel processing.",
            "TimescaleDB hypertable chunk compression yielding 90%+ storage reduction on historical event partitions.",
            "Adaptive rate-based sampling protecting database nodes against sudden telemetry ingestion spikes.",
            "Live WebSocket server broadcasting real-time aggregated metrics with OpenTelemetry trace propagation.",
        ],
        "tradeoffs": [
            "Memory Buffer vs Ingest Latency: Buffering in Redis Streams protects PostgreSQL from connection pool exhaustion during traffic surges at the cost of eventual consistency on dashboard reads.",
            "Compression vs Query Performance: Compressing historical Timescale chunks reduces disk footprint drastically, but uncompressed recent chunks are prioritized for sub-10ms operational queries.",
        ],
        "default_body": """Hey HN,

I open-sourced a real-time streaming analytics engine built for high-throughput event telemetry:
https://github.com/aman179102/real-time-analytics-pipeline

### The Problem
Small to mid-scale engineering teams often need real-time event analytics and live monitoring, but end up having to choose between heavyweight complex infrastructure (Flink + Kafka + ClickHouse clusters) or expensive third-party SaaS tools that bill per event.

I wanted a modular, open-source stack that runs lean on a single server or small cluster while handling thousands of events per second with sub-millisecond ingest latency.

### How it Works
1. **Async Ingest API (FastAPI)**: Validates incoming event payloads with Pydantic and instantly appends them to Redis Streams / Kafka, returning an HTTP 202 response.
2. **Batch Processing Workers**: Consumer groups pull batches from the stream and execute bulk UPSERTs into TimescaleDB hypertables.
3. **Hypertable Chunk Compression**: Automatically compresses time partitions older than 24 hours, shrinking storage footprint by over 90%.
4. **Adaptive Rate-Based Sampling**: Under unexpected traffic surges, the worker dynamically downsamples repetitive telemetry to prevent memory queue saturation.
5. **Live Push (WebSockets)**: Streams live aggregated metrics directly to frontend dashboards with OpenTelemetry trace correlation.

The repository is open-source under MIT: https://github.com/aman179102/real-time-analytics-pipeline

Feedback, benchmarks, and pull requests are warmly welcomed!
""",
    },
    "podvoice": {
        "slug": "podvoice",
        "title": "PodVoice: Local-First Multi-Speaker AI Audio Engine",
        "github_url": "https://github.com/aman179102/podvoice",
        "suggested_title": "Show HN: PodVoice – Local-first multi-speaker AI podcast studio from Markdown",
        "summary": "An open-source, local-first studio that converts Markdown conversation scripts into multi-speaker podcast audio completely offline using Coqui XTTS v2 and FastAPI.",
        "tech_stack": ["Python", "FastAPI", "Coqui XTTS v2", "PyTorch", "FFmpeg", "Markdown"],
        "architecture_points": [
            "100% offline text-to-speech running locally on CPU or CUDA GPU with zero cloud API keys or external server calls.",
            "Multi-speaker voice synthesis parsed deterministically from standard dialogue Markdown syntax.",
            "FastAPI Studio UI for script editing, audio waveform visualization, and voice sample management.",
            "Single-pass audio stitching and normalization using FFmpeg with automatic silence trimming.",
        ],
        "tradeoffs": [
            "Local Hardware vs Cloud Speed: Synthesizing speech locally requires CUDA GPU or modern multi-core CPU, but eliminates cloud SaaS fees and protects proprietary voice scripts.",
            "Script Parsing: Deterministic Markdown speaker tags allow seamless scriptwriting in standard text editors without complex timeline GUI overhead.",
        ],
        "default_body": """Hey HN,

I built PodVoice, a local-first studio that turns Markdown scripts into multi-speaker podcast audio completely offline:
https://github.com/aman179102/podvoice

### Why Local-First?
Most modern AI speech platforms require cloud subscriptions, upload your voice clones and audio scripts to third-party servers, and charge per generated character. 

For technical tutorials, private research, or automated audio briefings, having an engine that runs 100% locally on your machine ensures zero data leakage, zero API bills, and total creative independence.

### Key Capabilities
- **Multi-Speaker Markdown**: Write natural dialogues with simple tags (e.g., `[Host]: ...`, `[Guest]: ...`).
- **Offline Voice Cloning**: Uses Coqui XTTS v2 locally to synthesize expressive voices from a short 5-10 second reference audio WAV.
- **FastAPI Studio Interface**: Interactive web UI to edit scripts, preview voice assignments, and render final stitched MP3/WAV files.
- **Audio Pipeline**: Automatic loudness normalization, pause insertion, and silence trimming powered by FFmpeg.

Repo and setup guide: https://github.com/aman179102/podvoice

Looking forward to your questions and hearing what you create with it!
""",
    },
}


class ShowcaseManager:
    """Manages project showcases and drafts tailored Show HN posts."""

    def __init__(self, settings: Optional[Any] = None):
        self.settings = settings

    def list_projects(self) -> list[ShowcaseProject]:
        """Returns all registered showcase projects."""
        projects = []
        for slug, data in PROJECT_REGISTRY.items():
            projects.append(
                ShowcaseProject(
                    slug=slug,
                    title=data["title"],
                    github_url=data["github_url"],
                    summary=data["summary"],
                    tech_stack=data["tech_stack"],
                    architecture_points=data["architecture_points"],
                    tradeoffs=data["tradeoffs"],
                    suggested_title=data["suggested_title"],
                    body_markdown=data["default_body"],
                )
            )
        return projects

    def get_project(self, slug_or_name: str) -> Optional[ShowcaseProject]:
        """Looks up a project by slug or partial title."""
        key = slug_or_name.lower().strip()
        for slug, data in PROJECT_REGISTRY.items():
            if key in slug or key in data["title"].lower():
                return ShowcaseProject(
                    slug=slug,
                    title=data["title"],
                    github_url=data["github_url"],
                    summary=data["summary"],
                    tech_stack=data["tech_stack"],
                    architecture_points=data["architecture_points"],
                    tradeoffs=data["tradeoffs"],
                    suggested_title=data["suggested_title"],
                    body_markdown=data["default_body"],
                )
        return None

    def draft_show_hn(
        self,
        slug_or_name: str,
        custom_angle: Optional[str] = None,
        settings: Optional[Any] = None,
    ) -> Optional[ShowcaseProject]:
        """
        Drafts a tailored Show HN post for a specified project.
        Uses AI if requested and available; otherwise returns the curated Show HN post.
        """
        project = self.get_project(slug_or_name)
        if not project:
            logger.warning("Project '%s' not found in registry.", slug_or_name)
            return None

        cfg = settings or self.settings
        if cfg and generate_text and getattr(cfg, "has_ai", False) and custom_angle:
            system_prompt = (
                "You are an expert technical writer drafting a Show HN submission for Hacker News.\n"
                "Follow strict HN etiquette: direct, technical, humble, transparent about trade-offs, zero hype, zero buzzwords.\n"
                "Format with clear Markdown headers: Motivation, Architecture, Trade-offs, and GitHub Repo link."
            )
            user_prompt = (
                f"Project: {project.title}\n"
                f"GitHub: {project.github_url}\n"
                f"Summary: {project.summary}\n"
                f"Tech: {', '.join(project.tech_stack)}\n"
                f"Architecture Highlights:\n" + "\n".join(f"- {p}" for p in project.architecture_points) + "\n\n"
                f"Trade-offs:\n" + "\n".join(f"- {t}" for t in project.tradeoffs) + "\n\n"
                f"Custom Focus Angle: {custom_angle}\n\n"
                f"Draft the Show HN post body:"
            )
            ai_body = generate_text(cfg, system_prompt, user_prompt, temperature=0.3)
            if ai_body and len(ai_body.strip()) > 100:
                project.body_markdown = ai_body.strip()

        return project

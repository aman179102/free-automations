from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from common.ai import generate_text
from common.config import Settings


@dataclass(frozen=True)
class TumblrPostCandidate:
    title: str
    body: str
    tags: list[str]
    category: str
    is_promo: bool = False


# Curated, high-signal technical topics with clean ASCII diagrams and architecture breakdowns
SYSTEMS_TOPIC_TEMPLATES = [
    {
        "category": "database_internals",
        "title": "Why SQLite in WAL Mode is Ludicrously Fast (Systems Breakdown)",
        "body": """### The Hidden Architecture of SQLite's Write-Ahead Log

Most developers treat SQLite as a lightweight toy database. In production, when switched to **WAL (Write-Ahead Logging)** mode, it rivals dedicated servers for read-heavy local workloads.

Here is what happens under the hood:

```
[ Traditional Rollback Journal ]
Writer Locks Entire DB  -->  Writes Page Copy to Journal  -->  Writes to DB  -->  Deletes Journal
(Readers are BLOCKED during writes)

[ Write-Ahead Log (WAL) Mode ]
Writer Appends to WAL File  -------------------->  DB File Untouched!
Readers read DB + WAL Index (SHM Shared Memory) ->  ZERO LOCKING!
```

#### Why it changes everything:
1. **Readers never block writers**, and **writers never block readers**.
2. Sequential I/O: Appending new database pages to `db-wal` is sequential disk I/O, which modern NVMe drives process at multiple GB/s.
3. Checkpointing: Background workers merge WAL frames into the primary `.db` file in bulk, amortizing sync costs.

*Key Takeaway:* Before spinning up an external database cluster for edge or single-node microservices, benchmark SQLite with `PRAGMA journal_mode=WAL;` and `PRAGMA synchronous=NORMAL;`.
""",
        "tags": ["software engineering", "databases", "sqlite", "backend architecture", "systems design", "programming"],
    },
    {
        "category": "concurrency_patterns",
        "title": "Preventing the Thundering Herd Problem in High-Concurrency Caches",
        "body": r"""### The Cache Stampede (Thundering Herd) Anatomy

Picture this: A high-traffic API key in Redis expires at peak load (say 20,000 requests/sec). 
Within 5 milliseconds, 1,000 concurrent threads discover a cache miss and all query your primary PostgreSQL database simultaneously.

Your connection pool saturates. Latency spikes from 4ms to 8,000ms. The database crashes.

```
[ Cache Stampede ]
Requests: R1, R2, R3, ..., R1000  (All hit cache miss simultaneously)
               |
               v
      [ Database Overwhelmed ]  <-- 1000 identical heavy queries!
```

#### 3 Production-Tested Solutions:

1. **Mutex / Single-Flight Locking:**
   When a thread discovers a cache miss, it acquires a lightweight distributed mutex (`SETNX key_lock`). Only *one* thread queries the database and repopulates the cache; the other 999 wait and read from cache.

2. **Probabilistic Early Expiration (XFetch Algorithm):**
   Instead of waiting for `TTL = 0`, compute an early refresh dynamically:
   $$\Delta \times \beta \times \ln(\text{rand}()) > \text{remaining\_ttl}$$
   As the key nears expiration, the probability that a background reader refreshes the cache asynchronously approaches 100%.

3. **Background Worker Asynchronous Refresh:**
   Keep the key in cache indefinitely with an embedded expiration timestamp. When expired, return stale data immediately (zero latency impact) while dispatching an async task to refresh.

*Architecture Rule:* Never let your database be the fallback for synchronous cache invalidations under burst traffic.
""",
        "tags": ["distributed systems", "redis", "concurrency", "backend", "web development", "computer science"],
    },
    {
        "category": "linux_kernel_io",
        "title": "epoll vs io_uring: How the Linux Kernel Revolutionized Asynchronous I/O",
        "body": """### From select() to io_uring: 30 Years of Linux I/O

For two decades, `epoll` has powered the backbone of the internet — Nginx, Node.js (libuv), Redis, and Envoy. But `epoll` has a fundamental bottleneck: **Context Switches**.

```
[ epoll Architecture ]
User Space                    Kernel Space
    |                              |
    |---- epoll_wait() sys-call -->| (Context Switch overhead)
    |<--- Ready file descriptors --|
    |                              |
    |---- read() / write() -------->| (Another Sys-call & Context Switch)
```

Every single buffer read or poll requires crossing the user/kernel space boundary. At 1,000,000 IOPS, syscall overhead alone consumes up to 30% of your CPU cycles.

#### Enter io_uring (Linux 5.1+):
`io_uring` replaces syscall ping-pong with **two lock-free ring buffers** in shared memory (`mmap`):

```
[ Submission Queue (SQ) ]  -->  Application enqueues I/O requests without syscalls
[ Completion Queue (CQ) ]  <--  Kernel posts completed results asynchronously
```

- **True Asynchronous Disk I/O:** Unlike epoll (which only supports sockets, not regular files), io_uring works with disk files, network sockets, and pipes.
- **Polling Mode (SQPOLL):** A dedicated kernel thread continuously polls the submission queue. The application can submit and complete millions of I/O operations with **ZERO system calls**.

*Future Outlook:* High-performance storage engines and web gateways in Go and Rust are moving to `io_uring` for sub-microsecond tail latencies.
""",
        "tags": ["linux", "kernel", "systems programming", "c", "rustlang", "low level", "performance"],
    },
    {
        "category": "memory_architecture",
        "title": "Struct Alignment & CPU Cache Line False Sharing Explained",
        "body": """### Why Struct Field Order Changes Your Memory Footprint

Modern CPUs don't read bytes from RAM individually. They read in **64-byte Cache Lines**. 

Due to hardware alignment rules, CPU registers fetch data much faster when memory addresses are multiples of the variable's byte size. If you don't order your struct fields properly, the compiler will silently inject invisible padding bytes.

```c
// ❌ Naive Ordering: 24 Bytes (8 bytes wasted padding!)
struct BadUser {
    bool is_active;     // 1 byte  + [7 bytes invisible padding]
    int64_t user_id;    // 8 bytes
    bool is_admin;      // 1 byte  + [7 bytes invisible padding]
};

// ✅ Optimized by Size: 16 Bytes (33% memory reduction!)
struct OptimizedUser {
    int64_t user_id;    // 8 bytes
    bool is_active;     // 1 byte
    bool is_admin;      // 1 byte  + [6 bytes padding to 8-byte boundary]
};
```

#### What happens at scale:
In an in-memory cache holding 100,000,000 records:
- `BadUser` consumes **2.4 GB** RAM.
- `OptimizedUser` consumes **1.6 GB** RAM.
- **800 MB saved** simply by sorting fields from largest to smallest!

*Bonus (False Sharing):* In multi-threaded programs, if two threads modify adjacent variables that reside on the *same 64-byte cache line*, the CPU core invalidates the entire cache line across all cores, cratering multi-core throughput. Use cache-line padding (`alignas(64)`) for thread-hot variables.
""",
        "tags": ["programming", "c++", "golang", "memory", "software engineering", "coding tips"],
    },
    {
        "category": "api_design",
        "title": "Idempotency Keys: Building Bulletproof Payment & State APIs",
        "body": """### How Distributed APIs Never Double-Charge a Customer

Network requests in distributed systems are inherently unreliable:
1. Client sends payment request.
2. Server charges customer successfully.
3. TCP connection drops or timeout occurs before client receives HTTP 200 OK.
4. Client retries the request.

Without an **Idempotency Key**, the customer is charged twice.

```
Client                                      Server / Gateway
  |                                                |
  |--- POST /pay (Idempotency-Key: abc-123) ------>| Check key in Redis/DB:
  |                                                | -> NOT found: Lock key
  |                                                | -> Execute payment
  |                                                | -> Save HTTP 200 response in DB
  |<-- [Network Drops! Client gets timeout] -------|
  |                                                |
  |--- RETRY POST /pay (Same Key: abc-123) -------->| Check key:
  |                                                | -> FOUND! Return cached response
  |<-- HTTP 200 OK (Payment Verified) -------------| (NO SECOND TRANSACTION)
```

#### Production Checklist:
- **Scope:** Store idempotency keys with a 24-hour TTL in Redis or an ACID database table.
- **In-Flight Locking:** If a retry arrives *while* the first request is still processing, return `409 Conflict` or wait on a distributed lock.
- **Request Hash Verification:** Verify that the request body matches the original payload for that key to prevent replay attacks with mismatched data.
""",
        "tags": ["api design", "backend", "web development", "architecture", "microservices", "fintech"],
    },
]


def generate_tumblr_post(settings: Settings, category: str = "random") -> TumblrPostCandidate:
    """
    Generates a high-signal, educational tech post for Tumblr.
    99.9% of posts contain zero links, establishing high organic authority.
    """
    # Pick a systems engineering topic
    if category != "random":
        matched = [t for t in SYSTEMS_TOPIC_TEMPLATES if t["category"] == category]
        template = matched[0] if matched else random.choice(SYSTEMS_TOPIC_TEMPLATES)
    else:
        template = random.choice(SYSTEMS_TOPIC_TEMPLATES)

    # If AI is configured, optionally synthesize fresh variation, else use verified high-signal template
    if settings.has_ai:
        prompt = (
            f"Topic: {template['title']}\n\n"
            f"Requirements:\n"
            f"- Write an authoritative, engaging markdown technical breakdown.\n"
            f"- Include a clean ASCII diagram illustrating the internal architecture or workflow.\n"
            f"- Detail the real-world trade-offs, edge cases, and production rules.\n"
            f"- Tone: Senior, clear, concise, visually appealing for developers.\n"
            f"- IMPORTANT: DO NOT include any marketing links, social promotion, or calls to action. Pure technical insight only.\n"
            f"- Length: 250-450 words.\n"
        )
        ai_body = generate_text(
            settings=settings,
            system="You are a Staff Systems & Software Architect writing an aesthetic technical post for a software engineering blog.",
            user=prompt,
            temperature=0.7,
        )
        if ai_body and len(ai_body.strip()) > 150:
            return TumblrPostCandidate(
                title=template["title"],
                body=ai_body.strip(),
                tags=template["tags"],
                category=template["category"],
                is_promo=False,
            )

    return TumblrPostCandidate(
        title=template["title"],
        body=template["body"].strip(),
        tags=template["tags"],
        category=template["category"],
        is_promo=False,
    )

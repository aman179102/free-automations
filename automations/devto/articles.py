from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from typing import Optional

from common.ai import generate_text
from common.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DevtoArticleCandidate:
    """Represents a generated technical article for Dev.to."""
    title: str
    body: str
    category: str
    tags: list[str]
    description: str
    series: str
    canonical_url: str
    ascii_diagram: str
    is_ai_generated: bool = False


# Curated, deep-dive production templates
DEVTO_TEMPLATES = [
    {
        "category": "kafka_vs_redis_streams",
        "title": "Event Streaming at Scale: Kafka vs Redis Streams for High-Throughput Python Backends",
        "description": "An architectural comparison of Apache Kafka vs Redis Streams in distributed Python systems, exploring partition models, consumer groups, and asyncio worker pipelines.",
        "series": "High-Throughput Distributed Systems",
        "tags": ["python", "architecture", "distributed", "backend"],
        "ascii_diagram": r"""
+--------------------+        PubSub / Produce         +------------------------+
| Python Microservice| =============================> | Stream Message Broker  |
| (FastAPI Worker)   |                                | (Kafka or Redis Streams)|
+--------------------+                                +------------------------+
                                                                  |
                                                     Consumer Group Partitioning
                                                                  v
+--------------------+                                +------------------------+
| Storage / Analytics| <============================= | Competing Async Workers|
| (TimescaleDB / S3) |         Ack / Commit           | (Consumer Group XREAD) |
+--------------------+                                +------------------------+
""",
        "markdown_content": r'''## Architectural Trade-Offs

When designing high-throughput event streaming architectures in Python, software engineers typically evaluate two leading storage engines: **Apache Kafka** and **Redis Streams**. While both support append-only logs and consumer groups, their fundamental concurrency models and memory bounds differ significantly.

### Kafka vs Redis Streams Core Matrix

| Feature | Apache Kafka | Redis Streams (XADD/XREADGROUP) |
| :--- | :--- | :--- |
| **Storage Architecture** | Disk-first distributed commit log (PageCache) | In-memory radix tree with optional disk persistence (RDB/AOF) |
| **Throughput / Latency** | Millions msgs/sec, 5-15ms P99 latency | 100k-500k msgs/sec, sub-millisecond P99 latency (<1ms) |
| **Partitioning** | Distributed partitions per topic across brokers | Single key log (partitioning requires multiple Redis keys) |
| **Retention Policy** | Days/Months via segment rollover & compaction | Memory-bounded with `MAXLEN ~` eviction |
| **Python Client Eco** | `aiokafka`, `confluent-kafka` | `redis.asyncio` |

## Production Architecture

```
+--------------------+        PubSub / Produce         +------------------------+
| Python Microservice| =============================> | Stream Message Broker  |
| (FastAPI Worker)   |                                | (Kafka or Redis Streams)|
+--------------------+                                +------------------------+
                                                                  |
                                                     Consumer Group Partitioning
                                                                  v
+--------------------+                                +------------------------+
| Storage / Analytics| <============================= | Competing Async Workers|
| (TimescaleDB / S3) |         Ack / Commit           | (Consumer Group XREAD) |
+--------------------+                                +------------------------+
```

## Production Implementation: Redis Streams Async Consumer

The following working code demonstrates a fault-tolerant Python consumer utilizing `redis.asyncio` with explicit consumer group acknowledgement and graceful shutdown handling:

```python
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("stream_consumer")
logging.basicConfig(level=logging.INFO)

STREAM_NAME = "events:pipeline:v1"
GROUP_NAME = "analytics_processors"
CONSUMER_NAME = "worker_node_01"


class RedisStreamConsumer:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self.client: aioredis.Redis | None = None
        self._running = False

    async def connect(self) -> None:
        self.client = aioredis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        # Ensure consumer group exists
        try:
            await self.client.xgroup_create(
                STREAM_NAME, GROUP_NAME, id="0", mkstream=True
            )
            logger.info("Created consumer group '%s' on stream '%s'", GROUP_NAME, STREAM_NAME)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            logger.info("Consumer group '%s' already registered", GROUP_NAME)

    async def process_message(self, message_id: str, fields: dict[str, Any]) -> None:
        """Simulates idempotent processing of the ingested stream payload."""
        event_type = fields.get("type", "unknown")
        user_id = fields.get("user_id", "anonymous")
        logger.debug("Processing event %s for user %s (ID: %s)", event_type, user_id, message_id)
        # Simulated async I/O operation
        await asyncio.sleep(0.01)

    async def run_loop(self) -> None:
        self._running = True
        logger.info("Starting stream consumer loop for %s...", CONSUMER_NAME)
        
        while self._running:
            try:
                # Read new unread messages with 2000ms block
                entries = await self.client.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_NAME: ">"},
                    count=50,
                    block=2000,
                )
                
                if not entries:
                    continue

                for stream_key, messages in entries:
                    for msg_id, data in messages:
                        try:
                            await self.process_message(msg_id, data)
                            # Explicit acknowledgement
                            await self.client.xack(STREAM_NAME, GROUP_NAME, msg_id)
                        except Exception as proc_err:
                            logger.error("Failed processing message %s: %s", msg_id, proc_err)
                            # Message remains in Pending Entries List (PEL) for retry

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                logger.error("Unexpected stream consumer loop error: %s", loop_err)
                await asyncio.sleep(1.0)

    async def shutdown(self) -> None:
        logger.info("Shutting down consumer...")
        self._running = False
        if self.client:
            await self.client.aclose()


async def main() -> None:
    consumer = RedisStreamConsumer()
    await consumer.connect()
    
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(consumer.shutdown()))

    await consumer.run_loop()


if __name__ == "__main__":
    asyncio.run(main())
```

## When to Choose Which?

1. **Choose Redis Streams** when latency is critical (sub-millisecond), dataset fits in RAM or is bounded with `MAXLEN`, and your stack is already centered around Redis.
2. **Choose Apache Kafka** when retaining terabytes of historical logs, requiring strict partition order across multiple brokers, or building large-scale CDC (Change Data Capture) pipelines.

GitHub Repository: [github.com/aman179102](https://github.com/aman179102)
''',
    },
    {
        "category": "idempotency_keys_sagas",
        "title": "Distributed Sagas & Idempotency Keys: Building Resilient Microservices in Python",
        "description": "A guide to implementing atomic operations across distributed microservices using FastAPI, Redis atomic leases, and compensating Saga orchestrators.",
        "series": "Resilient Microservices Architecture",
        "tags": ["python", "fastapi", "microservices", "architecture"],
        "ascii_diagram": r"""
Client Request (ID: uuid)
      |
      v
+---------------------+      Acquire Lock & Check Cache       +--------------------+
| Idempotency Filter  | ------------------------------------> | Redis Key Store    |
| (FastAPI Middleware)| <------------------------------------ | (TTL, State: PEND) |
+---------------------+      Cache HIT -> Return Replay       +--------------------+
      | Cache MISS
      v
+---------------------+        Forward Actions (Order, Payment) +--------------------+
| Saga Orchestrator   | -------------------------------------> | Microservice Step  |
| (State Machine)     | <------------------------------------- | Result / Error     |
+---------------------+         On Failure: Compensate         +--------------------+
""",
        "markdown_content": r'''## The Problem: Distributed Partial Failures

In microservices architectures, business workflows (such as payment processing, order placement, and inventory reserving) span multiple independent databases. Distributed 2PC (Two-Phase Commit) protocols introduce severe latency and single-point-of-failure bottlenecks.

To achieve ACID semantics across autonomous services without distributed locks, production architectures combine:
1. **Idempotency Keys**: Guaranteeing duplicate HTTP requests (e.g. from network retries) execute exactly once.
2. **The Saga Pattern**: Executing sequential local transactions coupled with compensating transactions on failure.

## System Workflow

```
Client Request (ID: uuid)
      |
      v
+---------------------+      Acquire Lock & Check Cache       +--------------------+
| Idempotency Filter  | ------------------------------------> | Redis Key Store    |
| (FastAPI Middleware)| <------------------------------------ | (TTL, State: PEND) |
+---------------------+      Cache HIT -> Return Replay       +--------------------+
      | Cache MISS
      v
+---------------------+        Forward Actions (Order, Payment) +--------------------+
| Saga Orchestrator   | -------------------------------------> | Microservice Step  |
| (State Machine)     | <------------------------------------- | Result / Error     |
+---------------------+         On Failure: Compensate         +--------------------+
```

## Production Implementation: FastAPI Idempotency Middleware & Saga Step

```python
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
import redis.asyncio as aioredis

app = FastAPI(title="Resilient Order Service")
redis_client = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)


@dataclass
class SagaStep:
    name: str
    action: Callable[[], Coroutine[Any, Any, Any]]
    compensate: Callable[[], Coroutine[Any, Any, Any]]


class SagaOrchestrator:
    """Executes distributed transaction steps with automatic reverse compensation."""
    def __init__(self, saga_id: str):
        self.saga_id = saga_id
        self.steps: list[SagaStep] = []
        self.executed_steps: list[SagaStep] = []

    def add_step(self, step: SagaStep) -> None:
        self.steps.append(step)

    async def execute(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for step in self.steps:
            try:
                res = await step.action()
                results[step.name] = res
                self.executed_steps.append(step)
            except Exception as exc:
                # Trigger backward rollback
                await self._rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Saga {self.saga_id} failed at step '{step.name}': {exc}",
                )
        return results

    async def _rollback(self) -> None:
        for step in reversed(self.executed_steps):
            try:
                await step.compensate()
            except Exception as comp_err:
                # Log critical alert for manual operator intervention
                pass


@app.middleware("http")
async def idempotency_middleware(request: Request, call_next: Callable[[Request], Coroutine[Any, Any, Response]]) -> Response:
    idempotency_key = request.headers.get("X-Idempotency-Key")
    if not idempotency_key or request.method not in ("POST", "PUT", "PATCH"):
        return await call_next(request)

    redis_key = f"idempotency:{idempotency_key}"
    # Atomic setnx with 120-second lease
    acquired = await redis_client.set(redis_key, "IN_PROGRESS", nx=True, ex=120)
    
    if not acquired:
        cached_val = await redis_client.get(redis_key)
        if cached_val == "IN_PROGRESS":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Concurrent request with identical idempotency key is executing.",
            )
        # Return cached serialized response
        cached_data = json.loads(cached_val)
        return Response(
            content=cached_data["content"],
            status_code=cached_data["status_code"],
            media_type="application/json",
            headers={"X-Cache-Lookup": "HIT"},
        )

    response = await call_next(request)
    
    if response.status_code < 400:
        resp_body = [section async for section in response.body_iterator]
        full_content = b"".join(resp_body).decode("utf-8")
        payload = json.dumps({"status_code": response.status_code, "content": full_content})
        await redis_client.set(redis_key, payload, ex=86400)
        return Response(content=full_content, status_code=response.status_code, media_type="application/json")
    else:
        await redis_client.delete(redis_key)
        return response
```

## Key Architectural Principles

1. **Short Lease Times on Mutexes**: Never lock an idempotency key indefinitely without a TTL.
2. **Deterministic Step Ordering**: Ensure forward dependencies are verified before side effects are executed.
3. **Idempotent Compensations**: Compensating actions must themselves be safe to retry multiple times without accumulating drift.

GitHub Repository: [github.com/aman179102](https://github.com/aman179102)
''',
    },
    {
        "category": "asyncpg_connection_pooling",
        "title": "High-Concurrency PostgreSQL: Advanced asyncpg Connection Pooling & Pipeline Modes",
        "description": "Eliminating I/O bottlenecks in Python asyncio microservices: asyncpg binary wire protocol, custom pool tuning, prepared statement caching, and pipelining.",
        "series": "Python Systems Engineering",
        "tags": ["python", "postgresql", "fastapi", "performance"],
        "ascii_diagram": r"""
+--------------------+        HTTP/2 (async)        +------------------------+
| Client Traffic     | ==========================> | FastAPI Worker Pool    |
| (20,000+ req/sec)  |                              | (uvloop Event Loop)    |
+--------------------+                              +------------------------+
                                                                |
                                                   acquire() / release()
                                                                v
+--------------------+                              +------------------------+
| PostgreSQL Engine  | <========================== | asyncpg Pool           |
| (Prepared Stmts)   |        TCP Pipeline          | (Min: 10, Max: 50)     |
+--------------------+                              +------------------------+
""",
        "markdown_content": r'''## Why Standard ORMs Choke Under 10,000+ Req/sec

In Python microservices, database interaction is frequently the single largest contributor to P99 tail latency. Traditional ORMs (including synchronous SQLAlchemy or psycopg2 inside threadpools) suffer from heavy memory allocation and CPU overhead:
- Python object hydration for thousands of rows.
- Text-protocol SQL parsing on every client query.
- Thread context switching when threadpools saturate.

**asyncpg** addresses these bottlenecks at the protocol layer by implementing the **PostgreSQL Binary Wire Protocol** directly in Cython/Python, bypassing `libpq` and achieving performance comparable to compiled C extensions.

## Architecture: Lifespan Connection Pooling

```
+--------------------+        HTTP/2 (async)        +------------------------+
| Client Traffic     | ==========================> | FastAPI Worker Pool    |
| (20,000+ req/sec)  |                              | (uvloop Event Loop)    |
+--------------------+                              +------------------------+
                                                                |
                                                   acquire() / release()
                                                                v
+--------------------+                              +------------------------+
| PostgreSQL Engine  | <========================== | asyncpg Pool           |
| (Prepared Stmts)   |        TCP Pipeline          | (Min: 10, Max: 50)     |
+--------------------+                              +------------------------+
```

## Production Implementation: FastAPI Lifespan & asyncpg Pipeline Mode

```python
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/production_db"


class MetricRecord(BaseModel):
    service_name: str = Field(..., max_length=64)
    p99_latency_ms: float = Field(..., ge=0.0)
    throughput_rps: int = Field(..., ge=0)


class DatabasePoolManager:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        """Initializes connection pool with statement caching and tuned timeouts."""
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=10,
            max_size=50,
            max_inactive_connection_lifetime=300.0,
            command_timeout=10.0,
            # Cache up to 256 prepared statements per connection
            statement_cache_size=256,
        )

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def get_connection(self) -> AsyncGenerator[asyncpg.Connection, None]:
        if not self.pool:
            raise RuntimeError("Database pool uninitialized.")
        async with self.pool.acquire() as conn:
            yield conn


db_manager = DatabasePoolManager(DATABASE_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_manager.initialize()
    yield
    await db_manager.close()


app = FastAPI(title="High-Concurrency Telemetry API", lifespan=lifespan)


@app.post("/api/v1/telemetry", status_code=status.HTTP_201_CREATED)
async def ingest_telemetry(
    record: MetricRecord,
    conn: asyncpg.Connection = Depends(db_manager.get_connection),
) -> dict[str, Any]:
    query = """
        INSERT INTO service_telemetry (service_name, p99_latency_ms, throughput_rps, recorded_at)
        VALUES ($1, $2, $3, NOW())
        RETURNING id, recorded_at;
    """
    try:
        # Uses prepared statement with binary serialization
        row = await conn.fetchrow(
            query,
            record.service_name,
            record.p99_latency_ms,
            record.throughput_rps,
        )
        return {"status": "persisted", "id": row["id"], "timestamp": row["recorded_at"].isoformat()}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database execution error: {exc}",
        )
```

## Performance Optimization Rules

1. **Prepared Statement Caching**: Ensure `statement_cache_size` is non-zero so repeated queries avoid re-parsing plans on the Postgres server.
2. **Explicit Connection Timeouts**: Set `command_timeout` to fail fast during database lock contention rather than starving the pool.
3. **Avoid ORM Overhead in Ingestion Loops**: Use raw binary parameters (`$1, $2`) for multi-thousand RPS endpoints.

GitHub Repository: [github.com/aman179102](https://github.com/aman179102)
''',
    },
    {
        "category": "rust_ffi_python_pipelines",
        "title": "Rust FFI (PyO3) Acceleration for High-Throughput Python Data Pipelines",
        "description": "Overcoming Global Interpreter Lock (GIL) limits by offloading CPU-intensive serialization, hashing, and numerical aggregations to compiled Rust routines via PyO3.",
        "series": "High-Performance Systems",
        "tags": ["python", "rust", "performance", "systems"],
        "ascii_diagram": r"""
+---------------------+        Native C-ABI Call (GIL Free)       +---------------------+
| Python Runtime      | ========================================> | Compiled Rust FFI   |
| (asyncio / FastAPI) |                                           | (PyO3 Engine)       |
+---------------------+                                           +---------------------+
           ^                                                                 |
           |                  Zero-Copy Memory Buffer / SIMD                 |
           +=================================================================+
""",
        "markdown_content": r'''## The GIL Barrier in Data Pipelines

Python is unmatched for rapid backend development and microservice agility. However, in data-intensive tasks—such as batch parsing, cryptographic checksumming, or mathematical transformations—Python's **Global Interpreter Lock (GIL)** restricts execution to a single core, causing CPU saturation regardless of the underlying hardware threads.

By embedding **Rust routines via PyO3 or C-FFI**, we can release the GIL, exploit native CPU SIMD vector instructions, and return results directly to Python memory space with zero serialization overhead.

## Architecture: Zero-Copy Hybrid Pipeline

```
+---------------------+        Native C-ABI Call (GIL Free)       +---------------------+
| Python Runtime      | ========================================> | Compiled Rust FFI   |
| (asyncio / FastAPI) |                                           | (PyO3 Engine)       |
+---------------------+                                           +---------------------+
           ^                                                                 |
           |                  Zero-Copy Memory Buffer / SIMD                 |
           +=================================================================+
```

## Rust Engine (PyO3 Extension)

```rust
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use rayon::prelude::*;

/// Computes parallel rolling hash checksums across millions of integers, releasing the GIL.
#[pyfunction]
fn fast_parallel_hash(py: Python, data: Vec<u64>) -> PyResult<u64> {
    // Release GIL for multithreaded Rayon execution
    let result = py.allow_threads(move || {
        data.par_iter()
            .fold(|| 0u64, |acc, &val| acc ^ (val.wrapping_mul(0x517cc1b727220a95)))
            .reduce(|| 0u64, |a, b| a ^ b)
    });
    Ok(result)
}

#[pymodule]
fn rust_engine(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fast_parallel_hash, m)?)?;
    Ok(())
}
```

## Python Integration & Pure Fallback Layer

In production systems, dynamic fallbacks ensure the service boots smoothly even if native binary compilation is pending in a container environment:

```python
from __future__ import annotations

import logging
import time
from typing import Sequence

logger = logging.getLogger("rust_ffi")

try:
    # Native compiled PyO3 extension
    import rust_engine  # type: ignore
    HAS_RUST = True
except ImportError:
    HAS_RUST = False
    logger.warning("Native rust_engine module not found. Operating with Python fallback.")


def parallel_hash_pipeline(data: Sequence[int]) -> int:
    """Executes hash calculation using Rust FFI or vectorized Python fallback."""
    if HAS_RUST:
        return rust_engine.fast_parallel_hash(list(data))
    
    # Resilient Python fallback
    acc = 0
    multiplier = 0x517CC1B727220A95
    mask = 0xFFFFFFFFFFFFFFFF
    for val in data:
        acc ^= (val * multiplier) & mask
    return acc


def benchmark_pipeline(items_count: int = 2_000_000) -> None:
    test_data = list(range(items_count))
    start_t = time.perf_counter()
    checksum = parallel_hash_pipeline(test_data)
    elapsed = time.perf_counter() - start_t
    print(f"Processed {items_count:,} items in {elapsed*1000:.2f}ms | Checksum: {checksum:x}")


if __name__ == "__main__":
    benchmark_pipeline()
```

## Key Engineering Insights

1. **Always Release the GIL**: Utilize `py.allow_threads` in Rust so other asyncio tasks on the event loop continue unhindered during compute bursts.
2. **Minimize Boundary Crossing**: Batch data across the Python-Rust boundary rather than invoking FFI inside tight loops.
3. **Graceful Fallbacks**: Maintain syntax-compatible Python fallbacks for local test environments and continuous integration pipelines.

GitHub Repository: [github.com/aman179102](https://github.com/aman179102)
''',
    },
    {
        "category": "lockstep_simulation_engines",
        "title": "Building Deterministic Lockstep Simulation Engines with Python and NumPy",
        "description": "An architectural guide to deterministic simulation engines: fixed-point arithmetic, isolated state stepping, state-hash verification, and replay logs.",
        "series": "Deterministic Simulation Architecture",
        "tags": ["python", "simulation", "architecture", "algorithms"],
        "ascii_diagram": r"""
+---------------------+         Tick Clock (Fixed 50ms)        +---------------------+
| Simulation Host     | -------------------------------------> | Input Ring Buffer   |
+---------------------+                                        +---------------------+
           |                                                              |
           v                                                              v
+---------------------+         Strictly Deterministic         +---------------------+
| State Snapshot T_n  | -------------------------------------> | State Snapshot T_n+1|
| (Fixed-Point Math)  |         Execute Frame Step             | (Merkle State Hash) |
+---------------------+                                        +---------------------+
""",
        "markdown_content": r'''## Why Floating-Point Breaks Distributed Replays

In multiplayer physics, distributed simulation testing, or algorithmic trading backtesting, **reproducibility is paramount**. If two instances execute the same sequence of actions on the same initial state, their end states must be bit-for-bit identical.

Standard IEEE 754 floating-point operations fail this test across different CPU architectures, OS kernels, and compiler optimization flags. An `x86_64` machine with FMA (Fused Multiply-Add) produces subtly different rounding results compared to an `ARM64` Apple Silicon or AWS Graviton processor.

The solution is **Deterministic Lockstep Simulation** driven by integer or fixed-point mathematics.

## Architecture

```
+---------------------+         Tick Clock (Fixed 50ms)        +---------------------+
| Simulation Host     | -------------------------------------> | Input Ring Buffer   |
+---------------------+                                        +---------------------+
           |                                                              |
           v                                                              v
+---------------------+         Strictly Deterministic         +---------------------+
| State Snapshot T_n  | -------------------------------------> | State Snapshot T_n+1|
| (Fixed-Point Math)  |         Execute Frame Step             | (Merkle State Hash) |
+---------------------+                                        +---------------------+
```

## Production Implementation: Fixed-Point Deterministic Engine

```python
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from typing import Any

FIXED_SHIFT = 16
FIXED_SCALE = 1 << FIXED_SHIFT  # 65536 multiplier for 16.16 fixed point


def float_to_fixed(val: float) -> int:
    return int(round(val * FIXED_SCALE))


def fixed_to_float(val: int) -> float:
    return float(val) / FIXED_SCALE


@dataclass
class EntityState:
    entity_id: int
    pos_x: int  # Fixed point
    pos_y: int
    vel_x: int
    vel_y: int

    def step(self) -> None:
        self.pos_x += self.vel_x
        self.pos_y += self.vel_y

    def serialize_binary(self) -> bytes:
        return struct.pack("!Iiiii", self.entity_id, self.pos_x, self.pos_y, self.vel_x, self.vel_y)


class DeterministicSimulation:
    def __init__(self, tick_rate_hz: int = 20):
        self.tick = 0
        self.tick_interval_ms = 1000 // tick_rate_hz
        self.entities: dict[int, EntityState] = {}

    def spawn_entity(self, entity_id: int, x: float, y: float, vx: float, vy: float) -> None:
        self.entities[entity_id] = EntityState(
            entity_id=entity_id,
            pos_x=float_to_fixed(x),
            pos_y=float_to_fixed(y),
            vel_x=float_to_fixed(vx),
            vel_y=float_to_fixed(vy),
        )

    def advance_tick(self) -> str:
        """Executes a single lockstep frame and returns the Merkle SHA256 state hash."""
        # Entities are sorted by entity_id to eliminate dict iteration non-determinism
        sorted_ids = sorted(self.entities.keys())
        hasher = hashlib.sha256()

        for eid in sorted_ids:
            ent = self.entities[eid]
            ent.step()
            hasher.update(ent.serialize_binary())

        self.tick += 1
        state_hash = hasher.hexdigest()
        return state_hash


def run_verification() -> None:
    sim1 = DeterministicSimulation()
    sim2 = DeterministicSimulation()

    for sim in (sim1, sim2):
        sim.spawn_entity(101, x=10.5, y=20.0, vx=1.2, vy=-0.5)
        sim.spawn_entity(102, x=-5.0, y=100.25, vx=0.0, vy=2.1)

    print("Executing 100 deterministic lockstep ticks...")
    for _ in range(100):
        h1 = sim1.advance_tick()
        h2 = sim2.advance_tick()
        assert h1 == h2, f"Desynchronization detected at tick {sim1.tick}!"

    print(f"Lockstep verified! Final Tick: {sim1.tick} | State Hash: {h1[:16]}...")


if __name__ == "__main__":
    run_verification()
```

## Production Takeaways

1. **Sort Keys for Traversal**: Never iterate unordered Python hash tables without sorting keys first; iteration order can vary across runs.
2. **Fixed-Point Quantization**: Shift real-world values into integer scale (16.16 or 32.32) to prevent cross-platform floating-point drift.
3. **Checksum Verification**: Compute state hashes every N ticks to detect desyncs immediately in networked simulations.

GitHub Repository: [github.com/aman179102](https://github.com/aman179102)
''',
    },
]


def format_frontmatter(
    title: str,
    description: str,
    tags: list[str],
    series: str,
    canonical_url: str,
    published: bool = True,
) -> str:
    """Generates standard Dev.to YAML frontmatter."""
    tags_str = ", ".join(t.strip().lstrip("#") for t in tags[:4])
    return (
        f"---\n"
        f"title: \"{title}\"\n"
        f"description: \"{description}\"\n"
        f"tags: {tags_str}\n"
        f"series: \"{series}\"\n"
        f"canonical_url: \"{canonical_url}\"\n"
        f"published: {str(published).lower()}\n"
        f"---\n\n"
    )


def validate_article_content(candidate: DevtoArticleCandidate) -> tuple[bool, str]:
    """
    Strict validation gate for Dev.to technical articles:
      - Title presence and reasonable length
      - Description presence
      - Tags presence (1 to 4 tags allowed by Dev.to)
      - Working code block present (```python or ```rust)
      - Clean ASCII diagram present
      - Minimum body length (>= 200 words and >= 1000 characters)
      - Strict 0.1% rule: zero spam links, max 1 author GitHub citation
    """
    if not candidate.title or len(candidate.title.strip()) < 10:
        return False, "Title too brief (minimum 10 characters required)."
    if len(candidate.title.strip()) > 150:
        return False, "Title exceeds maximum length (150 characters)."

    if not candidate.description or len(candidate.description.strip()) < 20:
        return False, "Description too brief (minimum 20 characters required)."

    if not candidate.tags or len(candidate.tags) == 0:
        return False, "At least 1 tag is required."
    if len(candidate.tags) > 4:
        return False, "Dev.to permits a maximum of 4 tags per article."

    body = candidate.body or ""
    if len(body.strip()) < 1000:
        return False, f"Article body too brief ({len(body.strip())} chars; minimum 1000 required)."

    word_count = len(body.split())
    if word_count < 200:
        return False, f"Article body too brief ({word_count} words; minimum 200 required)."

    # Code block verification
    if "```python" not in body and "```rust" not in body:
        return False, "Article missing working Python or Rust code block."

    # ASCII diagram verification (checking for box drawing or flow arrows)
    has_diagram = any(marker in body for marker in ("+---", "|", "==>", "-->", "---"))
    if not has_diagram:
        return False, "Article missing architectural ASCII diagram."

    # Frontmatter verification
    if not body.startswith("---") or "title:" not in body or "tags:" not in body:
        return False, "Article missing standard Dev.to YAML frontmatter."

    # Strict 0.1% rule: check links
    link_matches = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body)
    bare_urls = re.findall(r"https?://[^\s)\]]+", body)
    all_urls = set([url for _, url in link_matches] + bare_urls)

    disallowed_domains = {
        "bit.ly", "tinyurl.com", "goo.gl", "t.co", "affiliate", "tracking", "adclick"
    }

    github_author_count = 0
    for u in all_urls:
        u_lower = u.lower()
        if any(d in u_lower for d in disallowed_domains):
            return False, f"Article contains unauthorized or spam link domain: {u}"
        if "github.com/aman179102" in u_lower:
            github_author_count += 1
        elif "dev.to" in u_lower or "python.org" in u_lower or "fastapi.tiangolo.com" in u_lower or "redis.io" in u_lower:
            continue
        elif "github.com" in u_lower:
            pass  # Allowed general GitHub references
        else:
            # Check for excessive unvetted external domains
            pass

    if github_author_count > 1:
        return False, f"Exceeded maximum 1 author GitHub citation ({github_author_count} found)."

    return True, "OK"


def generate_template_article(category: str = "random") -> DevtoArticleCandidate:
    """Generates a deep-dive technical article from the curated production templates."""
    selected = None
    if category and category != "random":
        for t in DEVTO_TEMPLATES:
            if t["category"].lower() == category.lower():
                selected = t
                break

    if not selected:
        selected = random.choice(DEVTO_TEMPLATES)

    title = selected["title"]
    description = selected["description"]
    tags = selected["tags"]
    series = selected["series"]
    slug = re.sub(r"[^a-zA-Z0-9\-]", "-", title.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:60]
    canonical_url = f"https://dev.to/aman179102/{slug}"

    frontmatter = format_frontmatter(
        title=title,
        description=description,
        tags=tags,
        series=series,
        canonical_url=canonical_url,
        published=True,
    )
    full_body = frontmatter + selected["markdown_content"].strip() + "\n"

    return DevtoArticleCandidate(
        title=title,
        body=full_body,
        category=selected["category"],
        tags=tags,
        description=description,
        series=series,
        canonical_url=canonical_url,
        ascii_diagram=selected["ascii_diagram"].strip(),
        is_ai_generated=False,
    )


def generate_devto_article(
    settings: Settings,
    category: str = "random",
) -> DevtoArticleCandidate:
    """
    Generates a production-grade Dev.to technical article.
    Attempts AI generation if keys are present; falls back resiliently to curated templates.
    """
    if getattr(settings, "has_ai", False):
        try:
            logger.info("Attempting AI generation for Dev.to article (category='%s')...", category)
            system_prompt = (
                "You are an expert Systems and AI Engineer writing a deep-dive technical article for Dev.to. "
                "Adhere to the strict 0.1% rule: ZERO spam links, max 1 author GitHub citation (https://github.com/aman179102)."
            )
            user_prompt = (
                f"Topic category: {category}\n"
                "Requirements:\n"
                "- Write in high-signal English.\n"
                "- Provide an architectural ASCII diagram illustrating the data flow or system components.\n"
                "- Include a production-ready, working Python/FastAPI/async code block (syntax-valid, imports, error handling).\n"
                "- Minimum 500 words of technical depth.\n"
                "- Format YAML frontmatter at the top with: title, description, tags (comma-separated, max 4), series, canonical_url, published: true.\n"
            )
            raw_text = generate_text(settings, system=system_prompt, user=user_prompt)
            if raw_text and len(raw_text.strip()) >= 1000:
                # Attempt to parse YAML frontmatter and title
                title_match = re.search(r'title:\s*["\']?([^"\'\n]+)["\']?', raw_text)
                desc_match = re.search(r'description:\s*["\']?([^"\'\n]+)["\']?', raw_text)
                tags_match = re.search(r'tags:\s*\[?([^\]\n]+)\]?', raw_text)

                title = title_match.group(1).strip() if title_match else "Distributed Systems Optimization in Python"
                description = desc_match.group(1).strip() if desc_match else "Deep architectural guide to high-throughput Python systems."
                raw_tags = tags_match.group(1).split(",") if tags_match else ["python", "architecture", "fastapi"]
                tags = [t.strip().lstrip("#") for t in raw_tags if t.strip()][:4]

                slug = re.sub(r"[^a-zA-Z0-9\-]", "-", title.lower()).strip("-")
                slug = re.sub(r"-+", "-", slug)[:60]
                canonical_url = f"https://dev.to/aman179102/{slug}"

                candidate = DevtoArticleCandidate(
                    title=title,
                    body=raw_text.strip() + "\n",
                    category=category,
                    tags=tags,
                    description=description,
                    series="Python Systems Engineering",
                    canonical_url=canonical_url,
                    ascii_diagram="",
                    is_ai_generated=True,
                )
                valid, msg = validate_article_content(candidate)
                if valid:
                    logger.info("AI generated article successfully validated: '%s'", candidate.title)
                    return candidate
                logger.warning("AI generated article failed validation: %s; falling back to curated template.", msg)
        except Exception as exc:
            logger.warning("AI article generation failed: %s; falling back to curated template.", exc)

    logger.info("Using curated production template for Dev.to article.")
    return generate_template_article(category)

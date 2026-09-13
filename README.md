# NexusOps Job Scheduling Platform

A production-grade job scheduling system built from scratch to understand how distributed execution actually works — scheduling, claiming, heartbeats, fencing tokens, crash recovery, and reliable message delivery.

**No Celery. No shortcuts.** Every component is implemented and explained.

---

## Why This Project

Most backend tutorials stop at CRUD APIs. Real systems need to answer harder questions: *What happens when a worker crashes mid-execution? How do you prevent two workers from running the same job? How do you retry without duplicating work?*

This project exists to explore those problems by building a complete job scheduling platform — from the database schema to the worker heartbeat loop — and documenting every architectural decision along the way.

---

## The Problem

In production environments, job scheduling typically degrades into a collection of fragile scripts and distributed cron servers:

| Problem | Impact |
|---|---|
| Jobs scattered across multiple cron servers | No single source of truth for what's scheduled |
| Missed executions | Cron fails silently; nobody notices until data is stale |
| Duplicate executions | Two cron servers fire the same job; data gets corrupted |
| No execution history | "Did that report run yesterday?" — nobody knows |
| Worker crashes | Job process dies; lease is never released; job is lost |
| No reliable retry | Job fails once and is forgotten; no exponential backoff |
| No timeout handling | Job hangs forever, consuming a worker slot |
| No priority system | Critical billing job waits behind a low-priority cleanup |
| Poor observability | No dashboard, no metrics, no way to tell what's happening |

NexusOps addresses each of these problems with a purpose-built architecture.

---

## Requirements

### Functional
- Create and manage scheduled jobs (CRON expressions or one-time execution)
- Automatic scheduling based on `next_run_at` timestamps
- Job execution in isolated subprocesses
- Configurable retry with fixed or exponential backoff
- Priority-based execution ordering (CRITICAL / HIGH / MEDIUM / LOW)
- Manual job triggering for testing
- Full execution history with run and attempt tracking
- Real-time dashboard with auto-refreshing stats
- Health check endpoints for load balancers

### Technical
- Atomic job claiming with `SELECT FOR UPDATE SKIP LOCKED`
- Fencing tokens to prevent stale worker updates
- Heartbeat-based lease management (45s lease, 15s heartbeat)
- Watchdog process for crash detection and recovery
- Transactional Outbox Pattern for reliable RabbitMQ publishing
- Idempotency key support for duplicate-safe API mutations
- Rate limiting with token bucket algorithm
- OpenAPI/Swagger documentation

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         NexusOps                                 │
│                                                                  │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────────┐   │
│  │  Django   │───▶│  PostgreSQL  │◀───│  Scheduler Process    │   │
│  │  REST API │    │              │    │  ┌────────────────┐  │   │
│  └──────────┘    │  job         │    │  │ tick (5s)      │  │   │
│       │          │  job_run     │    │  │ publisher (1s) │  │   │
│       │          │  job_attempt │    │  │ watchdog (10s) │  │   │
│       │          │  worker      │    │  └────────────────┘  │   │
│       │          │  outbox_event│    └──────────┬───────────┘   │
│       │          └──────────────┘               │               │
│       │                                         │               │
│       ▼                                         ▼               │
│  ┌──────────┐                          ┌──────────────┐         │
│  │  Redis   │◀────────────────────────│  RabbitMQ     │         │
│  │          │                          │               │         │
│  │ idempot. │                          │ jobs_critical │         │
│  │ rate lim │                          │ jobs_high     │         │
│  └──────────┘                          │ jobs_medium   │         │
│                                        │ jobs_low      │         │
│                                        └──────┬───────┘         │
│                                               │                 │
│                                               ▼                 │
│                                        ┌──────────────┐         │
│                                        │   Worker      │         │
│                                        │   Process     │         │
│                                        │              │         │
│                                        │ ┌──────────┐ │         │
│                                        │ │ executor │ │         │
│                                        │ │ (pool=4) │ │         │
│                                        │ └────┬─────┘ │         │
│                                        │      │       │         │
│                                        └──────┼───────┘         │
│                                               │                 │
│                                               ▼                 │
│                                        ┌──────────────┐         │
│                                        │  Subprocess   │         │
│                                        │  (isolated)   │         │
│                                        └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Role |
|---|---|
| **Django REST API** | Job CRUD, manual triggers, execution history, dashboard stats |
| **PostgreSQL** | Persistent storage for all state — jobs, runs, attempts, workers, outbox events |
| **Scheduler** | Three loops: tick (find due jobs), publisher (outbox → RabbitMQ), watchdog (crash recovery) |
| **RabbitMQ** | Message transport with 4 priority queues; decouples scheduling from execution |
| **Worker** | Polls for claimable attempts, executes handlers in a ThreadPoolExecutor (max 4 concurrent) |
| **Subprocess** | Isolated execution environment for each job handler |
| **Redis** | Best-effort services only: idempotency key cache, rate limiting — not in the critical path |
| **Watchdog** | Detects expired leases, dead workers, and permanently failed runs; creates retry attempts |
| **Outbox** | Ensures reliable event publishing — job creation and message delivery happen in one DB transaction |

---

## How the System Works

### The Complete Lifecycle

```
1. CREATE JOB
   User creates a job with schedule (CRON or one-time), handler, priority, retry config.
   → Job record created with next_run_at calculated.

2. SCHEDULER TICK (every 5s)
   SELECT FOR UPDATE SKIP LOCKED finds ACTIVE jobs where next_run_at <= now.
   For each due job:
     → Create JobRun (status=QUEUED)
     → Create JobAttempt (status=QUEUED, priority, fencing_token=0)
     → Create OutboxEvent (status=PENDING)
     → Update job.next_run_at (or set NULL for one-time jobs)

3. OUTBOX PUBLISHER (every 1s)
   Polls PENDING OutboxEvents with FOR UPDATE SKIP LOCKED.
   Publishes to the correct RabbitMQ priority queue.
   Marks event as PUBLISHED (or FAILED after 10 retries with exponential backoff).

4. WORKER CLAIMS JOB
   Worker polls DB every 2s for claimable attempts.
   Atomic claim: UPDATE ... SET status=RUNNING, worker_id=..., fencing_token=fencing_token+1
   WHERE status='QUEUED' — returns nothing if already claimed.

5. WORKER EXECUTES
   Handler runs in a ThreadPoolExecutor (max 4 concurrent).
   Worker sends heartbeat every 15s, extending leases (45s duration).
   On completion: attempt status → SUCCESS or FAILED, run status updated.

6. RETRY ON FAILURE
   If attempt fails and retries remain:
     → Watchdog creates a new JobAttempt with attempt_number+1, fencing_token=0
     → Worker picks up the new attempt on next poll
   If all retries exhausted:
     → Run marked PERMANENTLY_FAILED

7. CRASH RECOVERY
   Watchdog runs every 10s:
     → Finds RUNNING attempts with expired leases → marks TIMED_OUT, creates retry
     → Finds workers with no heartbeat for 60s → marks DEAD
     → Finds runs where all attempts are terminal → marks SUCCESS or PERMANENTLY_FAILED
```

---

## Problems & Solutions

### Duplicate Job Execution

**Problem:** Two workers receive the same message, or a stale worker returns after its lease expires.

**Why it happens:** In distributed systems, at-least-once delivery is the norm. RabbitMQ can redeliver if a worker crashes after receiving but before acking. A worker might also resume after a temporary network partition.

**Solution:** Atomic claiming with fencing tokens.

```
Worker A                                    Worker B
────────                                    ────────
UPDATE job_attempt
  SET status='RUNNING',
      worker_id='worker-A',
      fencing_token = fencing_token + 1
  WHERE id='...' AND status='QUEUED'
→ 1 row updated ✓

                                            UPDATE job_attempt
                                              SET status='RUNNING',
                                                  worker_id='worker-B',
                                                  fencing_token = fencing_token + 1
                                              WHERE id='...' AND status='QUEUED'
                                            → 0 rows updated ✗ (already RUNNING)

UPDATE job_attempt
  SET status='SUCCESS'
  WHERE id='...' AND fencing_token=1
→ 1 row updated ✓

                                            UPDATE job_attempt
                                              SET status='SUCCESS'
                                              WHERE id='...' AND fencing_token=2
                                            → 0 rows updated ✗ (stale token rejected)
```

**Trade-off:** Fencing tokens add a WHERE clause check on every status update. This is cheap (indexed integer comparison) and eliminates an entire class of race conditions without distributed locks.

---

### Worker Crashes

**Problem:** Worker process dies mid-execution. The job is now "stuck" — it's marked RUNNING but nobody is working on it.

**Why it happens:** OOM kills, segfaults, hardware failures, deployment restarts, network partitions.

**Solution:** Heartbeats + lease expiry + watchdog recovery.

| Component | Interval | Purpose |
|---|---|---|
| Heartbeat | 15s | Worker proves it's alive and extends leases |
| Lease duration | 45s | After this, attempt is considered abandoned |
| Watchdog | 10s | Scans for expired leases and dead workers |

When a worker crashes:
1. Heartbeats stop
2. After 45s, lease expires
3. Watchdog detects expired lease → marks attempt as TIMED_OUT
4. If retries remain → creates new attempt (attempt_number+1, fencing_token=0)
5. Worker picks up new attempt on next poll

**Trade-off:** 45s lease means up to 45s of wasted work after a crash. Shorter leases increase heartbeat overhead. 45s is a reasonable balance for most workloads.

---

### Job Timeouts

**Problem:** A job hangs (infinite loop, stuck network call, deadlock) and never completes.

**Why it happens:** External service outages, buggy handler code, resource exhaustion.

**Solution:** Two-layer timeout handling.

1. **Executor timeout:** `ThreadPoolExecutor` wraps each job with a timeout. If the handler doesn't return within `timeout_seconds`, the thread raises a timeout exception.
2. **Lease-based timeout:** If the executor timeout fails to kill the process (e.g., thread is stuck in C code), the lease expires after 45s and the watchdog marks it TIMED_OUT.

**Trade-off:** The executor timeout is best-effort — Python threads can't be forcibly killed. The lease-based timeout is the hard backstop. Together they cover most failure modes.

---

### Reliable Job Dispatch

**Problem:** A job is created in PostgreSQL but the RabbitMQ message is lost. The job sits forever, never executed.

**Why it happens:** Network partitions between PostgreSQL and RabbitMQ, RabbitMQ restarts, publisher crashes between DB write and message publish.

**Solution:** Transactional Outbox Pattern.

```
1. In a SINGLE database transaction:
   - INSERT INTO job_run (...)
   - INSERT INTO job_attempt (...)
   - INSERT INTO outbox_event (event_type='job_created', payload=..., status='PENDING')

2. Separate publisher loop (every 1s):
   - SELECT ... FOR UPDATE SKIP LOCKED (find PENDING events)
   - Publish to RabbitMQ
   - UPDATE status='PUBLISHED'
```

The job and the message are created atomically. If the publisher crashes, events stay PENDING and are retried. If RabbitMQ is down, events queue up in the database and are published when it recovers.

**Trade-off:** Adds 1s latency between scheduling and execution (publisher poll interval). This is acceptable for jobs that run on 5s+ intervals. For sub-second scheduling, you'd publish directly in the same transaction, but that couples your DB to RabbitMQ availability.

---

### Job Priorities

**Problem:** All jobs are equal — a critical billing reconciliation waits behind a low-priority cache cleanup.

**Why it happens:** Simple FIFO queues don't distinguish between job importance.

**Solution:** Four dedicated RabbitMQ queues + priority-aware claiming.

| Priority | Queue | Use Case |
|---|---|---|
| CRITICAL | `jobs_critical` | Billing, payments |
| HIGH | `jobs_high` | Data sync, reports |
| MEDIUM | `jobs_medium` | Scheduled maintenance |
| LOW | `jobs_low` | Cache cleanup, log rotation |

Workers claim attempts sorted by priority (Python-side sort since PostgreSQL's `ORDER BY priority` string comparison is buggy). Within the same priority, jobs are ordered by `scheduled_for`.

**Anti-starvation:** Each tick creates separate attempts per priority. A stream of CRITICAL jobs won't block HIGH jobs from being created — they'll be in different RabbitMQ queues and claimed in priority order.

**Trade-off:** Four queues means four RabbitMQ connections per publisher tick. At this scale (50 jobs/minute), that's negligible. At 10,000 jobs/minute, you'd want a single queue with message priorities.

---

### Failed Jobs & Retries

**Problem:** A job fails once and is permanently lost. No retry, no backoff, no visibility.

**Why it happens:** Most cron setups have no retry mechanism. The job either succeeds or is silently dropped.

**Solution:** Configurable retry with attempt tracking.

```
Attempt 1: FAILED (handler throws exception)
  → Watchdog creates Attempt 2 (attempt_number=2, fencing_token=0)

Attempt 2: FAILED (external service timeout)
  → Watchdog creates Attempt 3 (attempt_number=3, fencing_token=0)

Attempt 3: SUCCESS
  → Run marked SUCCESS
```

Configuration per job:
- `retry_limit`: 1-10 (default: 1)
- `retry_backoff_type`: FIXED or EXPONENTIAL
- `retry_base_delay_seconds`: base delay (default: 30s)

Example with exponential backoff (base=30s):
- Retry 1: 30s delay
- Retry 2: 60s delay
- Retry 3: 120s delay

**Trade-off:** Exponential backoff delays recovery but protects against cascading failures. Fixed backoff is simpler but can hammer a failing service.

---

### Scheduler Reliability

**Problem:** Scheduler crashes or restarts. Jobs that were due during the outage are missed.

**Why it happens:** Deployments, OOM kills, hardware failures.

**Solution:** `next_run_at` based scheduling + idempotency.

The scheduler doesn't rely on "was the last tick successful?" It simply asks: "Which ACTIVE jobs have `next_run_at <= now`?" This is idempotent — if the scheduler missed 3 ticks, the next tick picks up all due jobs.

**Duplicate prevention:** `SELECT FOR UPDATE SKIP LOCKED` prevents two scheduler instances from processing the same job. The idempotency check (`created_at > now - 2 * interval`) prevents creating duplicate runs for the same scheduled time.

**Trade-off:** SKIP LOCKED means some jobs might be delayed by one tick if two schedulers compete. This is acceptable — the alternative (serialized locking) would be a bottleneck.

---

### Database Scalability

**Problem:** Why not shard across multiple databases?

**Decision:** Single PostgreSQL instance. Here's why:

| Metric | Value |
|---|---|
| Expected write load | ~50 scheduler ticks/min, 0-5 runs/tick |
| Records per year | ~4.3M attempts |
| PostgreSQL capacity | Tens of thousands of writes/sec |

The entire write load is under 1% of what a single PostgreSQL instance can handle on modest hardware.

**Future options (not implemented):**
- **Read replica** — when the dashboard query load exceeds a single instance
- **Table partitioning** — partition `job_attempt` by time when records exceed 100M
- **Connection pooling** — add PgBouncer when worker count exceeds 50

---

## Data Model

```
┌─────────────────┐
│       Job        │
├─────────────────┤
│ id (UUID PK)    │
│ name (unique)   │──── schedule_type: CRON | ONE_TIME
│ handler         │     cron_expression
│ priority        │     run_at
│ status          │     timeout_seconds
│ next_run_at     │     retry_limit
│ last_run_at     │     retry_backoff_type
└────────┬────────┘     retry_base_delay_seconds
         │
         │ 1:N
         ▼
┌─────────────────┐       ┌──────────────────┐
│     JobRun       │       │    OutboxEvent    │
├─────────────────┤       ├──────────────────┤
│ id (UUID PK)    │       │ id (UUID PK)     │
│ job (FK)        │       │ event_type       │
│ status          │       │ aggregate_id     │
│ scheduled_for   │       │ payload (JSON)   │
│ started_at      │       │ status           │
│ finished_at     │       │ attempts         │
│ total_attempts  │       │ next_attempt_at  │
└────────┬────────┘       └──────────────────┘
         │
         │ 1:N
         ▼
┌─────────────────┐
│   JobAttempt     │
├─────────────────┤
│ id (UUID PK)    │
│ job_run (FK)    │
│ job (FK)        │──── Denormalized for query performance
│ attempt_number  │
│ status          │
│ priority        │
│ worker_id       │
│ fencing_token   │──── Monotonic integer, incremented on claim
│ lease_expires_at│
│ duration        │
│ error_message   │
└─────────────────┘

┌─────────────────┐
│     Worker       │
├─────────────────┤
│ id (string PK)  │     worker-hostname-pid
│ hostname        │
│ status          │     ACTIVE | DEAD
│ started_at      │
│ last_heartbeat_at│
└─────────────────┘
```

### Indexes

| Table | Index | Purpose |
|---|---|---|
| `job` | `idx_job_next_run_at` (partial WHERE status='ACTIVE') | Scheduler tick query |
| `job_attempt` | `idx_attempt_claimable` (scheduled_for, priority WHERE status='QUEUED') | Worker claim query |
| `job_attempt` | `idx_attempt_job_id` | Job history queries |
| `job_attempt` | `idx_attempt_status` | Status filtering |
| `worker` | `last_heartbeat_at` | Dead worker detection |
| `outbox_event` | `next_attempt_at` | Publisher polling |

---

## Execution Lifecycle

### Happy Path

```
QUEUED ──▶ RUNNING ──▶ SUCCESS
```

### Retry Path

```
QUEUED ──▶ RUNNING ──▶ FAILED ──▶ QUEUED ──▶ RUNNING ──▶ FAILED ──▶ QUEUED ──▶ RUNNING ──▶ SUCCESS
          attempt 1              attempt 2                        attempt 3
```

### Timeout + Retry

```
QUEUED ──▶ RUNNING ──▶ TIMED_OUT ──▶ QUEUED ──▶ RUNNING ──▶ SUCCESS
          attempt 1                  attempt 2
```

### Permanent Failure

```
QUEUED ──▶ RUNNING ──▶ FAILED ──▶ PERMANENTLY_FAILED
          attempt 1              (all retries exhausted)
```

### Cancellation

```
QUEUED ──▶ CANCELLED
```

### States Reference

| Model | States |
|---|---|
| `JobRun` | QUEUED, RUNNING, SUCCESS, PERMANENTLY_FAILED, CANCELLED |
| `JobAttempt` | QUEUED, RUNNING, SUCCESS, FAILED, TIMED_OUT, CANCELLED |
| `Job` | ACTIVE, INACTIVE, DELETED |
| `Worker` | ACTIVE, DEAD |
| `OutboxEvent` | PENDING, PUBLISHED, FAILED |

---

## Failure Scenarios

| Scenario | System Response |
|---|---|
| Worker crashes mid-execution | Lease expires after 45s → watchdog marks TIMED_OUT → creates retry attempt |
| Scheduler crashes | Missed ticks are harmless — next tick finds all `next_run_at <= now` jobs |
| RabbitMQ unavailable | Outbox events stay PENDING in DB → publisher retries with exponential backoff |
| Redis unavailable | Idempotency and rate limiting degrade gracefully — requests pass through |
| PostgreSQL unavailable | System stops — all state depends on PostgreSQL (this is by design) |
| Job times out | Executor timeout + lease expiry → TIMED_OUT → retry if configured |
| Two workers receive same message | Only one succeeds atomic UPDATE — other gets 0 rows updated |
| Old worker returns after lease expires | Fencing token mismatch → status update rejected (0 rows updated) |
| Outbox publisher fails | Event stays PENDING → retried on next tick → exponential backoff up to 10 attempts |

---

## Technology Stack

| Technology | Version | Role |
|---|---|---|
| Python | 3.12+ | Runtime |
| Django | 5.1 | Web framework, ORM, admin |
| Django REST Framework | 3.15 | API serialization, pagination |
| PostgreSQL | 16 | Primary database |
| RabbitMQ | 3.13 | Message transport (priority queues) |
| Redis | 7 | Idempotency cache, rate limiting (best-effort only) |
| drf-spectacular | 0.27 | OpenAPI/Swagger documentation |
| croniter | 5.0 | CRON expression parsing |
| pika | 1.3 | RabbitMQ client |
| Gunicorn | 23.0 | Production WSGI server |
| Docker Compose | - | Local development orchestration |
| pytest | 8.3 | Test runner |
| pytest-django | 4.9 | Django test integration |

---

## Project Structure

```
nexusops/
├── apps/
│   ├── core/
│   │   └── health.py                  # Liveness + readiness endpoints
│   ├── executions/
│   │   ├── models.py                  # JobRun, JobAttempt, Worker, OutboxEvent
│   │   ├── views.py                   # API: runs, attempts, workers, dashboard, ops
│   │   ├── urls.py                    # /api/v1/runs/, /api/v1/attempts/, etc.
│   │   ├── dashboard.py               # HTML page views
│   │   └── management/commands/
│   │       ├── cleanup_old_runs.py    # Delete old records
│   │       └── reset_stuck_runs.py    # Reset stuck jobs
│   └── jobs/
│       ├── models.py                  # Job model
│       ├── views.py                   # API: CRUD, trigger, runs
│       ├── urls.py                    # /api/v1/jobs/
│       ├── serializers.py             # Validation, next_run_at calculation
│       └── handlers.py                # Handler registry (4 example handlers)
├── nexusops/
│   ├── settings.py                    # Django config, middleware, DRF, security
│   ├── urls.py                        # Root URL routing
│   ├── middleware.py                   # Idempotency key middleware
│   ├── ratelimit.py                   # Token bucket rate limiter
│   └── wsgi.py                        # WSGI entry point
├── scheduler/
│   ├── main.py                        # Entry point (tick + publisher + watchdog)
│   ├── tick.py                        # Find due jobs, create runs (SKIP LOCKED)
│   ├── publisher.py                   # Outbox → RabbitMQ (exponential backoff)
│   ├── rabbitmq.py                    # Connection manager, queue topology
│   └── watchdog.py                    # Crash recovery, lease expiry, dead workers
├── worker/
│   ├── main.py                        # Entry point (claim + execute + heartbeat)
│   ├── claim.py                       # Atomic claim with fencing tokens
│   ├── executor.py                    # ThreadPoolExecutor (max 4 concurrent)
│   └── heartbeat.py                   # Worker registration, lease extension
├── templates/
│   ├── base.html                      # Shared layout with navbar
│   ├── dashboard.html                 # Stats, recent runs, operations
│   ├── jobs.html                      # Job CRUD + trigger
│   ├── runs.html                      # Run history + attempt details
│   └── workers.html                   # Worker status + running attempts
├── static/
│   ├── css/base.css                   # Dark theme styles
│   └── js/
│       ├── dashboard.js               # Dashboard logic + operations
│       ├── jobs.js                    # Jobs management
│       ├── runs.js                    # Runs history
│       └── workers.js                 # Workers page
├── tests/
│   ├── test_models/                   # 31 tests across 5 files
│   ├── test_scheduler/                # 43 tests across 3 files
│   ├── test_worker/                   # 31 tests across 3 files
│   └── test_api/                      # 71 tests across 4 files
├── docker/
│   ├── Dockerfile                     # Production-ready with gunicorn
│   ├── entrypoints/                   # api.sh, scheduler.sh, worker.sh
│   └── postgres/init.sql              # uuid-ossp, pgcrypto extensions
├── docker-compose.yml                 # 6 services: postgres, redis, rabbitmq, api, scheduler, worker
├── requirements.txt                   # 15 dependencies
├── pytest.ini                         # Test configuration
├── conftest.py                        # Django setup for pytest
├── manage.py                          # Django management
├── .env                               # Environment variables
└── ARCHITECTURE.md                    # Detailed design document
```

---

## Setup & Installation

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- Git

### Quick Start

```bash
# Clone the repository
git clone <repository-url>
cd JOB-SCHEDULAR

# Start all services
docker compose up -d

# Verify everything is running
docker compose ps
```

This starts 6 services:

| Service | Port | URL |
|---|---|---|
| API (Django) | 8000 | http://localhost:8000 |
| PostgreSQL | 5432 | localhost:5432 |
| Redis | 6379 | localhost:6379 |
| RabbitMQ Management | 15672 | http://localhost:15672 |
| Scheduler | - | (background process) |
| Worker | - | (background process) |

### Running Tests

```bash
# Run all tests inside the API container
docker compose exec api python -m pytest tests/ -v

# Run a specific test module
docker compose exec api python -m pytest tests/test_scheduler/test_tick.py -v

# Run with short traceback
docker compose exec api python -m pytest tests/ --tb=short -q
```

### Verifying the System

```bash
# 1. Create a job via API
curl -X POST http://localhost:8000/api/v1/jobs/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test_job",
    "description": "A test job",
    "schedule_type": "CRON",
    "cron_expression": "*/5 * * * *",
    "handler": "billing_reconciliation",
    "priority": "HIGH",
    "timeout_seconds": 60,
    "retry_policy": {
      "max_attempts": 2,
      "backoff": "EXPONENTIAL",
      "base_delay_seconds": 30
    }
  }'

# 2. Trigger it manually
curl -X POST http://localhost:8000/api/v1/jobs/<job-id>/trigger/

# 3. Check the dashboard
open http://localhost:8000

# 4. Check health
curl http://localhost:8000/health/ready
```

### Management Commands

```bash
# Cleanup old runs (dry-run by default)
docker compose exec api python manage.py cleanup_old_runs --days 90
docker compose exec api python manage.py cleanup_old_runs --days 90 --execute

# Reset stuck runs (dry-run by default)
docker compose exec api python manage.py reset_stuck_runs --timeout-minutes 60
docker compose exec api python manage.py reset_stuck_runs --timeout-minutes 60 --execute
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `nexusops` | Database name |
| `POSTGRES_USER` | `nexusops` | Database user |
| `POSTGRES_PASSWORD` | `nexusops_dev_password` | Database password |
| `DATABASE_URL` | `postgresql://...` | Full database URL |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | RabbitMQ connection URL |
| `DJANGO_SECRET_KEY` | (dev default) | Django secret key |
| `DJANGO_DEBUG` | `true` | Debug mode |
| `DJANGO_ALLOWED_HOSTS` | `*` | Allowed hosts |

---

## API Endpoints

### Job Management

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/v1/jobs/` | List all jobs (filter: `?status=`, `?priority=`) |
| `POST` | `/api/v1/jobs/` | Create a new job |
| `GET` | `/api/v1/jobs/{id}/` | Get job details |
| `PATCH` | `/api/v1/jobs/{id}/` | Update a job |
| `DELETE` | `/api/v1/jobs/{id}/` | Soft-delete a job (sets status=INACTIVE) |
| `POST` | `/api/v1/jobs/{id}/trigger/` | Manually trigger a job |
| `GET` | `/api/v1/jobs/{id}/runs/` | Get execution history for a job |

**Create Job Example:**

```json
// POST /api/v1/jobs/
{
  "name": "daily_report",
  "description": "Generate daily sales report",
  "schedule_type": "CRON",
  "cron_expression": "0 6 * * *",
  "handler": "generate_daily_report",
  "priority": "HIGH",
  "timeout_seconds": 600,
  "retry_policy": {
    "max_attempts": 3,
    "backoff": "EXPONENTIAL",
    "base_delay_seconds": 60
  }
}

// Response: 201 Created
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "daily_report",
  "schedule_type": "CRON",
  "cron_expression": "0 6 * * *",
  "handler": "generate_daily_report",
  "priority": "HIGH",
  "status": "ACTIVE",
  "next_run_at": "2026-09-14T06:00:00Z",
  ...
}
```

### Execution History

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/v1/runs/` | List all runs (filter: `?status=`, `?job_id=`, `?limit=`, `?offset=`) |
| `GET` | `/api/v1/runs/{id}/` | Get run details with all attempts |
| `GET` | `/api/v1/attempts/` | List all attempts (filter: `?status=`, `?worker_id=`, `?job_id=`, `?priority=`) |
| `GET` | `/api/v1/attempts/{id}/` | Get attempt details |
| `GET` | `/api/v1/dashboard/` | Aggregate dashboard stats |

### Workers & System

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/v1/workers/` | List all workers (filter: `?status=`) |
| `GET` | `/api/v1/workers/{id}/` | Get worker details with running attempts |
| `POST` | `/api/v1/ops/cleanup/` | Dry-run/exec cleanup of old records |
| `POST` | `/api/v1/ops/reset-stuck/` | Dry-run/exec reset of stuck runs |

### Health Checks

| Method | URL | Description |
|---|---|---|
| `GET` | `/health` | Liveness check (always 200 if process is running) |
| `GET` | `/health/ready` | Readiness check (tests PostgreSQL, Redis, RabbitMQ) |

### API Documentation

| URL | Description |
|---|---|
| `/api/docs/` | Swagger UI (interactive API explorer) |
| `/api/redoc/` | ReDoc documentation |
| `/api/schema/` | Raw OpenAPI 3.0 JSON schema |

### Rate Limiting

All API responses include rate limit headers:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 97
X-RateLimit-Category: api
```

When exceeded, returns `429 Too Many Requests` with `Retry-After` header.

### Idempotency

POST/PUT/PATCH/DELETE requests support an `Idempotency-Key` header. Duplicate requests within 24 hours return the cached response with `X-Idempotent-Replay: true`.

---

## Testing

### Test Breakdown

| Module | Tests | Coverage |
|---|---|---|
| `test_models/` | 31 | Model fields, constraints, state transitions |
| `test_scheduler/test_tick.py` | 17 | Scheduling logic, idempotency, concurrency |
| `test_scheduler/test_publisher.py` | 17 | Outbox publishing, backoff, queue routing |
| `test_scheduler/test_watchdog.py` | 14 | Crash recovery, lease expiry, dead workers |
| `test_worker/test_claim.py` | 16 | Atomic claim, priority ordering, heartbeats |
| `test_worker/test_executor.py` | 6 | Handler execution, thread pool |
| `test_worker/test_fencing.py` | 13 | Fencing tokens, stale updates, race conditions |
| `test_api/test_jobs.py` | 27 | Job CRUD, validation, triggers |
| `test_api/test_executions.py` | 21 | Runs, attempts, workers, dashboard |
| `test_api/test_idempotency.py` | 13 | Idempotency middleware |
| `test_api/test_ratelimit.py` | 16 | Rate limiting middleware |
| **Total** | **191** | |

### Running Tests

```bash
# Full suite
docker compose exec api python -m pytest tests/ -v

# Specific module
docker compose exec api python -m pytest tests/test_worker/test_fencing.py -v

# Quick summary
docker compose exec api python -m pytest tests/ -q

# With coverage (if pytest-cov installed)
docker compose exec api python -m pytest tests/ --cov=apps --cov=scheduler --cov=worker
```

### Key Test Categories

- **Model tests:** Verify field constraints, state machines, cascade deletes
- **Scheduler tests:** Verify `FOR UPDATE SKIP LOCKED` behavior, idempotency, priority ordering
- **Worker tests:** Verify atomic claiming, fencing token validation, heartbeat lease extension
- **Fencing tests:** Verify stale worker rejection, concurrent watchdog/worker scenarios
- **API tests:** Verify CRUD operations, filtering, pagination, error handling

---

## Future Improvements

These are intentionally not implemented — the current system is complete and functional without them.

| Improvement | Why |
|---|---|
| **PostgreSQL read replica** | Offload dashboard queries when read load is high |
| **Table partitioning** | Partition `job_attempt` by month when records exceed 100M |
| **Prometheus/Grafana** | Structured metrics (queue depth, claim latency, success rates) |
| **Telegram/Slack alerts** | Notify on PERMANENTLY_FAILED jobs or dead workers |
| **Job dependencies** | Run job B only after job A succeeds (DAG execution) |
| **Cron expression builder UI** | Visual cron editor instead of raw expressions |
| **Job versioning** | Track handler code changes per job |
| **Multi-tenant isolation** | Separate job namespaces per team |
| **Horizontal worker scaling** | Auto-scaling based on queue depth |

---

## Engineering Concepts Demonstrated

1. **SELECT FOR UPDATE SKIP LOCKED** — Concurrent-safe job claiming without distributed locks
2. **Fencing Tokens** — Monotonic integers to reject stale worker updates
3. **Transactional Outbox Pattern** — Reliable message delivery without dual writes
4. **Token Bucket Rate Limiting** — Redis-backed rate limiting with per-client buckets
5. **Idempotency Keys** — Duplicate-safe API mutations via Redis cache
6. **Heartbeat & Lease Management** — Worker liveness detection with automatic recovery
7. **Watchdog Process** — Crash detection, abandoned execution recovery, dead worker marking
8. **Priority Queue Routing** — Multi-queue RabbitMQ topology with priority-aware claiming
9. **Exponential Backoff** — Retry scheduling with jitter to prevent thundering herds
10. **Partial Database Indexes** — Targeted indexes for hot-path queries
11. **OpenAPI Documentation** — Auto-generated API docs with drf-spectacular
12. **Token Bucket** — Rate limiting algorithm with configurable burst capacity

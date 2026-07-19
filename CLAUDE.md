# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Saathi** is an agentic AI coordination platform for Indian SFURTI cluster workshops — it lets a consortium of small workshops collectively fulfil large orders while presenting a single-supplier face to buyers. The core problem is a trust coordination gap: 20 nearby workshops have the combined capacity to take a 10,000-unit order but no buyer will trust 20 separate suppliers. Saathi is the neutral coordinator that splits, verifies, and settles the order — so the buyer deals with one accountable entity and never sees the individual workshops.

**Status:** LLD complete (`docs/saathi_LLD_proper.md`). Core backend modules implemented and unit-tested. Deadline: July 19, 2026.

**Implementation progress (as of July 16, 2026):**
- ✅ `core/allocation/engine.py` — MIP allocation, fully tested (20 tests)
- ✅ `core/trust/scorer.py` — weighted trust formula, fully tested (27 tests)
- ✅ `core/settlement/calculator.py` — penalty rules, fully tested (15 tests)
- ✅ `agents/verification/agent.py` — LLM tool-use loop, fully tested (16 tests as of the Gemini migration; grew further with `get_reference_image` below)
- ✅ `services/coordinator.py` — FSM + Kafka consumer, tested (12 unit tests)
- ✅ `api/routes/` — FastAPI routes, all buyer/workshop/admin endpoints built (BUG-06's SQL-in-route issue fixed)
- ✅ `frontend/` — React buyer + workshop screens built (2026-07-17), Meesho-inspired buyer UI, verified end-to-end against a live Postgres+Kafka+FastAPI stack
- ✅ `db/repositories/` — all 7 repositories now covered by a real-Postgres integration suite (`tests/integration/test_db_flow.py`, 4 tests, no mocks — `tests/integration/` was an empty stub until 2026-07-17), in addition to the mocked unit suite. Note: this suite skips-not-fails when `DATABASE_URL` doesn't resolve (e.g. bare host shell, no override) — see BUG-37 below for why that matters
- ✅ Workshop capacity/notifications/production-status screens (2026-07-17) — `My capacity` and `Notifications` frontend screens built on top of previously backend-only routes (`GET/POST /workshop/capacity`, `GET /workshop/notifications`); new `POST /workshop/sublots/{id}/start-production` action + `OrderCoordinator.on_production_started` (see UX Constraints below for the mentor-session capacity framing)
- ✅ `db/seed.sql` (2026-07-17) — `cost_per_unit` now correlates with `quality_tier` per real workshop; previously uncorrelated, so `quality_min` narrowed *which* workshops were eligible but a buyer paying for higher quality within that pool wasn't reliably paying more for it
- ✅ `workers/auto_verify_worker.py` (2026-07-17) — grace period before no-photo auto-verify, fixing BUG-29 (see below); the delivery-flow's `on_sublot_delivered` no longer auto-verifies inline
- ✅ `config.py`/`.env` — fixed BUG-31: no auth token existed for the factory workshop, so any order the MIP sent even partially to the factory directly got permanently stuck at `ASSIGNED` with no way to ever mark it delivered. Fixed by adding a real `token-factory` login (an auto-verify-at-creation alternative was tried, verified live, then explicitly rejected — see this file's Allocation Engine section)
- ✅ Post-delivery defect flagging (2026-07-17) — `on_defect_flagged` now falls back to a `VERIFIED` sub-lot once the order is `CLOSED`, instead of 409ing; see this file's Privacy rule section
- ✅ Judge/senior-engineer backend audit (2026-07-17) — catch-all + validation exception handlers so an unhandled error can no longer break the API's own documented error shape (`api/errors.py`), correlation-ID actually threaded through coordinator logs now (`observability.py`), dead code removed, stale docstrings fixed, an unbounded query capped, `pytest-asyncio`→`anyio` dependency mismatch reconciled
- ✅ Defect-photo upload validation (2026-07-17) — previously any file of any size was written to disk unconditionally with no error handling; `api/uploads.py` + `core/media_types.py` now validate content-type/size before touching disk with clean 415/413 errors, shared by both the buyer and workshop photo routes
- ✅ Frontend UI/UX audit (2026-07-17) — login now validates the token against a live backend call before navigating away (BUG-33) instead of silently landing a bad session on the dashboard; fixed a mobile viewport-overflow bug on the login card (BUG-34); "Cancel order" now requires an inline confirm instead of firing on one click (BUG-35); every error banner across the app got a Retry action (OBS-15); the bulk-order deadline picker can no longer select a past/same-day date that would fail the allocation engine's lead-time filter unexplained (BUG-36); plus loading skeletons, a stat-tile layout for order progress, and a defect-photo preview — see `docs/implementation_bugs_and_observations.md`'s "Twenty-First Pass"
- ✅ Docker Compose robustness audit (2026-07-17) — no service had a `restart` policy (a crashed container just stayed dead) and `zookeeper`/`api` had no healthcheck (so `depends_on: condition: service_healthy` couldn't work for kafka→zookeeper, and `docker compose ps` couldn't tell a broken-but-alive API from a healthy one). Fixed: `restart: unless-stopped` on all 4 services, a TCP healthcheck on zookeeper, `api`'s own `GET /health` wired in as its container healthcheck, kafka's zookeeper dependency upgraded to `condition: service_healthy`. Verified live with a real `docker kill saathi-kafka-1` + auto-recovery test — see `docs/implementation_bugs_and_observations.md`'s "Twenty-Second Pass"
- ✅ Verification confidence gate (2026-07-17) — a `DEFECT` verdict below `verification_defect_confidence_threshold` (0.90) no longer auto-applies against the workshop (no `FAILED` status, no trust event); routed to `NEEDS_HUMAN_REVIEW` instead, verdict still recorded for a human reviewer. Deterministic backstop on top of the model's own prompted judgment, scoped to `DEFECT` only since `OK`/`SPEC_AMBIGUITY` carry no penalty either way — see this file's Verification Agent section and `docs/implementation_bugs_and_observations.md`'s "Twenty-Fourth Pass"
- ✅ Fixed BUG-37 (2026-07-17) — `tests/integration/test_db_flow.py` had been silently `SKIPPED` (not passing) on every local run since the product catalog moved off the placeholder `'kurta'` product_type; the suite's own connection fixture skips-not-fails on an unreachable `DATABASE_URL`, which is why nobody noticed the tests could no longer find a matching row in `db/seed.sql`. Repointed at a real catalog product (`'jute-door-mat'`) and added a 4th test covering the newer `list_capacity`/`start_production`/`list_delivered_past_grace`/`list_for_workshop(limit=...)` repository methods; now genuinely 4/4 passing against real Postgres — see `docs/implementation_bugs_and_observations.md`'s "Twenty-Third Pass"
- ✅ Fixed BUG-38 (2026-07-17) — an independent senior-engineer review actually ran `python -m pytest tests/unit/ -q` rather than trusting this file's own trailing pass-count notes, and found 2 failing: `services/coordinator.py` called `observability.py`'s `set_correlation_id()` at all three FSM entry points (`on_order_placed`/`on_sublot_delivered`/`on_verification_complete`) without ever importing it — a live `NameError` on every order placed through the real API. One-line import fix; see `docs/implementation_bugs_and_observations.md`'s "Twenty-Fifth Pass"
- ✅ LLM provider migration: Anthropic → Gemini (2026-07-17) — `VerificationAgent` and its tool schemas rewritten for `google.genai`'s `contents`/`parts`/`function_call` shape in place of Anthropic's `messages`/content-blocks; `services/coordinator.py`'s client construction swapped accordingly. See `docs/implementation_bugs_and_observations.md`'s "Twenty-Sixth Pass" for the full file list, and its "Twenty-Seventh Pass" for BUG-39/BUG-40 (the SDK version and model-name problems only found by actually calling the live API, not by a clean unit-test run) and "Twenty-Eighth Pass" for BUG-41 (a settlement bug the confidence gate above didn't actually close, caught by a real end-to-end verification run rather than more unit tests)
- ✅ `get_reference_image` tool added to `VerificationAgent` (2026-07-18) — a fourth, optional tool alongside `get_order_spec`/`get_workshop_history` (`agents/verification/prompts.py`, `agents/verification/agent.py`) that fetches a product_type-keyed reference photo (`agents/verification/reference_images.py`, filesystem lookup — no `orders` schema change, since no per-order photo column exists) so the model can compare the defect photo against a real reference image instead of the text spec alone. The image bytes travel as a separate `inline_data` part alongside the tool's JSON `function_response` in the same turn, since Gemini's `function_response.response` field must be JSON-serializable. `assets/reference_photos/*.png` currently hold placeholder images (one per `frontend/src/data/catalog.ts` product, reusing that file's own gradient colors/names) watermarked "PLACEHOLDER — NOT REAL PRODUCT PHOTOGRAPHY" — swap in real product photography before a judge-facing demo where this matters; the tool degrades gracefully (`found: false`, no penalty either way) for any `product_type` without a file on disk, including anything a workshop adds via `MyCapacity`'s free-text "Add product". 8 new unit tests across `tests/unit/test_verification_agent.py` and `tests/unit/test_reference_images.py`.
- ✅ Tool-sequencing guard on `VerificationAgent` (2026-07-18) — "the model decides which tools to call before submitting a verdict" used to be enforced only by `SYSTEM_PROMPT`, not by code: `submit_verdict` was accepted the instant the model returned it, with nothing checking `get_order_spec`/`get_workshop_history` had actually been called first. `agent.py` now tracks invoked tools in a `called_tools` set against `_REQUIRED_TOOLS_BEFORE_VERDICT`, and rejects a premature `submit_verdict` with an explanatory tool error the model can recover from within the same loop (same turn-taking mechanism as any other tool response) rather than silently accepting an ungrounded verdict or crashing. If required context still hasn't been gathered by the final stricter-prompt retry, it escalates to `NeedsHumanReviewError` instead of accepting one anyway. 3 new tests in `tests/unit/test_verification_agent.py`; see `docs/agent_activity_trail_plan.md` for the reasoning that led here — a real gap identified in review, not a hypothetical one, verified against the actual loop before and after the fix.
- ✅ Admin "Needs review" surface + redo path (2026-07-18) — new `admin` role end-to-end: `GET /admin/sublots/needs-review` (`db/repositories/sublot_repository.py`'s `list_needing_review`, `api/models.py`'s `ReviewItem` — deliberately includes `workshop_id`, since this view is admin-only and the buyer-facing privacy rule doesn't apply here) lists any sub-lot stuck in `VERIFYING` or resting in `NEEDS_HUMAN_REVIEW`; `POST /admin/sublots/{id}/retry-verification` (`OrderCoordinator.retry_verification`) re-locates the original defect photo on disk and re-drives `on_verification_complete` unchanged. New `frontend/src/pages/admin/NeedsReview.tsx` + nav badge (`useAdminReviewCount`). Found and fixed two real bugs while building/testing this — see `docs/implementation_bugs_and_observations.md`'s "Thirty-First Pass": BUG-46 (`agent.py` only caught `genai_errors.APIError`, missing raw `httpx.HTTPError` transport failures — a live `CERTIFICATE_VERIFY_FAILED` was reaching FastAPI as an unhandled 500 instead of the documented retry→`VerificationError` path) and BUG-47 (`/app/uploads` had no persistent Docker volume, so rebuilding the `api` container to deploy this very feature destroyed the photo evidence for 5 pre-existing backlog sub-lots — fixed with a named volume, verified by rebuilding again and confirming a fresh photo survives). Backend fully curl-verified against the live stack; the new frontend page type-checks/builds clean but was not visually confirmed in-browser this session (Claude-in-Chrome extension disconnected mid-session and did not reconnect).
- ✅ `VerificationAgent` loop control flow moved onto LangGraph (2026-07-18) — initial pass: a control-flow-only refactor into a thin 3-node graph (`model`/`act`/`stricter_retry`), Gemini client/tools/contents shape untouched. Reverses the earlier project-wide "Explicitly NOT using LangGraph" call, scoped specifically to this component — see Final Tech Stack above. Doing this also surfaced that the repo had no project-local virtualenv (installs were landing in the shared global Python site-packages); added `.venv/` and moved all installs there, after first discovering and reverting an accidental global dependency conflict this caused with an unrelated pre-existing `langchain`/`langsmith` install on this machine. **Superseded same day by the redesign below** — the 3-node version was a fair target for "this barely uses what LangGraph is for."
- ✅ Fixed a real, live-verified bug where a cancelled order left a phantom actionable sub-lot on the assigned workshop's dashboard (2026-07-19) — `POST /orders/{id}` cancel released reserved capacity per sub-lot but never touched the sub-lot rows themselves, leaving them permanently stuck at `ASSIGNED`. Caught live: cancelled a real order via the buyer UI, then logged in as the assigned workshop and found a fully-live "Start production" / "mark delivered" card for an order the buyer had already cancelled — clicking either would have driven a genuine state-machine action against a cancelled order. Sub-lots aren't hard-deleted on cancel (`db/schema.sql`'s Order State Machine notes previously implied they would be) because `notifications`/`trust_events`/`verification_results`/`payments` all reference `sublot_id` with `NO ACTION` foreign keys, and `notifications` in particular already exists by the time an `ALLOCATED` order can be cancelled (fired at allocation time) — a hard delete would have raised a `ForeignKeyViolationError` on the realistic cancel path, trading a silent bug for a hard crash. Fixed properly instead: added a `CANCELLED` value to the `sublot_status` enum, a new `SublotRepository.cancel_for_order()` scoped to `ASSIGNED` sub-lots, wired into the cancel route. The frontend needed zero logic changes — `MySublots.tsx`'s `ACTIONABLE`/`DELIVERABLE` sets are allow-lists that already exclude anything not explicitly listed, so `CANCELLED` sub-lots fall straight into the existing "Completed & in review" history view with a status pill, for free. Regression test added in `tests/integration/test_db_flow.py::test_cancellation_rules`; the one already-orphaned sub-lot from this session's own live testing was backfilled directly.
- ✅ Fixed a real, guaranteed-to-recur trust-ledger bug in post-delivery defect flagging (2026-07-19) — `trust_events` and `verification_results` both carried a leftover `UNIQUE(sublot_id)` constraint from before that feature existed, on the (once-true, now-false) assumption that a sublot only ever gets verified once. Post-delivery defect flagging deliberately re-verifies an already-VERIFIED sublot, so `TrustRepository.append_event`'s `ON CONFLICT (sublot_id) DO NOTHING` was silently dropping the second trust event *every single time* — the UI said "the responsible workshop's trust score has been updated," the sublot correctly flipped to `FAILED`, and settlement's penalty logic (which reads sublot status, not trust_events) still worked, but the trust score itself never moved. Found via live end-to-end testing (flag a defect on an already-VERIFIED sublot, then check the DB directly) — invisible to the mocked unit suite, since fakes don't simulate real Postgres constraints. Fixed by dropping both `UNIQUE(sublot_id)` constraints (schema + live `ALTER TABLE` migration), removing the now-invalid `ON CONFLICT` clauses, and making `VerificationRepository.get`/`get_for_order` explicitly return the *latest* row per sublot (`ORDER BY created_at DESC`) now that more than one can exist. Verified live twice — once reproducing the silent no-op, once confirming the fix (workshop 1's score moved 89.5%→85.2%, defect rate 26.3%→37.0%, event count 10→11, immediately after a live defect flag). `tests/integration/test_db_flow.py::test_schema_constraints_enforced` was asserting the old (now-wrong) constraint behavior — rewritten to assert two verification passes on one sublot both persist correctly instead. **Trade-off accepted, not yet closed:** this reopens a narrow, unconfirmed Kafka-redelivery window for the *original* single-pass verification path (a consumer crash between "sublot → VERIFIED" and "trust event recorded" could now in theory double-append on redelivery, instead of the constraint silently — and separately incorrectly — orphaning it). The correct long-term fix is a `capacity_released_at`-style atomic claim scoped to the Kafka-triggered first pass specifically (that idiom already exists in this codebase for a different operation); not implemented — accepted as lower risk than the guaranteed, always-on breakage it replaces. `payments` and `notifications` keep their own `UNIQUE(sublot_id)` untouched — those are correct by design (settle-once, one-notification-per-assignment), not instances of this bug.
- ✅ `VerificationAgent` LangGraph redesign: parallel tool dispatch + real human-in-the-loop + Postgres checkpointing (2026-07-18, later same day) — see "The One Real Agent" below for the full shape. Headline pieces: `Send`-based concurrent tool-call dispatch, a genuine `interrupt()`/`Command(resume=...)` pause instead of a dead-end exception (`VerificationAgent.resume_with_guidance`/`resume_with_verdict`, `is_resumable`), and `db/checkpointer.py` (`AsyncPostgresSaver` over its own `psycopg` pool) so a paused thread survives restarts. Admin retry (`POST /admin/sublots/{id}/retry-verification`) now genuinely resumes instead of always restarting from scratch. Two real bugs found and fixed via live testing during this pass, not just reasoning: a checkpointer-serialization **hang** (not even a clean error) the moment a checkpointer sat on top of state holding raw `types.Candidate`/`types.Part` objects — root-caused by bisecting with/without a checkpointer, fixed by making graph state plain JSON-safe data; and `is_resumable` returning a false positive for a *crashed* (not paused) thread, caught live against this dev machine's real Gemini-unreachable failure mode, fixed by checking `task.interrupts` instead of `snapshot.next`. 284 unit tests passing (up from 259), plus a Mermaid diagram export (`scripts/export_verification_graph.py`) for demo purposes. See `docs/implementation_bugs_and_observations.md`'s "Thirty-Third Pass" for the full narrative.

**Known remaining issues:** `docs/implementation_bugs_and_observations.md`. ~~OBS-10 (hardcoded on_time=True)~~ — **fixed**: `services/coordinator.py`'s `_compute_on_time` (lines ~296-299) computes it for real from `sublot.delivered_at` vs. `order.deadline`, not a hardcoded `True`. No open issues from that doc remain outstanding as of 2026-07-19. **BUG-29, BUG-31, BUG-32, BUG-38, BUG-39, BUG-40, and BUG-41 are all fixed** (Fifteenth/Sixteenth/Seventeenth/Twenty-Fifth/Twenty-Seventh/Twenty-Eighth Pass) — see that doc and this file's own AutoVerifyWorker / factory-login / LLM-migration notes above. See that doc's "Sixth Pass — Live Frontend/Backend Integration" (2026-07-17, BUG-19 through BUG-23), "Seventh Pass — First Real Database Integration Test Suite" (2026-07-17, BUG-24), "Tenth Pass — Dockerized Stack Fails to Boot" (2026-07-17, BUG-27), and "Twenty-Seventh Pass — First Live End-to-End Verification of the Gemini Migration" (BUG-39, BUG-40) for bugs found only by running the real stack end-to-end rather than the mocked unit suite.

**Docker Compose networking:** container-to-container traffic (`api` → `postgres`, `api` → `kafka`) must address the other service by its Compose service name, never `localhost` — `localhost` inside a container always means that container itself. `.env`'s `DATABASE_URL`/`KAFKA_BOOTSTRAP_SERVERS` and `docker-compose.yml`'s `KAFKA_ADVERTISED_LISTENERS` all use `postgres`/`kafka` accordingly (see BUG-27). `config.py`'s `localhost` defaults are correct as-is — they're for running `uvicorn` directly on the host, outside Docker.

**Reference paper:** Chauhan et al., "Real-time large-scale supplier order assignments across two-tiers of a supply chain," *Computers & Industrial Engineering* 176 (2023) — MIP formulation adapted from this paper.

---

## Final Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12, FastAPI | Async-native, type-safe |
| MIP solver | PuLP + CBC | `pip install pulp` includes solver; no separate install |
| Database | PostgreSQL 16 | asyncpg driver, append-only trust ledger, JSONB specs |
| Event bus | Kafka (aiokafka) | Bitnami KRaft mode — no Zookeeper |
| LLM agent | Google Gen AI SDK (`gemini-flash-lite-latest`) | Vision + tool use for VerificationAgent |
| Agent orchestration | LangGraph (`StateGraph`, `Send`, `interrupt`/`Command`) | Orchestrates VerificationAgent's tool-use loop — parallel tool dispatch + human-in-the-loop pause/resume — see "The One Real Agent" below. `VerificationAgent`-only, not `OrderCoordinator` |
| Agent checkpointing | `langgraph-checkpoint-postgres` (psycopg) | Persists VerificationAgent's paused threads in the same Postgres DB (`db/checkpointer.py`) — separate connection pool from the app's own asyncpg pool |
| Retry | tenacity | Exponential backoff on Gemini API calls |
| Config | pydantic-settings + `.env` | All business constants in one place |
| Frontend | React | Buyer view (3 screens) + Workshop view (2 screens) |
| Auth | Static bearer tokens in config (v0) | v1 = JWT with role claim |

**Explicitly NOT using:**
- OR-Tools / CP-SAT — PuLP + CBC is sufficient and simpler to install
- LangGraph **for `OrderCoordinator`** — hand-rolled FSM there; explicit states > open-ended loop for the money/state-transition owner. (Reversed for `VerificationAgent` specifically on 2026-07-18 — see "The One Real Agent" below. The reasoning that keeps `OrderCoordinator` off LangGraph is unchanged; it never applied to the one component that was already an open-ended tool-use loop.)
- Celery/Redis — aiokafka workers handle async tasks
- Field-level encryption — deferred to v2; label plaintext in README
- Alembic — raw SQL for hackathon; schema.sql is the migration

---

## Repo Structure

```
saathi/
├── config.py               # Pydantic BaseSettings — ALL business constants here
├── main.py                 # FastAPI app, lifespan (DB pool + Kafka)
├── core/                   # Pure Python — ZERO framework/DB imports here
│   ├── domain.py           # All shared dataclasses (OrderSpec, WorkshopBid, etc.)
│   ├── protocols.py        # typing.Protocol interfaces for all core engines
│   ├── exceptions.py       # Exception hierarchy (SaathiError base)
│   ├── allocation/
│   │   └── engine.py       # AllocationEngine — MIP via PuLP + CBC
│   ├── trust/
│   │   └── scorer.py       # TrustScorer — weighted moving average
│   └── settlement/
│       └── calculator.py   # SettlementCalculator — pure arithmetic
├── services/               # Orchestration — can import core + repositories
│   └── coordinator.py      # OrderCoordinator — FSM lifecycle owner
├── agents/
│   └── verification/
│       ├── agent.py        # VerificationAgent — the only LLM component
│       └── prompts.py      # System prompt (design artifact, not inline string)
├── db/
│   ├── connection.py       # asyncpg pool creation
│   ├── checkpointer.py     # psycopg pool + LangGraph AsyncPostgresSaver (VerificationAgent's checkpointed threads)
│   ├── schema.sql          # Full schema — run once to create tables
│   ├── seed.sql            # Demo seed: 6 workshops + 1 factory
│   └── repositories/       # ALL SQL lives here — nowhere else
│       ├── orders.py
│       ├── sublots.py
│       ├── workshops.py
│       ├── trust.py
│       ├── verification.py
│       └── payments.py
├── api/
│   ├── dependencies.py     # Auth token → role/workshop_id resolution
│   ├── models.py           # Pydantic request/response models (API layer only)
│   └── routes/
│       ├── orders.py
│       ├── workshops.py
│       └── sublots.py
├── events/
│   ├── producer.py         # Kafka producer wrapper
│   └── schemas.py          # Kafka message dataclasses
├── workers/
│   ├── allocation_worker.py    # Consumes saathi.order.placed
│   ├── verification_worker.py  # Consumes saathi.sublot.delivered
│   └── auto_verify_worker.py   # Not Kafka-driven — asyncio sweep loop, auto-verifies DELIVERED sublots past the grace period
├── tests/
│   ├── unit/               # Pure function tests — no DB, no mocks needed
│   │   ├── test_allocation.py
│   │   ├── test_trust.py
│   │   └── test_settlement.py
│   └── integration/
│       └── test_order_flow.py
├── frontend/               # React app
├── docker-compose.yml      # Postgres + Kafka (Bitnami KRaft)
├── requirements.txt
└── .env.example
```

---

## Architecture Rules — Never Violate These

### Layer dependency rules

```
API routes  →  services/  →  core/
workers     →  services/  →  core/
agents/     →  db/repositories/ (for tool implementations)
core/       →  NOTHING (zero imports from db/, api/, events/, agents/)
```

- **`core/` is pure Python.** If a file in `core/` has `import asyncpg` or `import fastapi` or `import google.genai`, that is a bug.
- **All SQL is in `db/repositories/`.** If a SQL string appears anywhere else, that is a bug.
- **No business logic in route handlers.** Routes validate input, call `services/coordinator.py` or a repository, return output. That is all.
- **`OrderCoordinator` is in `services/`, not `core/`.** It depends on repositories and therefore cannot be in the pure core layer.

### OOP rules

- **Use `core/protocols.py` interfaces.** `OrderCoordinator` depends on `IAllocationEngine`, `ITrustScorer`, `ISettlementCalculator` — not the concrete classes. This enables testing with fakes and solver swapping.
- **No magic numbers.** Every business constant (`0.30` trust threshold, `0.5` penalty factor, `0.05` platform fee, `0.20` non-delivery penalty, `0.9` trust recency decay) lives in `config.py` with a named field. Never hardcode them inline. (A confirmed workshop-fault defect is a full write-off, not a percentage — see Settlement Rules below.)
- **Exception hierarchy.** Base class is `SaathiError` in `core/exceptions.py`. Subclasses: `AllocationError`, `VerificationError`, `NeedsHumanReviewError(VerificationError)`. Callers catch at the right level.
- **Validate VerificationOutput with Pydantic.** After the LLM returns JSON, validate `verdict ∈ {OK, DEFECT, SPEC_AMBIGUITY}`, `fault_party ∈ {workshop, buyer, none}`, `confidence ∈ [0,1]`. Do not trust raw LLM output.

### Money rules — non-negotiable

- `Decimal` everywhere money is computed. Never `float`.
- `NUMERIC(12,2)` in PostgreSQL for all money columns. Never `FLOAT`.
- `buyer_total = Σ (delivered_qty × cost_per_unit) × 1.05` — buyer is never billed for undelivered goods.

### Privacy rule — non-negotiable

- `workshop_id` and `workshop_name` are **never** returned in buyer-facing API responses.
- `GET /api/v1/orders/{id}` returns aggregate progress (`assigned_qty`, `in_production_qty`, `verified_ok_qty`, `failed_qty`), not a sublot list.
- Defect flagging is at order level (`POST /orders/{id}/flag-defect`), not sublot level. Buyers cannot know which sublot their defective units came from.

**Post-delivery defect flagging (added 2026-07-17):** `flag-defect` also works after the order is fully `CLOSED` — `on_defect_flagged` falls back to the most recent `VERIFIED` sub-lot when none are still `DELIVERED` (the normal post-settlement state). `VerificationAgent` still runs for real and a workshop-fault `DEFECT` verdict still records a trust event (`trust_events` has no time limit). Deliberately does **not** reopen or rewrite `payments` — that table is append-only (`ON CONFLICT (sublot_id) DO NOTHING`), and `_check_terminal_and_settle` safely no-ops once the order is already `CLOSED`. v0 has no real money transfer anyway, so there is nothing observable a payout adjustment would actually correct. `on_defect_flagged` returns the target sub-lot's final status (never its id) so the API can report a real `verification_status` instead of the old hardcoded `"PENDING"`.

---

## The One Real Agent

**`VerificationAgent` is the only LLM component.** Everything else is deterministic.

Why it's genuinely agentic:
1. **Perceives** — defect photo (vision input)
2. **Uses tools** — `get_order_spec`, `get_workshop_history` (live DB reads), optionally `get_reference_image` (product_type-keyed reference photo — see Implementation progress above)
3. **Reasons** — cross-references photo against spec and workshop track record
4. **Acts** — verdict drives trust score update and settlement penalty

Why everything else is deterministic: allocation, settlement, and trust scoring move money. They must be auditable and reproducible. An LLM that "guesses" an allocation is not defensible when a workshop disputes their sub-lot.

**Three failure/escalation modes — never conflate:**
1. Gemini API error (network, rate limit) → `tenacity` retries 3× → `VerificationError`
2. Model loop non-convergence (no grounded `submit_verdict` within `verification_max_loop_iterations`, plus one stricter-prompt retry) → **pauses on a real human-in-the-loop interrupt**, not a dead end (see below) → `NeedsHumanReviewError(thread_id=...)`
3. `GraphRecursionError` backstop (the loop's own iteration check somehow didn't fire) → `NeedsHumanReviewError(thread_id=None)` — not resumable, since nothing meaningful paused

Caller (`VerificationWorker`, `OrderCoordinator.on_verification_complete`) catches `NeedsHumanReviewError` and sets `sublot.status = NEEDS_HUMAN_REVIEW`. It does NOT raise an unhandled exception.

**System prompt lives in `agents/verification/prompts.py`.** Never inline it as a string inside `agent.py`. It is a design artifact.

**Runs on a real agentic LangGraph `StateGraph`, including human-in-the-loop (2026-07-18, redesigned same day as the initial port).** The first LangGraph pass (same day, earlier) was a thin 1:1 port of the old while-loop into 3 nodes — a fair "senior engineer" critique of that version was that it used none of what LangGraph actually exists for. The redesign below is the response to that critique, scoped and verified rather than done for its own sake:

- **Parallel tool dispatch via `Send`.** `model` → `_route_after_model` inspects the response and, if the model returned multiple `function_call`s in one turn (the system prompt asks for one at a time, but Gemini isn't guaranteed to comply), fans them out as concurrent `Send("execute_tool", ...)` branches rather than a sequential loop — each branch is a narrow, independently-schemad node (`_ToolCallPayload`, not the full graph state). `collect_tool_results` re-sorts by an explicit `index` before merging (never relies on async completion order) and appends one combined turn to `contents`.
- **Real Postgres-backed checkpointing (`db/checkpointer.py`)**, not just in-memory. `AsyncPostgresSaver` over a `psycopg_pool.AsyncConnectionPool`, `.setup()` called once at startup (`main.py`'s lifespan, alongside the asyncpg pool) — creates its own `checkpoints`/`checkpoint_writes`/`checkpoint_blobs` tables, no changes to `db/schema.sql`. Thread ID is `f"verification-sublot-{sublot_id}"` — a natural key, no new column needed anywhere.
- **Genuine `interrupt()`/`Command(resume=...)` human-in-the-loop**, replacing what used to be a dead end. When the loop exhausts without a grounded verdict, `human_review` calls `interrupt({...})` — this **pauses the graph and checkpoints it**, it does not raise. `VerificationAgent.verify()` detects the `__interrupt__` key in the returned state and raises `NeedsHumanReviewError(thread_id=...)` so existing callers don't need to change, but that `thread_id` is now a real handle: `VerificationAgent.resume_with_guidance(sublot_id, guidance)` re-enters the *same* paused run with a human hint and a fresh iteration budget (routes back to `model`); `VerificationAgent.resume_with_verdict(sublot_id, verdict)` lets a human's own decision complete the paused run directly, **without calling the model again** — the graph never re-runs `_call_api` for this path (verified: `mock_call.assert_not_awaited()` in `test_resume_with_verdict_completes_paused_run_without_calling_model_again`). A thread can pause more than once across multiple guidance rounds — each is its own `ainvoke`/resume call with its own recursion budget.
- **`VerificationAgent.is_resumable(sublot_id)`** is the authority `OrderCoordinator.retry_verification` uses to choose resume-vs-restart — sourced from the checkpoint itself (`graph.aget_state(...)`), never guessed. **Real bug found and fixed via live testing, not just reasoning about it:** the first version checked `bool(snapshot.next)`, which is *not* sufficient — a run that crashed via a raised exception (e.g. a real Gemini API failure) also leaves a non-empty `.next`, because LangGraph checkpoints the pending task *before* running it and a raised exception never clears that. Caught live: flagging a defect on this dev machine (Gemini unreachable — see BUG-46/this file's Docker Compose section on the Avast interception issue) produced exactly this crashed-not-paused state, and the naive check let an admin "resume" a thread that was never actually waiting on anything. Fixed to check `any(task.interrupts for task in snapshot.tasks)` — the real, specific signal for "a human_review interrupt is genuinely pending" — with a regression test (`test_is_resumable_false_for_a_crashed_not_interrupted_thread`) reproducing the exact scenario against a `MemorySaver`.
- **Graph state is plain, JSON-safe data, not raw SDK objects** (`finish_reason_is_stop: bool`, `function_calls: list[dict]`, `model_turn: dict` — no `types.Candidate`/`types.Part` stored anywhere). This isn't just style: the checkpointer serializes state at every step, and while `google.genai.types` objects happen to be pydantic (and pickle-safe), holding onto a third-party SDK's internal types in a Postgres-persisted checkpoint is fragile across SDK version bumps — and in this session's own unit tests it surfaced as a real, reproducible **hang** (not a clean error) the first time a checkpointer was added on top of the original 3-node port, because the mocked `types.Candidate`/`types.Part` test fixtures are `MagicMock`, which is not serializable and made `ormsgpack` hang rather than raise. Root-caused by bisecting with/without a checkpointer against an isolated repro script, not guessed.
- **Admin retry (`POST /admin/sublots/{id}/retry-verification`)** now takes an optional body (`api/models.py`'s `RetryVerificationRequest`): no body → unchanged fresh-restart behavior (the only thing that makes sense for a genuinely "Stuck" sub-lot, which never got a checkpoint at all); `guidance` or a direct `verdict` (`verdict`/`fault_party`/`confidence`/`explanation` fields) → `OrderCoordinator._resume_verification` resumes the checkpointed thread instead, rejecting with 409 if `is_resumable` says there's nothing paused to resume.
- **Recursion limit is computed, not left at LangGraph's default 25** — `self._recursion_limit = 6 * settings.verification_max_loop_iterations + 5`, sized for the new topology's worst case (a handful of parallel `execute_tool` branches per round, not just 2 steps/round as in the original 3-node port). A `GraphRecursionError` is still caught as a last-resort backstop (see failure mode 3 above), covered by a dedicated regression test that patches routing to force it, since it's otherwise unreachable under correct routing.
- All this is genuinely exercised, not just claimed: 284 unit tests pass (up from 259 pre-redesign), including dedicated coverage for parallel dispatch, both resume paths, multi-round pausing, the `is_resumable` false-positive fix, and the recursion backstop — plus live verification against the real Docker stack (order placed → delivered → defect flagged → real `VerificationError`-via-Avast path exercised → confirmed `is_resumable` correctly said no → 409, not a silent bad resume).
- A **Mermaid diagram** of the graph can be exported via `python -m scripts.export_verification_graph` (demo/judge-facing artifact; not imported by the app). Note LangGraph's static graph introspection can't see the `model → execute_tool` edge on its own since it's built at runtime via `Send()` — the script manually appends that one edge to the generated diagram.
- This reverses the project's earlier blanket "Explicitly NOT using LangGraph" decision, but only for this one component — see the Final Tech Stack table above for the scope of that reversal. **Also added this session: a project-local `.venv/`** (this repo previously ran directly against the shared global Python install) — `langgraph` and its dependency tree are isolated there; `requirements.txt` is unchanged in meaning, just now installed into `.venv` instead of global site-packages.

**Confidence-gated auto-apply (2026-07-17):** a *third*, non-exceptional outcome — the model returns a valid verdict, but the confidence on a `DEFECT` is below `settings.verification_defect_confidence_threshold` (default `0.90`). `SYSTEM_PROMPT` already tells the model to prefer `SPEC_AMBIGUITY` over a low-confidence `DEFECT`, but that's a soft, probabilistic ask — not something a money/trust-moving decision should rely on alone (same "never trust raw LLM output" principle as the enum/range validation in `_parse_verdict`, extended from *is this output well-formed* to *is this output confident enough to act on*). Below the threshold, `on_verification_complete` sets `NEEDS_HUMAN_REVIEW` instead of `FAILED` — no trust event, no settlement penalty fires — but the verdict is still saved via `verifications.save()` so a human reviewer sees what the agent actually found. Deliberately scoped to `DEFECT` only: `OK`/`SPEC_AMBIGUITY` carry no trust-score or settlement penalty either way, so gating them would add friction without adding protection. `NEEDS_HUMAN_REVIEW` from this path is indistinguishable downstream from the existing exception-driven `NEEDS_HUMAN_REVIEW` paths (loop exhaustion, API failure) — same status, same settlement treatment (treated as `SPEC_AMBIGUITY` per the Settlement Rules below).

---

## Allocation Engine (MIP)

Formulation adapted from Chauhan et al. 2023:

```
Variables:   x[i] ∈ ℤ≥0  (qty to workshop i), x_factory ∈ ℤ≥0
Objective:   min Σ x[i] × cost[i] × (1 + (1 − trust[i]) × penalty_factor)
                 + x_factory × factory_cost
Demand:      Σ x[i] + x_factory = total_qty
Capacity:    x[i] ≤ effective_qty[i]  (= available_qty - reserved_qty)

Pre-filter (hard exclusions before MIP):
  trust_score[i]  ≥  trust_minimum_threshold  (default 0.30)
  quality_tier[i] ≥  order.quality_min
  lead_time_days  ≤  (deadline − allocation_date).days
  effective_qty   >  0
```

Key rules:
- Called **exactly once per order.** Never called again for the same order.
- Factory fallback is part of the MIP (x_factory variable), not a post-processing step.
- If zero workshops pass pre-filter, immediately return full factory fallback — do not run solver.
- `effective_qty = available_qty − reserved_qty`. `reserved_qty` is incremented when sublots are created, decremented when sublots reach terminal state.
- `_factory_fallback(order: OrderSpec, qty: int)` takes the full `OrderSpec` — it needs `factory_workshop_id` and `factory_fallback_cost` from it.
- `AllocationConfig.solver_time_limit_seconds` (default 30) controls the CBC time limit — sourced from `settings.allocation_solver_time_limit_seconds`, never hardcoded.
- `_correct_rounding_drift` patches ±1 FP artifacts in-place. Drift > 1 logs ERROR (solver defect). A post-correction CRITICAL log fires if the demand invariant is still violated after correction.
- **The factory workshop has a real login, `token-factory` → `workshop_id=99`, in `WORKSHOP_TOKENS_JSON`.** A sublot assigned to `factory_workshop_id` by the *original* MIP allocation behaves exactly like a real workshop's sublot — sits `ASSIGNED`, must be delivered via `POST /workshop/sublots/{id}/deliver` authenticated as `token-factory`, then flows through the normal BUG-29 grace period / `AutoVerifyWorker` sweep. This was a deliberate choice (BUG-31): an earlier auto-verify-at-creation approach was implemented, verified live, and explicitly rejected — the factory should behave like a real, auditable party, not something that silently self-completes.
- **A factory shortfall is never backfilled to the factory itself (BUG-32).** `_backfill_factory_shortfall` still auto-verifies its sublots immediately, and still applies correctly whenever a *real workshop* under-delivers (factory-as-trusted-backstop is intentional and untouched there) — but `on_sublot_delivered` explicitly skips calling it when `sublot.workshop_id == order_row["factory_workshop_id"]`. Without that guard, a factory sublot's own shortfall got routed back to `get_factory(product_type)` — i.e. itself — recreating the exact quantity it failed to deliver as a new, auto-verified sublot with zero real delivery, silently defeating BUG-31's accountability fix for the one case it exists for. A factory shortfall is now a real, unfulfilled shortfall, penalized normally by `SettlementCalculator`.

---

## Trust Scorer

```python
# window = last 30 events (settings.trust_window_size), exponential recency
# decay (settings.trust_recency_decay = 0.9 per step back in time — the most
# recent event's weight share stays ~constant as the window grows, unlike a
# linear ramp whose recent-event share erodes as 2/(n+1)). Widened from a
# 10-event linear-weight window (2026-07-19) because a couple of bad
# deliveries could swing a small linear window disproportionately — see
# docs/implementation_bugs_and_observations.md.
score = 0.6 × weighted_on_time_rate + 0.4 × (1 − weighted_workshop_defect_rate)
score = clamp(score, 0.0, 1.0)
cold_start = 0.500  # returned when events list is empty
```

- `on_time` is **set by the system** (comparing `sublot.updated_at` when DELIVERED against `order.deadline`) — not self-reported by the workshop.
- Only defects where `fault_party == "workshop"` count toward `workshop_defect_rate`. Buyer-caused damage and spec ambiguity do not penalize the workshop.
- `spec_disputes >= 3` on a workshop adds a 10% MIP objective penalty multiplier on top of the trust adjustment. Prevents gaming via deliberately ambiguous specs.

---

## Order State Machine

```
PENDING → ALLOCATING → ALLOCATED → IN_PRODUCTION
                                         │
                         ┌───────────────┘
                         ▼
                     VERIFYING ──────────────────► SETTLING → CLOSED
                         │                              ▲
                         ▼ (shortfall)                  │
                   FACTORY_FALLBACK ───────────────────┘
                         │
                         ▼ (factory also fails)
                      FAILED

From PENDING or ALLOCATED only:
  CANCELLED  (buyer cancels before production starts)
```

**Buyer-facing labels** (API maps internal status before returning — never expose raw enum):

| Internal | Buyer sees |
|---|---|
| PENDING | "Order received" |
| ALLOCATING | "Finding workshops" |
| ALLOCATED | "Workshops confirmed — production starting soon" |
| IN_PRODUCTION | "In production" |
| VERIFYING | "Quality check in progress" |
| FACTORY_FALLBACK | "Delivery adjustment in progress" |
| SETTLING | "Processing payment" |
| CLOSED | "Completed" |
| FAILED | "Order could not be fulfilled — contact support" |
| CANCELLED | "Cancelled" |

**Settlement timing rule:** `_settle()` runs ONCE, after ALL sublots reach a terminal state (`VERIFIED`, `FAILED`, or `NEEDS_HUMAN_REVIEW`). Never per-sublot.

**Cancellation rule:** Only allowed when `status ∈ (PENDING, ALLOCATED)`. Once any sublot is `IN_PRODUCTION`, cancel is rejected (409). On cancel: sublots deleted, `reserved_qty` restored for each workshop.

---

## Kafka Design

Two topics, two consumers:

| Topic | Producer | Consumer |
|---|---|---|
| `saathi.order.placed` | POST /orders route | AllocationWorker |
| `saathi.sublot.delivered` | POST /sublots/{id}/delivery route | VerificationWorker |

**Manual offset commit only** — after successful DB write. Never auto-commit.

**JSON deserializer is required.** The producer uses `value_serializer=lambda v: json.dumps(v).encode()`. The consumer must have the matching `value_deserializer=lambda m: json.loads(m.decode("utf-8"))`. Without it, `message.value` is raw bytes and every `payload["key"]` access raises `TypeError` — messages are silently dropped.

**Idempotency:**
- AllocationWorker: before running MIP, `UPDATE orders SET status='ALLOCATING' WHERE id=$1 AND status='PENDING'`. If 0 rows updated, another worker got there first — skip.
- VerificationWorker: `UNIQUE(sublot_id)` on `verification_results` — DB rejects duplicate insert as no-op.

**Exception handling in `_dispatch`:** Only `InvalidStateTransitionError` is caught (replay guard). All other exceptions propagate to `run()`, which catches them in an inner try/except, logs, and skips the `consumer.commit()` — so the message is redelivered. Never use bare `except Exception` in `_dispatch` or FSM handlers — it breaks at-least-once delivery by committing offsets on infrastructure failures.

**Dual-write recovery:** API writes to DB first, then publishes to Kafka. If Kafka publish fails, order is stuck in PENDING. Reconciliation: orders stuck PENDING > 60s get republished. v0: manual admin trigger.

**VerificationWorker decision rule:**
- `defect_photo_url` is null → sublot stays `DELIVERED`, no LLM call. It is
  **not** auto-VERIFIED immediately — see AutoVerifyWorker below (BUG-29:
  immediate auto-verify left no window for a defect to be flagged before
  settlement made `VerificationAgent` unreachable).
- `defect_photo_url` is present → call `VerificationAgent.verify()`

**AutoVerifyWorker (`workers/auto_verify_worker.py`):** a separate,
non-Kafka, plain-`asyncio` sweep loop (added 2026-07-17) that auto-approves
(`OrderCoordinator.auto_verify_expired_deliveries`) any sublot still sitting
in `DELIVERED` after `settings.verification_auto_approve_grace_seconds`
(default 30s) with no photo attached — polls every
`settings.auto_verify_sweep_interval_seconds` (default 5s). This is the
real window that lets `POST /workshop/sublots/{id}/photo` and
`POST /orders/{id}/flag-defect` actually reach `VerificationAgent` instead
of racing an instant auto-verify. Not Celery/Redis (see "Explicitly NOT
using" below) — a plain in-process loop, wired into `main.py`'s lifespan
alongside the three Kafka-consumer workers.

---

## Settlement Rules

```
Buyer is billed only for delivered units:
  buyer_base   = Σ (delivered_qty × cost_per_unit)   ← across all sublots
  platform_fee = buyer_base × 0.05
  buyer_total  = buyer_base + platform_fee

Per-workshop payout:
  Not delivered (delivered_qty=0): net = 0, penalty = base × 0.20
  Defect (workshop fault):         net = 0, penalty = delivered_base (full write-off)
  OK or SPEC_AMBIGUITY:            net = delivered_base, penalty = 0
  NEEDS_HUMAN_REVIEW:              treated as SPEC_AMBIGUITY until resolved

Platform absorbs non-delivery penalty in v0. Security deposit mechanism is v1.
```

A confirmed workshop-fault defect is a full write-off, not a partial 15% haircut — the buyer already isn't billed a cent for those units (`buyer_billable_amount` is 0.00 for a confirmed defect), so any nonzero payout to the workshop would be the platform paying out of its own pocket for goods nobody paid for. `core/settlement/calculator.py::_compute_penalty` returns the full `base_amount` for this case. (This doc previously said a 15% penalty — that language was stale relative to the code and to `tests/unit/test_settlement.py::test_workshop_defect_penalty_is_full_writeoff_of_delivered_value`; fixed 2026-07-19, along with a stale 15%-based fixture in `tests/integration/test_db_flow.py` that never actually exercised `SettlementCalculator` and so never caught the drift.)

---

## Database Rules

- `trust_events` is **append-only**. No `UPDATE` or `DELETE` grants on this table.
- `cost_per_unit` in `sublots` is snapshotted at allocation time. Settlement reads from `sublots`, never from current `workshop_capacity`.
- `workshop_capacity.reserved_qty` is incremented when sublots are created; decremented at terminal state.
- `NUMERIC` for all money. `TIMESTAMPTZ` for all timestamps (UTC). `JSONB` for `item_spec` and `raw_response`.
- All money arithmetic in Python uses `Decimal`. No `float` paths for money.

---

## API Rules

- `202 Accepted` for async operations (order placement, delivery confirmation).
- `404` for missing resources, `409` for invalid state transitions, `403` for cross-workshop access.
- All errors use standard structure: `{ "error": { "code": "...", "message": "..." } }`.
- `X-Correlation-ID` header on every response — UUID generated at order creation, threaded through Kafka messages and all log lines.
- Workshop tokens are scoped. A workshop token for ID=3 calling `/workshops/5/sublots` returns 403 immediately in the dependency layer.

---

## Demo Requirements

Seeded inputs are OK. Seeded outcomes are not.

| Demo action | What must happen live |
|---|---|
| Judge changes order quantity | MIP re-runs, allocation recomputes in < 5s |
| Judge marks a sub-lot as failed | Trust score visibly drops for that workshop |
| Judge uploads a defect photo | VerificationAgent calls tools, returns verdict with explanation |
| Judge calls GET /orders/{id}/quote | Shows estimated total (not hardcoded) |
| Judge cancels order before production | Order transitions to CANCELLED, capacity restored |

Live demo flow: `POST /orders` → poll until ALLOCATED → `GET /quote` → sub-lots go IN_PRODUCTION → `POST /delivery` → `POST /orders/{id}/flag-defect` (with photo) → VerificationAgent runs → trust score updates → settlement computed → `GET /invoice`.

---

## Running Tests

**Use the project venv (`.venv/`, added 2026-07-18), not the global Python install.** Activate it first, or invoke `.venv/Scripts/python.exe` directly on Windows. `requirements.txt` (including `langgraph`) is installed there, not globally — this machine's global site-packages has an unrelated pre-existing `langchain`/`langsmith` install that a global `pip install` can silently break.

```bash
# All unit tests (284 tests as of the LangGraph human-in-the-loop redesign, ~10s, no DB/Kafka needed)
python -m pytest tests/unit/ -v

# Single file
python -m pytest tests/unit/test_allocation.py -v
```

**Important:** The runtime environment has `anyio` but NOT `pytest-asyncio`. Async tests in `test_coordinator.py` use `@pytest.mark.anyio` (not `@pytest.mark.asyncio`). Do not add `pytest.mark.asyncio` — it is an unknown mark in this environment.

## Test Priority

Unit tests cover pure functions and FSM handlers with zero DB/Kafka:

1. `test_allocation.py` (20) — MIP edge cases: all excluded, partial capacity, factory fallback, zero bids, factory bid stripped, rounding drift paths, AllocationError on missing factory
2. `test_trust.py` (27) — scoring formula, cold start, recency weighting, fault attribution, explanation consistency
3. `test_settlement.py` (15) — each penalty rule, partial delivery, platform fee, NEEDS_HUMAN_REVIEW handling
4. `test_verification_agent.py` — retry logic, LLM output validation, OSError wrapping, MIME type rejection, `get_reference_image` tool dispatch (plus `test_reference_images.py` for the filesystem lookup itself)
5. `test_coordinator.py` (12) — idempotency guards, BUG-04/BUG-05 regressions, settle path selection

Shape-only assertions for VerificationAgent output (LLM output, never exact-match test).

---

## Agents Explicitly Rejected

Do not re-propose without a strong new argument:

| Agent | Why rejected |
|---|---|
| Per-workshop capacity agent | Capacity is owner-maintained in `workshop_capacity` table. No live negotiation needed. |
| Fraud/collusion detection agent | No labeled fraud cases. Would produce arbitrary outputs. |
| Escalation-severity classifier | It's a confidence threshold, not a reasoning step. |
| Logistics re-routing agent | Reuse AllocationEngine re-solve. Not a new code path. |
| Onboarding trust agent | No ground truth. Fixed cold-start = 0.500 is more defensible. |
| Coordinator as LLM agent | FSM with explicit states is auditable. Open-ended agent loop is not. |

---

## UX Constraints

**Buyer (3 screens max):**
1. Order intake form
2. Order status — shows aggregate progress, single timeline, never workshop names
3. Invoice — shows grand total and platform fee breakdown

**Workshop owner (4 screens, expanded 2026-07-17 — see below):**
1. My sub-lots — item type, qty, deadline, status for their own sublots only. Includes a workshop-triggered "Start production" action (`POST /workshop/sublots/{id}/start-production`, `ASSIGNED → IN_PRODUCTION`) — previously this transition only ever happened retroactively as a side effect of delivery, so the buyer's "In production" label never appeared until something was already being delivered. One-directional for now; a revert path (`IN_PRODUCTION → ASSIGNED`) is intentionally deferred pending mentor input, not built.
2. My capacity — "Workshop A has this capacity" per-product breakdown: **Available Inventory** (editable, `available_qty`), **In Transit** (read-only, `reserved_qty` — "units committed to in-flight orders"), **Serving Capacity** (read-only, derived: `available_qty − reserved_qty`). This three-field breakdown is the mentor-session framing; the arithmetic is unchanged from `WorkshopBid.effective_qty` — no new capacity model, just a real UI over data that already existed but was previously only mutable via API, never visible.
3. Notifications — order-allocated notifications (`GET /workshop/notifications`, backed by `saathi.sublot.assigned` → `NotificationWorker`); backend existed since the Kafka design was built but had no frontend until now.
4. My trust score — grade, score, plain-language explanation, last 5 events

Workshop owners see **nothing** about other workshops.

---

## What Is Not Being Built (v0)

| Feature | Status |
|---|---|
| Field-level encryption | v2. Label plaintext in README. |
| Live payment rails (UPI/escrow) | Settlement math only. No actual transfer. |
| Full renegotiation loop | Factory fallback only — agreed with mentor. |
| Automated deadline enforcement | Manual admin trigger (`POST /admin/orders/{id}/enforce-deadline`). v1: cron job. |
| Push notifications / webhooks | Client polls `GET /orders/{id}`. v1: SSE. |
| Workshop security deposit collection | Penalty computed, absorption logged. v1: escrow. |
| ML trust scoring | Rule-based formula is more defensible. No training data exists. |
| Pre-order price quote | Quote available after ALLOCATED state. True pre-order quoting is v1. |
| Appeal path for SPEC_AMBIGUITY | `spec_disputes` counter adds MIP penalty. Full arbitration is post-pilot. |

---

## Key Reference Documents

- `docs/saathi_LLD_proper.md` — **primary source of truth** for all module interfaces, DB schema, API contracts, sequence diagrams, and business rules
- `docs/saathi_decision_log.md` — decisions from mentor Slack thread (what was cut and why)
- `docs/paper.pdf` — Chauhan et al. 2023 (MIP formulation reference)
- `docs/image.png` — mentor-approved HLD flow diagram

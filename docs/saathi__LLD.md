
## 1. Scope

This document covers the internal design of each module in Saathi — class interfaces, database schema, API contracts, sequence diagrams, and algorithm design. It does not cover infrastructure setup or test implementation.

---

## 2. System Module Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                            │
│         (FastAPI routes — validation and routing only)      │
└────────┬───────────────────────────────────────┬────────────┘
         │                                       │
         ▼                                       ▼
┌─────────────────┐                   ┌──────────────────────┐
│   Event Layer   │                   │  Coordinator Layer   │
│  (Kafka topics) │                   │  (OrderCoordinator,  │
│                 │                   │   FSM lifecycle mgr) │
└────────┬────────┘                   └──────────┬───────────┘
         │                                       │
         ▼                                       ▼
┌─────────────────────────────────────────────────────────────┐
│                      Core Layer                             │
│   AllocationEngine | TrustScorer | SettlementCalculator    │
│         (pure functions — no framework dependencies)        │
└────────────────────────────┬────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌──────────────────┐
│  Agent Layer    │ │ Repository Layer │ │  Worker Layer    │
│VerificationAgent│ │  (all DB access) │ │ (Kafka consumers)│
└─────────────────┘ └─────────────────┘ └──────────────────┘
```

**Layer rules:**
- API layer calls Service layer only — never Core or Repository directly
- Core layer has zero imports from API, DB, or Kafka — pure business logic
- Repository layer owns all SQL — no SQL anywhere else
- Agent layer calls Repository for tool implementations

---

## 3. Module Design

### 3.1 AllocationEngine

**Responsibility:** Given an order and a list of workshop bids, return the optimal sub-lot split using MIP. Called exactly once per order. Stateless.

**Interface:**

```
Class: AllocationEngine
Location: core/allocation/engine.py

Methods:
  allocate(order: OrderSpec, bids: List[WorkshopBid]) -> List[SubLotDraft]
    - Filters ineligible workshops (quality, deadline, capacity)
    - If zero workshops pass pre-filter: immediately returns factory fallback for full qty
    - Solves MIP using PuLP + CBC solver
    - Returns list of sub-lot assignments
    - Returns factory fallback sub-lot if demand unmet after solver
    - Raises: AllocationError if solver status not OPTIMAL and no factory fallback possible

  _filter_eligible(order: OrderSpec, bids: List[WorkshopBid]) -> List[WorkshopBid]
    - Private method
    - Applies hard pre-filters before MIP runs

  _factory_fallback(qty: int) -> List[SubLotDraft]
    - Private method
    - Returns sub-lot assigned to factory workshop
```

**Data classes (no ORM — pure Python dataclasses):**

```
OrderSpec:
  order_id: int
  total_qty: int
  deadline: date
  quality_min: int            # 1–5
  allocation_date: date       # passed in — no datetime.now() in pure functions
  factory_fallback_cost: Decimal
  factory_workshop_id: int    # ID of the workshops row where is_factory = TRUE
  

WorkshopBid:
  workshop_id: int
  available_qty: int
  cost_per_unit: Decimal
  quality_tier: int         # 1–5
  lead_time_days: int
  trust_score: float        # 0.0–1.0

SubLotDraft:
  order_id: int             # which order this sub-lot belongs to
  workshop_id: int
  qty_assigned: int
  cost_per_unit: Decimal    # snapshot at allocation time
```

**MIP Formulation** (adapted from Chauhan et al. 2023):

```
Decision variables:
  x[i]    ∈ ℤ≥0    quantity assigned to workshop i
  x_f     ∈ ℤ≥0    quantity assigned to factory fallback

Objective (minimize trust-adjusted cost):
  min  Σ  x[i] × (cost[i] × (1 + (1 − trust[i]) × 0.5))  +  x_f × factory_cost

Subject to:
  [Demand]    Σ x[i] + x_f  =  total_qty          ∀ eligible workshops
  [Capacity]  x[i]           ≤  available_qty[i]   ∀ i

Pre-filter (before MIP, hard exclusions):
  trust_score[i]                           ≥  0.30
  quality_tier[i]                          ≥  order.quality_min
  lead_time_days[i]                        ≤  (deadline − today).days
  effective_qty[i] = available_qty[i]
                   - reserved_qty[i]       >  0    ← net uncommitted capacity only
```

**Trust adjustment design rationale:**

The trust multiplier `(1 + (1 − trust) × 0.5)` is linear and bounded:
- trust = 1.0 → multiplier = 1.00 (no markup — perfectly reliable)
- trust = 0.5 → multiplier = 1.25 (25% effective cost increase)
- trust = 0.3 → multiplier = 1.35 (35% effective cost increase)
- trust < 0.3 → hard excluded before MIP runs

A division-based formula (`cost / trust`) was considered and rejected — it produces unbounded effective costs near zero trust, causing the solver to prefer factory fallback over usable but low-history workshops. The linear formula keeps penalty proportional and predictable.

**Factory fallback cost is not trust-adjusted** — intentional. Factory is the consortium's trusted backstop (implicit trust = 1.0 by design). Its cost enters the objective unadjusted.

**Assumption (v0):** `lead_time_days` is treated as a fixed constant regardless of sub-lot size. A workshop is either fully eligible (lead_time ≤ deadline) or fully excluded. Partial-quantity-before-deadline scheduling is out of scope for v0.

---

### 3.2 TrustScorer

**Responsibility:** Compute a workshop's trust score from its append-only event history. Called after every verification result.

**Interface:**

```
Class: TrustScorer
Location: core/trust/scorer.py

Methods:
  compute_score(events: List[TrustEvent]) -> float
    - Returns float in range [0.000, 1.000]
    - Returns 0.500 if events list is empty (cold start)
    - Applies recency weighting (recent events weighted higher)

  score_explanation(events: List[TrustEvent]) -> List[str]
    - Returns plain-language lines for workshop owner UI
    - e.g. ["On-time delivery rate: 80%", "Workshop defect rate: 10%"]
```

**Data class:**

```
TrustEvent:
  workshop_id: int
  sublot_id: int
  on_time: bool           # computed by system: delivered_at <= (order.deadline - buffer)
                          # NOT self-reported by workshop. Coordinator sets this
                          # by comparing sublot.updated_at (when DELIVERED was set)
                          # against orders.deadline.
  defect_found: bool
  fault_party: str        # "workshop" | "buyer" | "none"
  created_at: datetime
```

**SPEC_AMBIGUITY anti-gaming rule:**
A SPEC_AMBIGUITY verdict must NOT be free of consequence. Any sublot where the LLM returns SPEC_AMBIGUITY increments a `spec_disputes` counter on the workshop. When `spec_disputes >= 3`, the workshop's allocation weight is reduced by an additional 10% multiplier in the MIP objective — even if their trust score is healthy. This discourages workshops from writing deliberately vague specs to escape defect penalties.

**Scoring formula:**

```
Window: last 10 events (configurable)
Weights: [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
         (most recent = 1.0, oldest = 0.1)

on_time_rate     = weighted_average(event.on_time for event in events)
workshop_defects = weighted_average(
                     event.defect_found AND event.fault_party == "workshop"
                     for event in events
                   )

score = (0.6 × on_time_rate) + (0.4 × (1 − workshop_defects))
score = clamp(score, 0.000, 1.000)
```

---

### 3.3 SettlementCalculator

**Responsibility:** Compute per-workshop payout and buyer invoice. Pure arithmetic. No DB access, no LLM calls.

**Interface:**

```
Class: SettlementCalculator
Location: core/settlement/calculator.py

Methods:
  compute(sublots: List[SubLotRecord],
          verifications: Dict[int, VerificationRecord]) -> SettlementResult
    - sublots: all sub-lots for the order
    - verifications: keyed by sublot_id
    - Returns: SettlementResult with per-workshop payments and buyer total
    - Uses Decimal for all arithmetic (not float)

SettlementResult:
  payments: List[PaymentDraft]
  buyer_total: Decimal      # sum of all base costs × (1 + platform_fee)
```

**Data classes:**

```
SubLotRecord:             # a sublot already saved to DB (read back for settlement)
  sublot_id: int
  order_id: int
  workshop_id: int
  qty_assigned: int
  delivered_qty: Optional[int]
  cost_per_unit: Decimal
  status: str

VerificationRecord:       # a verification_results row (read for settlement)
  sublot_id: int
  verdict: str            # "OK" | "DEFECT" | "SPEC_AMBIGUITY"
  fault_party: str
  confidence: float

PaymentDraft:             # settlement output, written to payments table
  workshop_id: int
  base_amount: Decimal
  penalty: Decimal
  net_amount: Decimal
```

**Penalty rules table (data-driven, not buried in code):**

```
┌──────────────────────────┬──────────────────┬─────────────────┬──────────────────────────┐
│ Condition                │ Workshop penalty  │ Net to Workshop │ Buyer pays               │
├──────────────────────────┼──────────────────┼─────────────────┼──────────────────────────┤
│ Sub-lot not delivered    │ 20% of base      │ 0               │ 0 (NOT billed for undelivered)
│ Defect, workshop at fault│ 15% of base      │ base − penalty  │ base (goods received)    │
│ Defect, spec ambiguity   │ 0                │ base            │ base (goods received)    │
│ Verified OK              │ 0                │ base            │ base (goods received)    │
└──────────────────────────┴──────────────────┴─────────────────┴──────────────────────────┘

Buyer invoice rules:
  buyer_base   = Σ (delivered_qty × cost_per_unit) for ALL sublots  ← only delivered units
  platform_fee = buyer_base × 0.05                                  ← fee only on delivered value
  buyer_total  = buyer_base + platform_fee

  The workshop penalty (20% for non-delivery) is absorbed by the platform in v0.
  In v1: held from a workshop security deposit. State in README explicitly.

  "Not delivered" means delivered_qty = 0. Partial delivery uses §5.4 rules.
```

---

### 3.4 VerificationAgent

**Responsibility:** The only LLM component. Judges a buyer-submitted defect photo against the order spec and workshop history. Uses tool calls to fetch live data before reasoning.

**Interface:**

```
Class: VerificationAgent
Location: agents/verification/agent.py

Methods:
  verify(sublot_id: int,
         photo_bytes: bytes,
         order_id: int,
         workshop_id: int) -> VerificationOutput
    - Sends image + context to Claude claude-opus-4-8 (vision)
    - Runs agentic tool-call loop until model reaches end_turn
    - Retries up to 3 times on API error (exponential backoff via tenacity)
    - Raises: VerificationError if Anthropic API fails on all retries (network/auth)
    - Raises: NeedsHumanReviewError if model loop does not converge after escalation
    - Caller (VerificationWorker) catches NeedsHumanReviewError → sets sublot NEEDS_HUMAN_REVIEW

  Two distinct failure modes — do NOT conflate:
    1. API error (network, rate limit, auth) → tenacity retries → VerificationError
    2. Loop non-convergence (model won't reach end_turn) → escalation → NeedsHumanReviewError

VerificationOutput:
  verdict: str           # "OK" | "DEFECT" | "SPEC_AMBIGUITY"
  fault_party: str       # "workshop" | "buyer" | "none"
  confidence: float      # 0.0–1.0
  explanation: str       # plain language, shown to buyer and workshop
```

**Tools available to agent:**

```
Tool 1: get_order_spec
  Input:  { order_id: int }
  Output: { item_type, quality_requirements, fabric_spec, tolerances }
  Purpose: agent reads what the order actually required

Tool 2: get_workshop_history
  Input:  { workshop_id: int }
  Output: { trust_score, recent_defect_rate, common_failure_modes }
  Purpose: agent checks if this type of defect is a pattern for this workshop
```

**Agent loop (sequence):**

```
1. Send photo + order_id + workshop_id to Claude
2. Claude calls get_order_spec → we execute → return result to Claude
3. Claude calls get_workshop_history → we execute → return result to Claude
4. Claude reasons and returns verdict JSON (stop_reason = "end_turn")
5. Parse and validate output

Expected: 2 tool calls + 1 final response = 3 iterations total.
Loop cap: 4 iterations (one buffer for unexpected tool re-call).
```

**Failure escalation (not silent crash):**

```
If iterations > 4 without end_turn:
  → Retry once with stricter system prompt:
    "You have the order spec and workshop history. Return your verdict now."
  → If retry also fails:
    → Mark sublot status = "NEEDS_HUMAN_REVIEW"
    → Log full message history for human inspector
    → Do NOT raise unhandled exception — order continues, sublot flagged

A failed verification that silently crashes is worse than a flagged sublot
awaiting human review. Settlement skips NEEDS_HUMAN_REVIEW sublots until
resolved.
```

---

### 3.5 Coordinator

**Responsibility:** Owns the order lifecycle state machine. Triggered by Kafka workers on state-changing events. Has no business logic of its own — delegates to AllocationEngine, VerificationAgent, TrustScorer, SettlementCalculator.

**Interface:**

```
Class: OrderCoordinator
Location: core/coordinator/coordinator.py

Methods:
  on_order_allocated(order_id: int) -> None
    - Called by AllocationWorker after sublots written to DB
    - Transitions order ALLOCATING → ALLOCATED
    - Sends workshop notification (in-memory for v0)

  on_production_started(sublot_id: int) -> None   *(added 2026-07-17)*
    - Called from POST /sublots/{id}/start-production (workshop-triggered)
    - Updates sublot ASSIGNED → IN_PRODUCTION explicitly
    - If order.status == ALLOCATED: transitions order ALLOCATED → IN_PRODUCTION
    - Idempotent — no-ops if the sublot already left ASSIGNED

  on_sublot_delivered(sublot_id: int, delivered_qty: int) -> None
    - Called by VerificationWorker after delivery confirmed
    - Updates sublot (ASSIGNED or IN_PRODUCTION) → DELIVERED with delivered_qty
      — ASSIGNED → IN_PRODUCTION is no longer only implicit here: a sublot
      may already be IN_PRODUCTION via on_production_started above, but if
      the workshop delivered without ever calling start-production, this
      remains the retroactive fallback that gets the order label right
      regardless
    - If order.status == ALLOCATED: transitions order ALLOCATED → IN_PRODUCTION
    - Checks if ALL sublots for the order are DELIVERED
    - If yes: transitions order IN_PRODUCTION → VERIFYING

  on_verification_complete(sublot_id: int, result: VerificationOutput) -> None
    - Updates sublot DELIVERED → VERIFIED or NEEDS_HUMAN_REVIEW
    - Calls TrustScorer.compute_score() and updates trust_scores table
    - Checks if ALL sublots for the order are in (VERIFIED, FAILED, NEEDS_HUMAN_REVIEW)
    - If yes: calls _settle(order_id)
    - If shortfall > 0: transitions to FACTORY_FALLBACK before settling

  _settle(order_id: int) -> None
    - Private. Called only when all sublots resolved.
    - Calls SettlementCalculator.compute()
    - Writes payments to DB
    - Transitions order VERIFYING → SETTLING → CLOSED

  _check_timeout(order_id: int) -> None
    - Called periodically (v0: manual trigger, v1: scheduled job)
    - If any sublot stuck IN_PRODUCTION past deadline → mark FAILED
    - Triggers factory fallback for failed sub-lot quantity
```

**State machine:**

```
PENDING ──► ALLOCATING ──► ALLOCATED ──► IN_PRODUCTION
                                               │
                              ┌────────────────┘
                              ▼
                          VERIFYING ──────────────► SETTLING ──► CLOSED
                              │                         ▲
                              ▼ (defect + shortfall)    │
                          FACTORY_FALLBACK ─────────────┘
                              │
                              ▼ (factory also fails)
                           FAILED
```

**Key rule — settlement timing:** Settlement runs ONCE, after ALL sublots reach a terminal state (VERIFIED, FAILED, or NEEDS_HUMAN_REVIEW). It does not run per-sublot. NEEDS_HUMAN_REVIEW sublots are treated as SPEC_AMBIGUITY (no penalty) until resolved, so settlement is not blocked by them.

**Cancellation rule:** Orders can be cancelled only when status ∈ (PENDING, ALLOCATED). Once any sublot moves to IN_PRODUCTION, the order cannot be cancelled — workshops have already committed capacity. On cancellation: order → CANCELLED, all ASSIGNED sublots deleted, workshop capacity restored.

**Deadline enforcement (v0):** No automated timeout. Workshop owners are expected to mark delivery. A manual admin endpoint (`POST /admin/orders/{id}/enforce-deadline`) marks overdue sublots as FAILED and triggers factory fallback. Automated enforcement is v1.

**State transitions:**

```
PENDING → ALLOCATING       : order.placed Kafka event consumed by AllocationWorker
ALLOCATING → ALLOCATED     : AllocationWorker calls on_order_allocated()
ALLOCATED → IN_PRODUCTION  : implicit — set when first sublot moves to IN_PRODUCTION
IN_PRODUCTION → VERIFYING  : all sublots reach DELIVERED (Coordinator checks count)
VERIFYING → FACTORY_FALLBACK: any sublot FAILED with shortfall > 0
FACTORY_FALLBACK → SETTLING: factory sub-lot created, all sublots now resolved
VERIFYING → SETTLING       : all sublots resolved, no shortfall
SETTLING → CLOSED          : SettlementCalculator writes payments successfully
any → FAILED               : factory also fails OR deadline enforced with no delivery
```

---

### 3.6 Repository Layer

**Responsibility:** All SQL lives here. No SQL anywhere else in the codebase — not in route handlers, not in core functions, not in workers. Each class wraps the asyncpg connection pool and exposes domain-level methods. Methods are async.

**Location:** `db/repositories/`

```
Class: OrderRepository
  Location: db/repositories/orders.py

  Methods:
    create(spec: OrderSpec, correlation_id: str) -> int
      - INSERT INTO orders; returns new order_id

    get_by_id(order_id: int) -> Optional[OrderRow]
      - SELECT by primary key; returns None if not found

    update_status(order_id: int, status: str) -> None
      - UPDATE orders SET status = $1, updated_at = NOW()

    list_by_buyer(buyer_name: str, page: int, page_size: int) -> List[OrderRow]
      - Paginated SELECT, ORDER BY created_at DESC
```

```
Class: SubLotRepository
  Location: db/repositories/sublots.py

  Methods:
    create_bulk(drafts: List[SubLotDraft]) -> List[int]
      - INSERT all sub-lots for one order in a single transaction
      - Returns list of generated sublot IDs

    get_by_order(order_id: int) -> List[SubLotRow]
      - All sub-lots for an order, ordered by id

    get_by_workshop(workshop_id: int) -> List[SubLotRow]
      - For workshop-facing GET /workshops/{id}/sublots endpoint

    update_status(sublot_id: int, status: str,
                  delivered_qty: Optional[int] = None) -> None

    all_terminal(order_id: int) -> bool
      - True if ALL sublots for order_id are in
        (VERIFIED, FAILED, NEEDS_HUMAN_REVIEW)
      - Used by Coordinator to decide when to run settlement
```

```
Class: WorkshopRepository
  Location: db/repositories/workshops.py

  Methods:
    list_bids() -> List[WorkshopBid]
      - JOIN workshops + workshop_capacity WHERE is_factory = FALSE
      - Returns ALL non-factory workshops — AllocationEngine applies business filters
      - No filtering SQL here; filtering is business logic, not data access

    update_capacity(workshop_id: int, available_qty: int,
                    cost_per_unit: Decimal, lead_time_days: int) -> None
      - INSERT ... ON CONFLICT (workshop_id) DO UPDATE
```

```
Class: TrustRepository
  Location: db/repositories/trust.py

  Methods:
    get_events(workshop_id: int, limit: int = 10) -> List[TrustEvent]
      - SELECT ORDER BY created_at DESC LIMIT limit

    insert_event(event: TrustEvent) -> None
      - INSERT; UNIQUE(sublot_id) — duplicate is a no-op (idempotent)

    upsert_score(workshop_id: int, score: float) -> None
      - INSERT INTO trust_scores ... ON CONFLICT (workshop_id) DO UPDATE

    get_score(workshop_id: int) -> float
      - Returns current score; returns 0.500 if no row exists (cold start)
```

```
Class: VerificationRepository
  Location: db/repositories/verification.py

  Methods:
    insert_result(sublot_id: int, output: VerificationOutput) -> None
      - INSERT; UNIQUE(sublot_id) — duplicate insert is a no-op

    get_by_order(order_id: int) -> Dict[int, VerificationRecord]
      - JOIN verification_results + sublots WHERE sublots.order_id = $1
      - Returns {sublot_id → VerificationRecord}
      - SettlementCalculator receives this dict as input

    get_recent_explanations(workshop_id: int, limit: int = 5) -> List[str]
      - SELECT explanation FROM verification_results
        JOIN sublots ON sublots.id = verification_results.sublot_id
        WHERE sublots.workshop_id = $1 AND verdict = 'DEFECT'
        ORDER BY verification_results.created_at DESC LIMIT limit
      - Used by get_workshop_history tool to populate common_failure_modes
```

```
Class: PaymentRepository
  Location: db/repositories/payments.py

  Methods:
    create_bulk(order_id: int, drafts: List[PaymentDraft]) -> None
      - INSERT all payments for one order in a single transaction
      - ON CONFLICT DO NOTHING — UNIQUE constraint prevents duplicates
```

**Key rule:** Repositories never raise business exceptions. They raise `asyncpg.UniqueViolationError` (for idempotency cases), which callers handle. Business validation happens before the repository is called.

---

## 4. Database Design

### 4.1 Entity Relationship

```
workshops ──< workshop_capacity
workshops ──< sublots
workshops ──< trust_events
workshops ──< trust_scores
workshops ──< payments

orders ──< sublots
sublots ──< trust_events
sublots ──< verification_results
orders ──< payments
```

### 4.2 Schema

```sql
-- WORKSHOPS
CREATE TABLE workshops (
    id             SERIAL PRIMARY KEY,
    name           TEXT        NOT NULL,
    location       TEXT        NOT NULL,
    quality_tier   SMALLINT    NOT NULL CHECK (quality_tier BETWEEN 1 AND 5),
    is_factory     BOOLEAN     NOT NULL DEFAULT FALSE,
    spec_disputes  SMALLINT    NOT NULL DEFAULT 0 CHECK (spec_disputes >= 0),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workshop_capacity (
    id             SERIAL PRIMARY KEY,
    workshop_id    INTEGER     NOT NULL REFERENCES workshops(id),
    available_qty  INTEGER     NOT NULL CHECK (available_qty >= 0),
    reserved_qty   INTEGER     NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0),
    cost_per_unit  NUMERIC(10,2) NOT NULL,
    lead_time_days SMALLINT    NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT one_capacity_per_workshop UNIQUE (workshop_id),
    CONSTRAINT reserved_lte_available CHECK (reserved_qty <= available_qty)
);
-- reserved_qty: units committed to active orders not yet DELIVERED.
-- AllocationEngine uses (available_qty - reserved_qty) as effective capacity.
-- AllocationWorker increments reserved_qty when sublots are created.
-- Decremented when sublot reaches DELIVERED, FAILED, or NEEDS_HUMAN_REVIEW.
-- Prevents double-booking across concurrent orders.

-- ORDERS
CREATE TYPE order_status AS ENUM (
    'PENDING','ALLOCATING','ALLOCATED','IN_PRODUCTION',
    'VERIFYING','FACTORY_FALLBACK','SETTLING','CLOSED','FAILED','CANCELLED'
);

-- Buyer-facing status labels (API layer maps internal status before returning)
-- Never expose raw enum values to buyer.
-- PENDING        → "Order received"
-- ALLOCATING     → "Finding workshops"
-- ALLOCATED      → "Workshops confirmed — production starting soon"
-- IN_PRODUCTION  → "In production"
-- VERIFYING      → "Quality check in progress"
-- FACTORY_FALLBACK → "Delivery adjustment in progress"
-- SETTLING       → "Processing payment"
-- CLOSED         → "Completed"
-- FAILED         → "Order could not be fulfilled — contact support"
-- CANCELLED      → "Cancelled"

CREATE TABLE orders (
    id               SERIAL PRIMARY KEY,
    correlation_id   UUID          NOT NULL DEFAULT gen_random_uuid(),
    buyer_name       TEXT          NOT NULL,
    item_type        TEXT          NOT NULL,
    item_spec        JSONB         NOT NULL DEFAULT '{}',
    total_qty        INTEGER       NOT NULL CHECK (total_qty > 0),
    deadline         DATE          NOT NULL,
    quality_min      SMALLINT      NOT NULL DEFAULT 3,
    status           order_status  NOT NULL DEFAULT 'PENDING',
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- SUB-LOTS
CREATE TYPE sublot_status AS ENUM (
    'ASSIGNED','IN_PRODUCTION','DELIVERED','VERIFIED','FAILED','NEEDS_HUMAN_REVIEW'
);

CREATE TABLE sublots (
    id               SERIAL PRIMARY KEY,
    order_id         INTEGER       NOT NULL REFERENCES orders(id),
    workshop_id      INTEGER       NOT NULL REFERENCES workshops(id),
    qty_assigned     INTEGER       NOT NULL CHECK (qty_assigned > 0),
    cost_per_unit    NUMERIC(10,2) NOT NULL,  -- snapshot at allocation time
    status           sublot_status NOT NULL DEFAULT 'ASSIGNED',
    delivered_qty    INTEGER       CHECK (delivered_qty IS NULL OR delivered_qty >= 0),
    defect_photo_url TEXT,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- TRUST LEDGER (append-only — no UPDATE/DELETE permitted)
CREATE TABLE trust_events (
    id           SERIAL PRIMARY KEY,
    workshop_id  INTEGER     NOT NULL REFERENCES workshops(id),
    sublot_id    INTEGER     NOT NULL REFERENCES sublots(id),
    on_time      BOOLEAN     NOT NULL,
    defect_found BOOLEAN     NOT NULL,
    fault_party  TEXT        CHECK (fault_party IN ('workshop','buyer','none')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT one_trust_event_per_sublot UNIQUE (sublot_id)
);

CREATE TABLE trust_scores (
    workshop_id  INTEGER      PRIMARY KEY REFERENCES workshops(id),
    score        NUMERIC(4,3) NOT NULL DEFAULT 0.500,
    computed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- VERIFICATION
CREATE TABLE verification_results (
    id           SERIAL PRIMARY KEY,
    sublot_id    INTEGER      NOT NULL REFERENCES sublots(id),
    verdict      TEXT         NOT NULL CHECK (verdict IN ('OK','DEFECT','SPEC_AMBIGUITY')),
    fault_party  TEXT         CHECK (fault_party IN ('workshop','buyer','none')),
    confidence   NUMERIC(4,3) NOT NULL,
    explanation  TEXT         NOT NULL,
    raw_response JSONB,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT one_verification_per_sublot UNIQUE (sublot_id)
);

-- SETTLEMENT
CREATE TABLE payments (
    id           SERIAL PRIMARY KEY,
    order_id     INTEGER        NOT NULL REFERENCES orders(id),
    workshop_id  INTEGER        NOT NULL REFERENCES workshops(id),
    base_amount  NUMERIC(12,2)  NOT NULL,
    penalty      NUMERIC(12,2)  NOT NULL DEFAULT 0,
    net_amount   NUMERIC(12,2)  NOT NULL,
    status       TEXT           NOT NULL DEFAULT 'PENDING'
                               CHECK (status IN ('PENDING','PROCESSED','FAILED')),
    created_at   TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    CONSTRAINT one_payment_per_workshop_per_order UNIQUE (order_id, workshop_id)
);
```

### 4.3 Indexes

```sql
CREATE INDEX idx_orders_status           ON orders(status);
CREATE INDEX idx_orders_correlation_id   ON orders(correlation_id);     -- trace by correlation_id
CREATE INDEX idx_sublots_order_id        ON sublots(order_id);
CREATE INDEX idx_sublots_workshop_id     ON sublots(workshop_id);
CREATE INDEX idx_sublots_order_status    ON sublots(order_id, status);  -- settlement check
CREATE INDEX idx_trust_events_ws         ON trust_events(workshop_id, created_at DESC);
CREATE INDEX idx_payments_order_id       ON payments(order_id);         -- settlement read
```

### 4.4 Key Design Decisions

| Decision | Reason |
|---|---|
| `cost_per_unit` snapshot in sublots | Settlement uses price at allocation time, not current price |
| `UNIQUE (sublot_id)` on trust_events and verification_results | Kafka consumer idempotency — processing same message twice is a no-op |
| `raw_response JSONB` on verification_results | Full LLM response stored for audit; disputed verdicts can be reviewed |
| `order_status` as enum | Invalid status values caught at DB, not just application layer |
| `NUMERIC` not `FLOAT` on all money columns | Float arithmetic causes rounding errors in financial calculations |

---

## 5. API Design

### 5.1 Authentication

All endpoints require an `Authorization: Bearer <token>` header.

Two roles with separate tokens (seeded for demo):

| Role | Identity | Can access |
|---|---|---|
| `buyer` | Fixed token per buyer | POST /orders, GET /orders/{id}, GET /orders/{id}/invoice |
| `workshop` | Fixed token per workshop_id | GET /workshops/{own_id}/*, POST /sublots/{own_sublot_id}/delivery |

Workshop tokens are scoped to their own workshop_id. A request from workshop A to `/workshops/B/sublots` returns `403 FORBIDDEN`. This is enforced in the FastAPI dependency layer, not in business logic.

v0 implementation: tokens are static secrets in config (no OAuth). Token-to-workshop_id mapping is a dict in `config.py`:
```python
WORKSHOP_TOKENS = {
    "token-ws-1": 1,
    "token-ws-2": 2,
    ...
}
```
FastAPI dependency reads `Authorization` header, looks up workshop_id, injects into route. v1: JWT with workshop_id claim.

### 5.2 Standard Error Response

All errors return this structure:

```json
{
  "error": {
    "code": "ORDER_NOT_FOUND",
    "message": "Order 42 does not exist"
  }
}
```

### 5.3 Photo Storage

Defect photos are NOT sent via Kafka. Delivery and defect flagging are two sequential actions:

**Step 1 — Workshop marks delivery (POST /sublots/{id}/delivery):**
```
1. API sets sublot status = DELIVERED, writes delivered_qty
2. API publishes saathi.sublot.delivered with defect_photo_url = null
3. VerificationWorker consumes event
4. defect_photo_url is null → auto-verdict: VERIFIED (OK), no LLM call needed
5. Settlement proceeds normally
```

**Step 2 — Buyer flags a defect after receiving shipment (POST /sublots/{id}/flag-defect):**
```
1. Buyer uploads photo via multipart/form-data
2. API saves photo to filesystem: /uploads/{sublot_id}.jpg  (v1: S3 bucket)
3. API writes defect_photo_url to sublots table
4. API publishes a NEW saathi.sublot.delivered event WITH defect_photo_url
   (re-publish overrides the earlier null-photo event — consumer is idempotent)
5. VerificationWorker consumes event
6. defect_photo_url is present → fetch bytes from filesystem
7. Pass bytes to VerificationAgent.verify()
```

**VerificationWorker decision rule:**
- `defect_photo_url` is null → mark VERIFIED (OK), skip LLM, trust event on_time=True
- `defect_photo_url` is present → call VerificationAgent.verify()

Photo bytes never travel through Kafka — only the URL does.

### 5.4 Partial Delivery Rule

If `delivered_qty` < `qty_assigned`:
- The sub-lot is marked DELIVERED with the actual `delivered_qty`
- The shortfall (`qty_assigned - delivered_qty`) is treated as FAILED quantity
- The factory fallback is triggered ONLY for the shortfall quantity
- Settlement penalty applies to the shortfall: `penalty = shortfall × cost_per_unit × 0.20`
- The workshop is paid for what they actually delivered: `net = delivered_qty × cost_per_unit`

### 5.5 Endpoint Contracts

**POST /api/v1/orders**
```
Request:
  { buyer_name: str, item_type: str, item_spec: object,
    total_qty: int, deadline: date, quality_min: int }

Response 202:
  { order_id: int, status: "Order received", polling_url: "/api/v1/orders/{id}" }

Notes:
  - 202 not 201 — allocation is async, order not yet fulfilled
  - Publishes order.placed event to Kafka
  - Validates deadline is in the future
  - polling_url tells the client exactly where to poll for status updates
  - Estimated cost is NOT in this response — not available until allocation runs.
    Client polls GET /orders/{id} until status = "Workshops confirmed", then
    GET /orders/{id}/quote returns cost estimate.
```

**GET /api/v1/orders/{order_id}/quote**
```
Response 200 (after ALLOCATED):
  { order_id, estimated_total: decimal, platform_fee_pct: 5,
    workshop_count: int, factory_qty: int,
    note: "Final invoice may differ if any sub-lot fails quality check" }

Response 409 ALLOCATION_PENDING (before ALLOCATED):
  { error: { code: "ALLOCATION_PENDING",
             message: "Allocation in progress. Check back in a few seconds." } }

Purpose: buyer can see estimated cost and decide to cancel before production starts.
```

**GET /api/v1/orders/{order_id}**
```
Response 200:
  { order_id,
    status: str,               ← buyer-friendly label (see status label map in §4.2)
    buyer_name, total_qty, deadline,
    progress: {                ← aggregate summary, not raw sublot list
      assigned: int,           ← qty in ASSIGNED or IN_PRODUCTION
      in_production: int,
      verified_ok: int,
      failed: int
    },
    allocation_summary: { total_workshops: int, factory_qty: int } }

Errors: 404 ORDER_NOT_FOUND

Privacy rules:
  - workshop_id and workshop_name are NEVER returned to buyer
  - Individual sublot list is NOT returned — buyer sees aggregate progress only
  - factory_qty in allocation_summary shows how much the trusted factory holds
    (buyer knows Saathi uses a backup factory — this is fine to show)
```

**GET /api/v1/orders/{order_id}/invoice**
```
Response 200:
  { order_id, buyer_total: decimal, platform_fee: decimal,
    grand_total: decimal, status: str }

Errors: 404, 409 ORDER_NOT_SETTLED
```

**POST /api/v1/workshops/{workshop_id}/capacity**
```
Request (option A — structured):
  { available_qty: int, cost_per_unit: decimal, lead_time_days: int }

Request (option B — free text, triggers capacity agent):
  { raw_text: "machine 3 is down, can take 200 pieces this week" }

Response 200:
  { workshop_id, available_qty, cost_per_unit, lead_time_days, updated_at }
```

**GET /api/v1/workshops/{workshop_id}/capacity** *(added 2026-07-17, Twelfth Pass)*
```
Response 200:
  [{ product_type, available_qty, in_transit_qty, serving_capacity,
     cost_per_unit, lead_time_days, updated_at }, ...]
     ← one row per product_type this workshop has capacity for

Product rationale (mentor session): "Workshop A has this capacity" view.
  available_qty      = Available Inventory (editable via POST above)
  in_transit_qty      = In Transit (= reserved_qty — "committed to
                         in-flight orders"; read-only, system-maintained)
  serving_capacity     = Serving Capacity, derived: available_qty − in_transit_qty
                         (same arithmetic as WorkshopBid.effective_qty,
                         confirmed against two mentor-session worked
                         examples — not a new capacity resource, just this
                         number made visible to the workshop owner)

Auth: workshop token — always scoped to the caller's own workshop_id.
```

**GET /api/v1/workshops/{workshop_id}/trust-score**
```
Response 200:
  { workshop_id, score: float, grade: str,
    explanation: [str],   ← plain language lines for workshop owner
    history: [{ sublot_id, on_time, defect_found, fault_party, date }] }
    ← last 5 events only

Grade thresholds:
  score >= 0.85 → "A"  (high trust — preferred in allocation)
  score >= 0.70 → "B"  (good standing)
  score >= 0.50 → "C"  (acceptable — cold start default lands here)
  score <  0.50 → "D"  (at risk — may be excluded from future orders)
  score <  0.30 → hard excluded by AllocationEngine pre-filter
```

**POST /api/v1/sublots/{sublot_id}/delivery**
```
Request: { delivered_qty: int, defect_photo_url: str | null }

Response 202:
  { sublot_id, status: "DELIVERED" }

Notes:
  - 202 not 200 — verification is async
  - Publishes sublot.delivered event to Kafka
Errors: 404, 409 SUBLOT_ALREADY_DELIVERED
```

**POST /api/v1/sublots/{sublot_id}/start-production** *(added 2026-07-17, Twelfth Pass)*
```
Request: (no body)

Response 202:
  { sublot_id, status: "IN_PRODUCTION" }

Notes:
  - Workshop-triggered ASSIGNED -> IN_PRODUCTION. Previously this transition
    only ever happened retroactively, as a side effect of the delivery
    endpoint above — meaning the buyer's "In production" label never
    appeared until something was already being delivered. This gives the
    workshop an explicit signal instead, closing that visibility gap.
  - Idempotent: a sub-lot already past ASSIGNED returns 409, not an error
    on retry.
  - The order's own IN_PRODUCTION label flips on whichever sub-lot reaches
    this state first (same idempotency shape as the existing
    ALLOCATED -> IN_PRODUCTION transition below).
  - Deliberately one-directional in this version — no
    IN_PRODUCTION -> ASSIGNED revert action yet.
Errors: 404, 403 (wrong workshop), 409 (not ASSIGNED)
```

**POST /api/v1/orders/{order_id}/flag-defect**
```
Request: multipart/form-data { photo: File, defect_qty: int, description: str }

Response 202:
  { order_id, defect_qty: int, verification_status: "PENDING" }

Product rationale:
  The buyer receives ONE combined delivery and cannot know which workshop
  produced the defective units. They flag at the ORDER level with a count
  and a photo. The VerificationAgent attributes fault to the most probable
  sublot — using workshop history and the nature of the defect — then
  marks that sublot's status accordingly.

  If defect spans multiple workshops: system assigns proportionally by qty.
  Attribution method is stated plainly to buyer as "AI-assisted attribution."
```

**GET /api/v1/orders** (buyer-scoped)
```
Response 200:
  { orders: [{ order_id, status, item_type, total_qty, deadline, created_at }],
    total: int, page: int, page_size: int }

Query params: ?status=ALLOCATED&page=1&page_size=20
Auth: buyer token — returns only orders for this buyer
```

**GET /api/v1/workshops/{workshop_id}/sublots** (workshop-scoped)
```
Response 200:
  { sublots: [{ sublot_id, order_id, item_type, qty_assigned,
                delivered_qty, status, deadline }],
    total: int }

Auth: workshop token — 403 if token workshop_id ≠ path workshop_id
```

**DELETE /api/v1/orders/{order_id}**
```
Response 200:
  { order_id, status: "Cancelled" }

Rules:
  - Only allowed when internal status ∈ (PENDING, ALLOCATED)
  - Returns 409 ORDER_IN_PRODUCTION if any sublot is IN_PRODUCTION or later
  - On cancel: order status → CANCELLED, ASSIGNED sublots deleted,
    workshop_capacity.available_qty restored for each affected workshop
Auth: buyer token only
```

**GET /health**
```
Response 200: { status: "ok", db: "ok", kafka: "ok" }
Response 503: { status: "degraded", db: "ok", kafka: "error" }
```

---

## 6. Sequence Diagrams

### 6.1 Order Placement and Allocation

```
Buyer          API             Kafka            AllocationWorker      DB
  │                │               │                    │              │
  │─POST /orders──►│               │                    │              │
  │                │─validate──────┤                    │              │
  │                │─INSERT order──┼────────────────────┼─────────────►│ (status=PENDING)
  │                │─publish───────►order.placed         │              │
  │◄──202 Accepted─│               │                    │              │
  │                │               │──consume────────────►              │
  │                │               │                    │─UPDATE order──►│ (PENDING→ALLOCATING)
  │                │               │                    │─fetch bids───►│ (effective_qty = avail - reserved)
  │                │               │                    │◄─bids─────────│
  │                │               │                    │─allocate()    │
  │                │               │                    │  [MIP solver] │
  │                │               │                    │─INSERT sublots►│
  │                │               │                    │─UPDATE reserved_qty per workshop►│
  │                │               │                    │─UPDATE order──►│ (ALLOCATING→ALLOCATED)
```

### 6.2 Delivery and Verification

```
Workshop       API             Kafka          VerificationWorker   VerificationAgent    DB
  │              │               │                   │                    │              │
  │─POST delivery►│               │                   │                    │              │
  │              │─UPDATE sublot─┼───────────────────┼────────────────────┼─────────────►│
  │              │─publish───────►sublot.delivered    │                    │              │
  │◄─202 Accepted─│               │                   │                    │              │
  │              │               │──consume───────────►                    │              │
  │              │               │                   │─agent.verify()──────►              │
  │              │               │                   │                    │─get_order_spec►│
  │              │               │                   │                    │◄─spec──────────│
  │              │               │                   │                    │─get_ws_history►│
  │              │               │                   │                    │◄─history───────│
  │              │               │                   │                    │─[LLM reasons]  │
  │              │               │                   │◄─VerificationOutput─│              │
  │              │               │                   │─INSERT verification─┼─────────────►│
  │              │               │                   │─compute_score()     │              │
  │              │               │                   │─UPDATE trust_score──┼─────────────►│
  │              │               │                   │─coordinator.on_verification_complete()
  │              │               │                   │  ┌─────────────────────────────────┐│
  │              │               │                   │  │ all sublots terminal?            ││
  │              │               │                   │  │  NO  → wait for remaining        ││
  │              │               │                   │  │  YES → _settle(order_id)         ││
  │              │               │                   │  │    → SettlementCalculator.compute ││
  │              │               │                   │  │    → INSERT payments             ││
  │              │               │                   │  │    → UPDATE order CLOSED         ││
  │              │               │                   │  └─────────────────────────────────┘│
```

**Note:** Settlement runs ONCE after ALL sublots reach terminal state. The coordinator checks
`all_terminal(order_id)` after every verification — if any sublot is still pending, settlement
is deferred. This prevents race conditions when multiple sublots are verified concurrently.

### 6.3 Factory Fallback

```
AllocationWorker
  │
  │─allocate(order, bids)
  │  MIP solver runs
  │  x_factory > 0 (unmet demand)
  │
  │─INSERT sublot (workshop_id = FACTORY_ID, qty = x_factory)
  │─INSERT sublot (workshop_id = w1, qty = x[w1])
  │  ...
  │─UPDATE order status = ALLOCATED
```

---

## 7. Event Design

### 7.1 Kafka Topics

| Topic | Producer | Consumer | Trigger |
|---|---|---|---|
| `saathi.order.placed` | POST /orders | AllocationWorker | Order created |
| `saathi.sublot.delivered` | POST /delivery | VerificationWorker | Workshop marks delivery |

### 7.2 Message Schemas

**saathi.order.placed**
```json
{
  "version": "1.0",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "order_id": 42,
  "total_qty": 10000,
  "deadline": "2026-08-30",
  "quality_min": 3,
  "item_type": "kurta"
}
```

**saathi.sublot.delivered**
```json
{
  "version": "1.0",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublot_id": 7,
  "order_id": 42,
  "workshop_id": 3,
  "delivered_qty": 500,
  "defect_photo_url": "/uploads/7.jpg"
}
```

### 7.3 Consumer Idempotency

Both consumers check existence before processing:
- AllocationWorker: `SELECT COUNT(*) FROM sublots WHERE order_id = $1` — skip if > 0
- VerificationWorker: `UNIQUE (sublot_id)` on verification_results — DB rejects duplicate insert

Manual Kafka offset commit — only after successful DB write.

---

## 8. Non-Functional Design

### 8.1 Reliability

**Kafka dual-write problem:** The API writes to DB first, then publishes to Kafka. If Kafka publish fails after DB write, the order is written but never allocated. Recovery: a background reconciliation job (v0: manual, v1: automated) checks for orders stuck in PENDING > 60s and republishes the event.



| Risk | Design Decision |
|---|---|
| Kafka redelivers message | UNIQUE constraints enforce idempotency at DB layer |
| Anthropic API times out | Retry 3× with exponential backoff (2s, 4s, 8s) |
| MIP solver returns non-optimal | Log + fall back to factory for full order |
| DB connection drops | asyncpg connection pool with min=5, max=20 |
| Concurrent verifications race on settlement | `all_terminal()` + `UNIQUE(order_id, workshop_id)` on payments = safe: second settlement run is a no-op via ON CONFLICT DO NOTHING |

### 8.2 Observability

Every log line for an order operation includes `order_id`. Every log line for a workshop operation includes `workshop_id`. JSON-structured logs — no plain print() statements.

A `correlation_id` (UUID) is generated at `POST /orders` and passed through:
- Written to `orders.correlation_id` column
- Included in every Kafka message
- Included in every log line for that order's lifecycle
- Returned in API responses as `X-Correlation-ID` header

This means any order's complete lifecycle — from HTTP request through Kafka through worker through DB — can be traced with a single grep on the `correlation_id`.

### 8.3 Performance Targets (demo requirements)

| Operation | Target | Basis |
|---|---|---|
| POST /orders response | < 200ms | API writes order + publishes Kafka only |
| Allocation (MIP) | < 5s | CBC solver, 20–30 workshops, ~30 variables |
| Verification (LLM) | < 30s | Claude vision + 2 tool calls |
| GET /orders/{id} | < 100ms | Single DB query with index |

### 8.4 Data Integrity

| Rule | Enforcement |
|---|---|
| Trust events are never updated or deleted | No UPDATE/DELETE grants on trust_events table |
| Settlement uses price at allocation time | cost_per_unit snapshot in sublots, not a JOIN to current capacity |
| Money calculations are exact | NUMERIC(12,2) in DB, Decimal in Python — never float |
| Workshop sees only own data | WHERE workshop_id = $1 on all workshop-facing queries |

---

## 9. What Is Explicitly Out of Scope (v0)

### Technical

| Feature | Decision | Reason |
|---|---|---|
| Cost field encryption | Plaintext, labelled in README | Field-level encryption is v2; honest about it |
| Live payment rails | Settlement math only | UPI/escrow integration not needed for prototype |
| Logistics routing | Same-district grouping | Full vehicle routing is post-pilot scope |
| Full renegotiation loop | Factory fallback only | Agreed with mentor — out of scope for v0 |
| ML trust scoring | Rule-based formula | More defensible under questioning; no training data |
| Automated deadline enforcement | Manual admin trigger | v1: scheduled job; v0 demo does not need it |

### Product

| Feature | Decision | Reason |
|---|---|---|
| Price quote before order commitment | Buyer gets quote at ALLOCATED state (not pre-order) | True pre-order quoting requires running allocation without committing — two-phase design for v1 |
| Appeal / dispute resolution for SPEC_AMBIGUITY | Flagged as NEEDS_HUMAN_REVIEW with SPEC_AMBIGUITY counter | Full arbitration workflow is post-pilot |
| Workshop onboarding flow | Seeded for demo | Owner submits form, admin reviews — no product risk, deferred to v1 |
| Push notifications (webhook / SSE) | Client polls GET /orders/{id} | Polling is sufficient for prototype demo; SSE for v1 |
| Workshop security deposit / penalty collection | Penalty computed, absorption logged | Real collection needs escrow account — labelled honestly in README |
| Cold start fast-track for new workshops | Cold start = 0.500 (grade C) by design | Onboarding trust grant mechanism is v1 |

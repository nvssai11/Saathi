# Saathi

**One supplier to work with. A whole consortium behind it.**

Saathi is a coordination layer that lets a cluster of small SFURTI workshops jointly fulfil large orders they could never individually win — while presenting the buyer with one accountable supplier, not twenty unfamiliar ones.

Built for Meesho's **ScriptedBy Her 2.0** hackathon — theme: *Building for Bharat using Agentic AI*.

---

## The problem

20 nearby workshops can have the combined capacity to fill a 10,000-unit order but no buyer will trust 20 separate, unknown suppliers to coordinate a single shipment. So the order defaults to one large factory — not because it makes a better product, but because it's the only party a buyer can hold accountable. Saathi is the neutral coordinator in between: it splits the order across verified workshops, tracks each sub-lot to delivery, verifies quality, and settles payment — all behind a single buyer-facing identity.

This isn't hypothetical at Meesho's own scale. Meesho's IPO filing (RHP, Dec 2025) names quality-driven seller loss as a real, disclosed business risk: its seller base — 706,471 annual transacting sellers as of Sep 2025, overwhelmingly small manufacturers selling direct, with no wholesaler in between — can shrink from "delisting of sellers, or removal of products due to quality issues." It already happened at scale: **200,000+ products delisted in a single quarter (Sep–Nov 2023)** for poor quality. With 72% of orders on COD, a bad-quality item isn't caught until after it's shipped, refused, and returned — the cost lands after the fact, in a blunt one-shot sweep, not as an ongoing signal a seller can act on. Saathi's mechanism — verify before it becomes a return, score continuously instead of delisting in a sweep — applies just as directly to that seller base as it does to a SFURTI cluster.

## What's actually agentic here

Most of Saathi is deliberately **not** AI. Order allocation, trust scoring, and settlement move real money and have to be reproducible — a wrong number has to be traceable to a fixed formula, not a model's mood. So those are plain, deterministic, fully unit-tested Python.

The one place that genuinely needs judgment an "if" statement can't provide: **does this defect photo actually match what was ordered, given this workshop's track record?** That's `VerificationAgent` — the only LLM component in the system, and it's a real agent, not a single API call wearing an agent's name:

- **Perceives** — takes the actual defect photo as input.
- **Reasons with tools it chooses itself** — decides which of `get_order_spec`, `get_workshop_history`, and `get_reference_image` to call, and in what order. Nothing in the code calls these directly; the model's own response drives which ones run. This is enforced, not just requested — a verdict submitted before the required context is gathered gets rejected with an explanatory error the model can recover from, not silently accepted.
- **Acts, with real consequence** — its verdict updates a workshop's trust score and settlement payout directly.
- **Knows its limits** — a defect call below 90% confidence never touches a workshop's trust score or pay on its own. It's routed to a human instead.

Why this matters for Bharat specifically: a single factory has one on-site QC team. A cluster of 20 fragmented workshops doesn't have that luxury — sending a human inspector to every site doesn't scale the way it does for one factory floor. Remote, vision-based verification is what makes trust-at-scale possible across that geography. And because these are small businesses on thin margins, the agent is bounded on purpose: full autonomy to *reason and verify*, zero autonomy to *decide a payout alone*.

## Architecture

```
Buyer places order  →  MIP allocation engine splits it across trust-weighted,
                        eligible workshops (or a factory backstop)
                     →  Workshops mark production → deliver
                     →  Defect flagged?  →  VerificationAgent inspects,
                        reasons over live tool calls, returns a verdict
                     →  Trust score updates (recency-weighted formula)
                     →  Settlement runs once, after every sub-lot reaches
                        a terminal state — buyer billed only for what
                        was actually delivered
```

| Layer | What it is |
|---|---|
| `core/` | Pure Python — allocation (MIP via PuLP/CBC), trust scoring, settlement math. Zero framework imports, zero I/O. |
| `agents/verification/` | The one LLM component — Gemini-based tool-use loop. |
| `services/coordinator.py` | The order state machine (FSM) — PENDING → ALLOCATING → ... → CLOSED. |
| `api/` | FastAPI routes for buyers, workshops, and admin/ops. |
| `db/repositories/` | All SQL lives here, nowhere else. |
| `workers/` | Kafka consumers (allocation, verification, notifications) + a periodic auto-verify sweep. |
| `frontend/` | React — buyer shop/order flow, workshop capacity/sublot/trust screens, admin review queue. |

**Privacy by design:** workshop identity never reaches a buyer-facing response, anywhere. Buyers see aggregate order progress; defect flags are order-level, not sub-lot-level.

## Tech stack

| | |
|---|---|
| Backend | Python 3.12, FastAPI |
| Allocation | PuLP + CBC (Mixed Integer Program) |
| Database | PostgreSQL 16, asyncpg, append-only trust/payment ledgers |
| Event bus | Kafka (aiokafka) |
| Agent | Google Gen AI SDK, `gemini-flash-lite-latest` — vision + tool use |
| Frontend | React + TypeScript, Vite |
| Money | `Decimal` everywhere, `NUMERIC` in Postgres — never `float` |

## Running it

**Docker Compose (full stack):**

```bash
cp .env.example .env   # fill in GEMINI_API_KEY
docker compose up --build
```

API comes up on `:8000` once Postgres and Kafka both report healthy (`GET /health` for a live check). Frontend runs separately:

```bash
cd frontend
npm install
npm run dev
```

**Backend tests** (no DB or Kafka needed — pure unit tests):

```bash
python -m pytest tests/unit/ -v
```

259 tests, covering the allocation engine's edge cases (factory fallback, rounding drift, zero eligible bids), the trust formula, every settlement penalty rule, the verification agent's retry/validation/tool-sequencing behaviour, and the coordinator's state machine.

**Integration tests** (needs a real Postgres — `docker compose up postgres`):

```bash
python -m pytest tests/integration/ -v
```

## Reference

Allocation formulation adapted from Chauhan et al., *"Real-time large-scale supplier order assignments across two-tiers of a supply chain,"* Computers & Industrial Engineering 176 (2023).

---

**Team:** N. Varshitha Sri Sai — Sutradhar

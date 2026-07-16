-- Saathi seed data — 6 SFURTI workshops + 1 factory fallback.
-- Designed for the live hackathon demo: order qty 200, quality_min 2, deadline 30 days.
-- The allocation engine should split across WS-1..WS-3; WS-4 has too-low trust
-- (below 0.30 threshold), WS-5 too-low quality, WS-6 misses deadline.

TRUNCATE trust_score_cache, payments, verification_results, sublots, trust_events,
         orders, workshop_capacity, workshops RESTART IDENTITY CASCADE;

-- ── Workshops ─────────────────────────────────────────────────────────────────

INSERT INTO workshops (workshop_id, name, quality_tier, is_factory, spec_disputes) VALUES
    (1,  'Pune Textile Cluster A',     4, FALSE, 0),
    (2,  'Nagpur Weaving Unit',         3, FALSE, 0),
    (3,  'Nashik Finishing Co.',        3, FALSE, 1),
    (4,  'Aurangabad Stitching Ltd.',   2, FALSE, 0),   -- trust too low after bad history
    (5,  'Kolhapur Fabric Works',       1, FALSE, 0),   -- quality_tier 1 < quality_min 2
    (6,  'Solapur Embroidery House',    3, FALSE, 0),   -- lead_time = 45 > deadline
    (99, 'Central Factory (Fallback)', 5, TRUE,  0);

SELECT setval('workshops_workshop_id_seq', 99);

-- ── Capacity ─────────────────────────────────────────────────────────────────
-- Covers every product_type in the frontend's static catalog
-- (frontend/src/data/catalog.ts) — the buyer shop UI is a real multi-category
-- storefront, not a single-SKU demo, so every listing a buyer can click
-- through to must actually be orderable end to end.
--
-- cost_per_unit is quality_tier-correlated per real workshop (added
-- 2026-07-17): WS5 (tier 1) cheapest, WS4 (tier 2) next, WS2/WS3/WS6
-- (tier 3) the middle band, WS1 (tier 4) priciest of the real workshops —
-- a rank-preserving reassignment of each product's original six prices, not
-- new numbers, so every product's price range/order-of-magnitude and every
-- workshop's available_qty/lead_time_days are unchanged; only which real
-- workshop gets which of the six prices moved. Before this, cost had no
-- relationship to quality_tier at all (WS3, tier 3, was pricier than WS1,
-- tier 4) — AllocationEngine's MIP is a pure cost-minimizer within the
-- eligible set, so quality_min already narrowed *which* workshops qualify,
-- but a buyer paying for higher quality within an already-eligible pool
-- wasn't reliably paying more for it. Factory (workshop 99, tier 5) is left
-- untouched — it's deliberately the expensive no-coordination fallback, not
-- part of this correlation.
--
-- At the default demo order (quality_min 2, 21-day+ deadline), eligibility
-- is unchanged — still WS1/WS2/WS3 only, same as before (WS4 trust-filtered,
-- WS5 quality-filtered, WS6 deadline-filtered) — but the *split proportions*
-- between WS1/WS2/WS3 shift: WS1 (tier 4, highest trust) is now the most
-- expensive of the three instead of a mid-priced option, so it wins a
-- smaller share of the MIP's cost-minimized allocation than before.

INSERT INTO workshop_capacity (workshop_id, product_type, available_qty, reserved_qty, cost_per_unit, lead_time_days) VALUES

    (5,  'jute-tote-bag',  90, 0,  30.95, 10),
    (4,  'jute-tote-bag',  90, 0,  33.16, 10),
    (2,  'jute-tote-bag',  45, 0,  35.37, 18),
    (3,  'jute-tote-bag',  35, 0,  38.91, 12),
    (6,  'jute-tote-bag',  90, 0,  42.00, 45),
    (1,  'jute-tote-bag',  55, 0,  45.09, 14),
    (99, 'jute-tote-bag', 9999, 0, 64.00,  7),

    (5,  'cotton-tote-bag',  90, 0,  28.00, 10),
    (4,  'cotton-tote-bag',  90, 0,  30.00, 10),
    (2,  'cotton-tote-bag',  45, 0,  32.00, 18),
    (3,  'cotton-tote-bag',  35, 0,  35.20, 12),
    (6,  'cotton-tote-bag',  90, 0,  38.00, 45),
    (1,  'cotton-tote-bag',  55, 0,  40.80, 14),
    (99, 'cotton-tote-bag', 9999, 0, 55.00,  7),

    (5,  'khadi-scarf',  45, 0,  47.89, 10),
    (4,  'khadi-scarf',  45, 0,  51.32, 10),
    (2,  'khadi-scarf',  22, 0,  54.74, 18),
    (3,  'khadi-scarf',  18, 0,  60.21, 12),
    (6,  'khadi-scarf',  45, 0,  65.00, 45),
    (1,  'khadi-scarf',  28, 0,  69.79, 14),
    (99, 'khadi-scarf', 9999, 0, 98.00,  7),

    (5,  'bamboo-basket',  45, 0,  62.63, 10),
    (4,  'bamboo-basket',  45, 0,  67.11, 10),
    (2,  'bamboo-basket',  22, 0,  71.58, 18),
    (3,  'bamboo-basket',  18, 0,  78.74, 12),
    (6,  'bamboo-basket',  45, 0,  85.00, 45),
    (1,  'bamboo-basket',  28, 0,  91.26, 14),
    (99, 'bamboo-basket', 9999, 0, 120.00,  7),

    (5,  'terracotta-pot',  90, 0,  40.53, 10),
    (4,  'terracotta-pot',  90, 0,  43.42, 10),
    (2,  'terracotta-pot',  45, 0,  46.32, 18),
    (3,  'terracotta-pot',  35, 0,  50.95, 12),
    (6,  'terracotta-pot',  90, 0,  55.00, 45),
    (1,  'terracotta-pot',  55, 0,  59.05, 14),
    (99, 'terracotta-pot', 9999, 0, 80.00,  7),

    (5,  'block-print-cushion',  90, 0,  35.37, 10),
    (4,  'block-print-cushion',  90, 0,  37.89, 10),
    (2,  'block-print-cushion',  45, 0,  40.42, 18),
    (3,  'block-print-cushion',  35, 0,  44.46, 12),
    (6,  'block-print-cushion',  90, 0,  48.00, 45),
    (1,  'block-print-cushion',  55, 0,  51.54, 14),
    (99, 'block-print-cushion', 9999, 0, 72.00,  7),

    (5,  'handloom-stole',  45, 0,  88.42, 10),
    (4,  'handloom-stole',  45, 0,  94.74, 10),
    (2,  'handloom-stole',  22, 0, 101.05, 18),
    (3,  'handloom-stole',  18, 0, 111.16, 12),
    (6,  'handloom-stole',  45, 0, 120.00, 45),
    (1,  'handloom-stole',  28, 0, 128.84, 14),
    (99, 'handloom-stole', 9999, 0, 175.00,  7),

    (5,  'jute-door-mat', 135, 0,  22.11, 10),
    (4,  'jute-door-mat', 135, 0,  23.68, 10),
    (2,  'jute-door-mat',  68, 0,  25.26, 18),
    (3,  'jute-door-mat',  52, 0,  27.79, 12),
    (6,  'jute-door-mat', 135, 0,  30.00, 45),
    (1,  'jute-door-mat',  82, 0,  32.21, 14),
    (99, 'jute-door-mat', 9999, 0, 46.00,  7);

-- ── Trust score cache (pre-seeded; reflect 8-event history for active workshops) ─

INSERT INTO trust_score_cache (workshop_id, score, grade, computed_at) VALUES
    (1,  0.910, 'A', now()),
    (2,  0.780, 'B', now()),
    (3,  0.720, 'B', now()),
    (4,  0.250, 'D', now()),   -- below 0.30 threshold — excluded by filter
    (5,  0.800, 'B', now()),   -- excluded by quality, not trust
    (6,  0.850, 'A', now()),   -- excluded by deadline, not trust
    (99, 1.000, 'A', now());   -- factory always trusted

TRUNCATE trust_score_cache, payments, verification_results, sublots, trust_events,
         orders, workshop_capacity, workshops RESTART IDENTITY CASCADE;

INSERT INTO workshops (workshop_id, name, quality_tier, is_factory, spec_disputes, phone_number) VALUES
    (1,  'Pune Textile Cluster A',     4, FALSE, 0, '+919810000001'),
    (2,  'Nagpur Weaving Unit',         3, FALSE, 0, '+919810000002'),
    (3,  'Nashik Finishing Co.',        3, FALSE, 1, '+919810000003'),
    (4,  'Aurangabad Stitching Ltd.',   2, FALSE, 0, '+919810000004'),
    (5,  'Kolhapur Fabric Works',       1, FALSE, 0, '+919810000005'),
    (6,  'Solapur Embroidery House',    3, FALSE, 0, '+919810000006'),
    (99, 'Central Factory (Fallback)', 5, TRUE,  0, NULL);

SELECT setval('workshops_workshop_id_seq', 99);

INSERT INTO workshop_capacity (workshop_id, product_type, available_qty, reserved_qty, cost_per_unit, lead_time_days) VALUES

    (5,  'jute-tote-bag',  90, 0,  30.95, 10),
    (4,  'jute-tote-bag',  90, 0,  33.16, 10),
    (2,  'jute-tote-bag',  90, 0,  35.37, 18),
    (3,  'jute-tote-bag',  70, 0,  38.91, 12),
    (6,  'jute-tote-bag',  90, 0,  42.00, 45),
    (1,  'jute-tote-bag',  110, 0,  45.09, 14),
    (99, 'jute-tote-bag', 9999, 0, 64.00,  7),

    (5,  'cotton-tote-bag',  90, 0,  28.00, 10),
    (4,  'cotton-tote-bag',  90, 0,  30.00, 10),
    (2,  'cotton-tote-bag',  90, 0,  32.00, 18),
    (3,  'cotton-tote-bag',  70, 0,  35.20, 12),
    (6,  'cotton-tote-bag',  90, 0,  38.00, 45),
    (1,  'cotton-tote-bag',  110, 0,  40.80, 14),
    (99, 'cotton-tote-bag', 9999, 0, 55.00,  7),

    (5,  'khadi-scarf',  45, 0,  47.89, 10),
    (4,  'khadi-scarf',  45, 0,  51.32, 10),
    (2,  'khadi-scarf',  44, 0,  54.74, 18),
    (3,  'khadi-scarf',  36, 0,  60.21, 12),
    (6,  'khadi-scarf',  45, 0,  65.00, 45),
    (1,  'khadi-scarf',  56, 0,  69.79, 14),
    (99, 'khadi-scarf', 9999, 0, 98.00,  7),

    (5,  'bamboo-basket',  45, 0,  62.63, 10),
    (4,  'bamboo-basket',  45, 0,  67.11, 10),
    (2,  'bamboo-basket',  44, 0,  71.58, 18),
    (3,  'bamboo-basket',  36, 0,  78.74, 12),
    (6,  'bamboo-basket',  45, 0,  85.00, 45),
    (1,  'bamboo-basket',  56, 0,  91.26, 14),
    (99, 'bamboo-basket', 9999, 0, 120.00,  7),

    (5,  'terracotta-pot',  90, 0,  40.53, 10),
    (4,  'terracotta-pot',  90, 0,  43.42, 10),
    (2,  'terracotta-pot',  90, 0,  46.32, 18),
    (3,  'terracotta-pot',  70, 0,  50.95, 12),
    (6,  'terracotta-pot',  90, 0,  55.00, 45),
    (1,  'terracotta-pot',  110, 0,  59.05, 14),
    (99, 'terracotta-pot', 9999, 0, 80.00,  7),

    (5,  'block-print-cushion',  90, 0,  35.37, 10),
    (4,  'block-print-cushion',  90, 0,  37.89, 10),
    (2,  'block-print-cushion',  90, 0,  40.42, 18),
    (3,  'block-print-cushion',  70, 0,  44.46, 12),
    (6,  'block-print-cushion',  90, 0,  48.00, 45),
    (1,  'block-print-cushion',  110, 0,  51.54, 14),
    (99, 'block-print-cushion', 9999, 0, 72.00,  7),

    (5,  'handloom-stole',  45, 0,  88.42, 10),
    (4,  'handloom-stole',  45, 0,  94.74, 10),
    (2,  'handloom-stole',  44, 0, 101.05, 18),
    (3,  'handloom-stole',  36, 0, 111.16, 12),
    (6,  'handloom-stole',  45, 0, 120.00, 45),
    (1,  'handloom-stole',  56, 0, 128.84, 14),
    (99, 'handloom-stole', 9999, 0, 175.00,  7),

    (5,  'jute-door-mat', 135, 0,  22.11, 10),
    (4,  'jute-door-mat', 135, 0,  23.68, 10),
    (2,  'jute-door-mat',  136, 0,  25.26, 18),
    (3,  'jute-door-mat',  104, 0,  27.79, 12),
    (6,  'jute-door-mat', 135, 0,  30.00, 45),
    (1,  'jute-door-mat',  164, 0,  32.21, 14),
    (99, 'jute-door-mat', 9999, 0, 46.00,  7);

INSERT INTO trust_score_cache (workshop_id, score, grade, computed_at) VALUES
    (1,  0.910, 'A', now()),
    (2,  0.780, 'B', now()),
    (3,  0.720, 'B', now()),
    (4,  0.250, 'D', now()),
    (5,  0.800, 'B', now()),
    (6,  0.850, 'A', now()),
    (99, 1.000, 'A', now());

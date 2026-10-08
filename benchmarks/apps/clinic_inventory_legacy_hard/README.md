# Clinic Inventory Legacy Hard — C3 benchmark

**Purpose:** deliberately challenging but runnable **framework-free procedural PHP + MySQL** benchmark for C3 data-layer migration. This is an original synthetic benchmark, **not** an external real-world GitHub application. It is designed to test SQL discovery, SQL reconstruction, ORM schema fidelity, safe parameterization, and appropriate abstention before running Hospital Management.

## Contents
- `legacy/*.php` — 22 numbered SQL execution scenarios plus connection and helper files; uses `mysqli` functions and no PHP framework.
- `database/schema.sql` — MySQL schema with 5 related tables, primary keys, foreign keys, decimal, datetime, nullability, uniqueness.
- `database/seed.sql` — deterministic sample records for an isolated test database.
- `ground_truth/site_inventory.csv` — manually authored scenario labels and expected treatment **at the research-scope level**; do not treat as validated predictions of your C3 implementation.
- `RUN_C3.md` — exact CLI and safe SQL setup instructions.

## Important caveats
- The source intentionally includes **injection-prone legacy interpolation**. Only run locally against disposable data, not with production credentials or on a publicly accessible PHP server.
- Because the fixtures include database writes, **do not indiscriminately execute all PHP files against a shared database**. Only the `C3` static pipeline run is necessary for initial evaluation.
- `schema.sql` is DDL, not a SQLite `.db` file. Your existing C3 CLI expects a `.sql` schema file. MySQL data must be created with the SQL scripts.
- Some query sites are deliberately outside current C3's conservative single-table CRUD scope. Successful migration is **not** 100% conversion; safe abstention is a valid outcome.
- `ground_truth` is a review guide to independently annotate and audit; actual correctness and metrics must be measured after execution.

# C3 - Data Layer Migration: Integration Handoff

## 1. Component Overview

C3 converts supported database operations from legacy procedural PHP into SQLAlchemy-based Python data-access code.

It also produces structured JSON reports for migration monitoring and manual review.

## 2. Required Inputs

- Legacy PHP source directory
- Existing MySQL schema.sql
- Output directory

## 3. Running C3

Working directory:
components/c3_data_layer

Command:

python -m c3.cli run --php <php_directory> --schema <schema_file> --output <output_directory>

Example:

python -m c3.cli run --php ../../benchmarks/apps/simple_crud/legacy --schema ../../benchmarks/apps/simple_crud/schema.sql --output ../../output/simple_crud

Requirements:
- Python environment with C3 dependencies installed
- PHP executable available on PATH
- C3 src directory available on PYTHONPATH

## 4. Backend Integration

Generated files:
- models.py: SQLAlchemy model definitions
- data_access.py: SQLAlchemy database-access functions
- schema.json: Parsed database schema
- converted_queries.json: Query conversion metadata
- migration_notes.md: Schema features not migrated automatically (unsupported column types, tables without a primary key)

The backend should:
1. Invoke the C3 pipeline.
2. Read the generated artifacts.
3. Expose migration results through its API.
4. Treat generated Python as untrusted output requiring review and validation before execution.

## 5. Frontend Integration

The frontend can display these reports through backend API endpoints:

- summary.json: Migration statistics
- queries.json: Recovered SQL and recovery status
- converted_queries.json: Conversion status and reasons
- unresolved_queries.json: Manual-review items
- column_usage.json: Column usage analysis
- schema.json: Parsed database structure

Frontend pages may include:
- Migration summary dashboard
- Recovered queries table
- Unresolved queries table
- Generated files view

## 6. Important Limitations

- Only supported SQL patterns are converted; everything else is UNRESOLVED with file, line, reason code and reason.
- Static INNER JOIN SELECTs are converted; LEFT/RIGHT JOINs and dynamic JOINs are not.
- Generated data access is parameterized SQLAlchemy (`text()` + bind parameters), not ORM query constructs.
- An existing MySQL schema (`schema.sql` / dump) is required.
- Tables without a primary key are generated as SQLAlchemy Core `Table` objects, not ORM classes.
- Behavioural equivalence on MySQL has been shown for the Clinic fixtures only (61 tests, 2026-10-15).
- Generated files should not be executed automatically without review.

## 7. Integration Contract

contracts/c3_data_layer_contract.json

## 8. Research Validation

Current results (2026-10-15; see `docs/C3_EVALUATION_RESULTS.md` and `evidence/evidence_register.md`). Each entry is total / recovered / converted:

- simple_crud: 4 / 4 / 4
- mixed_crud: 6 / 5 / 5
- hard_crud: 5 / 2 / 2
- Clinic (development): 22 / 17 / 21. 14 are converted by general rules and 7 by guarded query-specific templates.
- Helpdesk holdout: 9 / 7 / 6 pre-adaptation (frozen); 9 / 9 / 8 post-adaptation
- Unseen third-party apps, post-adaptation: HMS 44 / 40 / 40, NEWS-BUZZ 32 / 30 / 29, Fee-Rainbow 22 / 20 / 19

These benchmarks demonstrate supported behaviour and conservative handling of unsupported cases. They do not establish real-world migration accuracy.

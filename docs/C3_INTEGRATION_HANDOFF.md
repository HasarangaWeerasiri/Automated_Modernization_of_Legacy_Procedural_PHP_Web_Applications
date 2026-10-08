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

- Only supported SQL patterns are converted.
- Dynamic SQL may be marked UNRESOLVED.
- JOIN conversion is currently unsupported.
- An existing MySQL schema is required.
- Generated code has not been validated against a production database.
- Generated files should not be executed automatically without review.

## 7. Integration Contract

contracts/c3_data_layer_contract.json

## 8. Research Validation

Controlled benchmarks:
- simple_crud: 4 recovered, 4 converted
- mixed_crud: 5 recovered, 5 converted, 1 unresolved
- hard_crud: 2 recovered, 1 converted, 3 recovery-unresolved

The benchmarks demonstrate supported behavior and conservative handling of unsupported cases. They do not establish real-world migration accuracy.

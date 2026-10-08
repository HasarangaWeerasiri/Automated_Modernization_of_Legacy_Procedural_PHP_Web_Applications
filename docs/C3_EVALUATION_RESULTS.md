# C3 – Data Layer Migration: Evaluation Results

## 1. Introduction

Component 3 (C3) of the Automated Modernization of Legacy Procedural PHP Web Applications research project focuses on migrating legacy procedural PHP database operations into SQLAlchemy-based Python data-access functions.

The evaluation examined the component's ability to recover SQL queries, convert supported queries into parameterized SQLAlchemy functions, identify unsupported SQL patterns, and preserve observable database behavior.

## 2. Evaluation Methodology

Three benchmark applications were evaluated:

- **Simple CRUD:** Basic INSERT, SELECT, UPDATE, and DELETE operations.
- **Mixed CRUD:** SQL interpolation, concatenation, variable reassignment, and function-generated SQL.
- **Hard CRUD:** LIKE searches, dynamic ordering, dynamic IN lists, conditional SQL construction, and JOIN queries.

The evaluation used automated Python tests and MySQL 8.0 behavioral comparisons.

For database execution testing, the original procedural PHP implementation and the generated Python implementation were executed against separate MySQL validation databases populated with equivalent test data.

Their recorded query results and database states were compared using Python comparison scripts.

## 3. SQL Migration Results

| Benchmark | Total Queries | Recovered | Converted | Conversion Rate |
|---|---:|---:|---:|---:|
| Simple CRUD | 4 | 4 | 4 | 100% |
| Mixed CRUD | 6 | 5 | 5 | 83.3% |
| Hard CRUD | 5 | 2 | 1 | 20% |
| **Total** | **15** | **11** | **10** | **66.7%** |

Overall, C3 successfully converted 10 of the 15 benchmark queries.

Five queries could not be automatically converted and were retained for manual review rather than being transformed using unsupported assumptions.

## 4. MySQL Behavioral Validation

| Benchmark | Checks Passed | Checks Failed |
|---|---:|---:|
| Simple CRUD | 5 | 0 |
| Mixed CRUD | 7 | 0 |
| Hard CRUD | 6 | 0 |
| **Total** | **18** | **0** |

### 4.1 Simple CRUD

The original PHP and generated SQLAlchemy implementations completed CREATE, READ, UPDATE, and DELETE operations.

The test verified that the expected user record was inserted, retrieved, updated, and deleted. Both implementations ended with an empty users table.

**Result: 5/5 checks passed.**

### 4.2 Mixed CRUD

Five supported SQL queries were exercised against MySQL.

The test compared product retrieval, selection by identifier, database state after variable reassignment, product updates, deletion, and final database state.

The original PHP implementation did not expose the result of the reassignment SELECT operation. Therefore, this stage was validated through successful execution and database-state comparison rather than direct comparison of returned rows.

The sixth query was classified as requiring manual review.

**Result: 7/7 checks passed, including one manual-review marker check.**

### 4.3 Hard CRUD

The supported LIKE-search query was tested using five search terms:

- Ali
- Alex
- Bob
- Charlie
- xyz

The original PHP and generated Python implementations returned matching results for all five inputs.

The remaining four queries were verified as unresolved in the conversion output and were not executed through generated functions.

**Result: 6/6 checks passed, including one conversion-status check.**

## 5. Unsupported Query Handling

The following SQL patterns remained unresolved:

| Benchmark | Query | Reason |
|---|---|---|
| Mixed CRUD | Q006 | Unsupported dynamic function expression |
| Hard CRUD | Q002 | Dynamic structural ORDER BY expression |
| Hard CRUD | Q003 | Dynamic SQL IN-list fragment |
| Hard CRUD | Q004 | SQL construction dependent on conditional control flow |
| Hard CRUD | Q005 | JOIN requiring multiple referenced tables |

C3 did not generate data-access functions for these unsupported queries. This demonstrates conservative handling of the unsupported patterns covered by the benchmarks.

## 6. Automated Testing

The C3 automated Python test suite completed successfully.

**Result: 113 tests passed.**

These tests supplement the MySQL behavioral comparisons by checking individual component functions and integration-related behavior.

## 7. Limitations

The evaluation has several limitations:

1. Only three controlled benchmark applications were used.
2. Behavioral tests covered selected input values rather than all possible inputs.
3. The current converter does not automatically support all complex SQL structures.
4. The evaluation does not demonstrate successful modernization of an entire real-world PHP application.
5. The behavioral comparisons include some database-state checks and conversion-status checks rather than exclusively direct SQL result comparisons.
6. The MySQL validation results do not establish equivalence under every concurrency, transaction, error-handling, or security scenario.

## 8. Conclusion

The evaluation demonstrates that C3 can recover and convert supported procedural PHP database queries into executable SQLAlchemy data-access functions.

Across the three benchmarks, 10 of 15 queries were converted successfully, giving an overall conversion rate of 66.7%.

The automated test suite passed 113 tests, while the MySQL evaluation passed all 18 recorded checks.

These results provide preliminary evidence that supported SQL transformations can preserve observed behavior for the tested scenarios. Unsupported dynamic and multi-table query patterns were identified for manual review instead of being converted automatically.

Further evaluation on larger and more representative legacy PHP applications is required to assess generalizability and broader behavioral correctness.
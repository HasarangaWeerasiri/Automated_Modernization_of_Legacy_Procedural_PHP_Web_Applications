# Endpoints, response shape and contract (schema 1.0)

```
python -m c1.endpoints analysis-out --src <app> [--schema dump.sql]  # -> endpoints.json, endpoint-map.json
python -m c1.contract  analysis-out --title HMS                      # -> contract/openapi.json, backend/, flags.json
python -m c1 run <app> --out analysis-out --exclude-dir TCPDF --title HMS   # every stage
```

## SQL shape (`sqlshape.py`)

- The SQL template is rebuilt from the DFG: literals, `.`, `"…$x…"`, `.=` chains and `sprintf()` (WackoPicko). Each interpolated value becomes a placeholder `§n§` that keeps its origins.
- Read from the template: the statement kind, tables, selected columns, WHERE `column op value` terms, and INSERT/UPDATE written columns. A value may be wrapped, e.g. `SHA1('§1§')`.
- A placeholder in a structural position (table, column, clause) makes the query `dynamic` and it is flagged. This is C3's rule (master reference §3.3 step 6).
- C3 owns full SQL parsing. This is only the slice C1 needs; C3's parsed queries can replace it, joined by query node id.
- **Schema:** `CREATE TABLE` statements in the app's `.sql` dump. This minimal reader is replaced by C3's pluggable reader later. It gives column types and answers C2's `SELECT *` question (the response lists the used columns that exist in the schema).
- **Coverage:** HMS 44/44 queries resolved. WackoPicko 45/45 data queries resolved; the other 15 are BEGIN/COMMIT/ROLLBACK.

## Endpoint recovery (master reference steps 5, 6, 8)

| Legacy pattern | API |
|---|---|
| SELECT outside every form handler (page render) | `GET /api/{table}`, one per query (C2's per-query endpoints) |
| Key column (`id`, `*_id`, `pid` …) compared with request input | `GET /api/{table}/{col}` |
| WHERE compared with a session value | `GET /api/me/{table}` (`/by-{col}` when not a key column); the value comes from the token |
| Form handler with INSERT | `POST /api/{table}` |
| UPDATE / DELETE by key | `PUT` / `DELETE /api/{table}/{col}` |
| Handler that checks a password-like column and writes the session | `POST /api/auth/login` (returns a JWT) |

- **Form handler:** the outermost branch whose condition reads request input. Its operation is its first write. Later writes are listed in `alsoWrites`.
- **Queries through calls:** queries reached through called functions and methods count. A value that is the callee's parameter is traced back to the call-site argument along the call chain, e.g. `Pictures::get_picture($_GET['picid'])` → `picid` → `pictures.id`.
- **Response shape:** only the columns the page uses, from DFG origins `column:<query>:<name>` (outputs, conditions, copies), not the whole `SELECT *`. Types come from the schema.
- **Entry pages:** files never included, plus `action=`/`href=` targets. HMS posts its login form straight to `func.php`, which other pages also include.
- **Writes during render:** a write inside a helper called while a page renders (WackoPicko clears expired admin sessions) goes into `sideEffects` of that GET, not into a separate endpoint.

### Flags (never guessed)

- `mutating-get`: a legacy GET changes data. Mapped by SQL kind; the decision is pending with the supervisor.
- `no-guard-in-legacy`: public endpoint or missing check? Needs a decision.
- `inconsistent-guard`: some pages guard the data, some don't. The endpoint takes the stricter rule (survey: 46.7% prefer the conservative default).
- `sensitive-field`: HMS displays doctor and patient passwords.
- `dynamic-sql-structure`, `sql-ambiguous`, `update/delete-without-key`, `type-unknown`, `write-during-render`.

## `endpoint-map.json`

`format: "c1-endpoint-map/1"`. `endpoints[]` has the same structure as C4's `c4-stand-in/1`:
- `endpoint_id, method, path, records_at`;
- `sources[]` with `source_id`, `legacy{method, path, fixed, records}` and the source's own `parameters[]` (`name, in, legacy_name, legacy_in, column`).

C4 only needs to register the new format name for the same reader. For C2: `pages` (page → endpoint ids) and `queries` (query node id → endpoint id).

Fields added on 2026-10-22, after C4 checked the map against its 11 needs:

| C4 item | Field | Meaning |
|---|---|---|
| 6 | `sources[].legacy.records` | `"only"`, a table number, `"unknown"` (the page has other tables too) or `"none"` (the handler only writes). A search form that prints its rows is never `"none"`. |
| 9 | `sources[].constraints[]` and `parameters[].same_as` | A field the handler requires to equal another one (`if ($password == $cpassword)`). A generated request must send the same value in both. |
| 8 | `sources[].login` | For a login form: `userType` (the table its password is checked against), `fields`, `sets` (session keys and cookies written, including in called functions) and `redirects`. |
| 8 | `endpoints[].auth` | `required`, `userTypes` (the logins that set every key this endpoint needs) and `sessionKeys`. |
| 8 | top-level `login` | `endpoint`, `tokenAt: "access_token"`, `sendAs: "Authorization: Bearer <access_token>"`, and `userTypes` (pages, fields, keys, redirects). |
| 10 | `endpoints[].response_fields` | Field → `table.column`. Joins are resolved from `alias.column` in the select list, then from the schema; `null` when several joined tables have the column (flagged `column-ambiguous`). |
| — | `endpoints[].flags`, `sources[].code` | Flag names (e.g. `table-not-in-schema` for dead template code), and the source file and line. |

## Contract and backend

- `contract/openapi.json`: OpenAPI 3.1.
  - **Schemas:** per operation `…Body`, `…Item`, `…Response` (list wrapped as `{<table>: [...]}`, C4's `records_at`), plus `Status` and `Token`.
  - **Security:** `bearerAuth` (JWT) on every guarded or session-bound endpoint.
  - **`x-c1`:** each operation lists its legacy sources (page, line, query nodes), auth, session bindings and flags.
- `backend/app/`: FastAPI + Pydantic v2.
  - **Session → JWT** (step 9): a guard becomes `Depends(get_current_user)`; a session value becomes a token claim; `$_SESSION['role'] == 'admin'` becomes `require_claim('role', 'admin')`.
  - **`data_access.py`:** typed stubs for C3, each with the legacy SQL template and the mapping from parameter to legacy field to column.
  - **Test:** `tests/test_contract.py` boots the generated app. It checks that FastAPI serves the same paths/methods as `openapi.json`, that a guarded route returns 401 without a token, and that the token claim reaches the data layer.
- `flags.json`: one list of everything a human must decide (step 11).

## Results

| | HMS | WackoPicko |
|---|---|---|
| Endpoints | 19 (GET 10, POST 6, PUT 2, DELETE 1) | 25 (GET 12, POST 10, DELETE 3) |
| Login endpoint | 4 legacy login forms → `POST /api/auth/login` | user + admin login → `POST /api/auth/login` |
| Generated app boots, served contract = `openapi.json` | yes | yes |
| Items in `flags.json` | 45 | 62 |
| Whole pipeline byte-identical on re-run and from another folder | yes | yes |

Cross-check: our `POST /api/contact` mapping (`txtName`/`txtEmail`/`txtPhone`/`txtMsg` → `contact.*`) and the patient search (`patient_contact` → `patreg.contact`) are the same as in C4's hand-written stand-in map.

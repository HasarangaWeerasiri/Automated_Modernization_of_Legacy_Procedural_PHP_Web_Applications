# Component 2 — Stage 3 and Stage 4 Specification (v0.2)

| Item | Value |
|---|---|
| Status | Draft, owner-approved for implementation on mocks |
| Date | 8 October 2026 (v0.2: decisions after the first Stage 3–4 run) |
| Owner | Jayawardhana R D L L (IT23213876) |
| Covers | Stage 3 (data requirement recovery) and Stage 4 (contract reconciliation) |
| Novelty link | Stage 4 is the core contribution: it checks two **independently derived** artefacts (C2's recovered needs, C1's synthesised contract) against each other **before** the app is assembled |

---

## 1. Stage 3 — Data requirement recovery

**Input:** the Stage 2 component tree + the timeline. **Output:** for every component, the list of data it needs.

### 1.1 What counts as a need

For each output node in a component (including AttributeExpression nodes), take its `reads` and classify each one:

| `sourceKind` / `confidence` | Becomes | Goes to Stage 4? |
|---|---|---|
| `db_row_field`, `resolved` | **Field need** `{table, column, queryNodeId}` | Yes |
| `computed` | Follow `derivedFrom` recursively to the base reads, then classify those. Record the wrapper (e.g. `h()`, `number_format()`) on the need, for Stage 5 | Base reads only |
| `db_row_field`, `ambiguous` (e.g. `SELECT *`) | **Ambiguous need** — flag `ambiguous_needs_schema` (C3 schema join, not built yet) | Reported, not matched |
| `unresolved` (any) | **Unresolved need** — flag `unresolved_read` | Reported, not matched |
| `session` | **Context need** (becomes auth context / JWT claim) | Excluded, reason `session` |
| `request` | **Context need** (becomes a route or query parameter) | Excluded, reason `request` |
| `server` | Context need | Excluded, reason `server` |
| `literal` | Nothing | No |

Never guess a table or column. If a read has no `source`, it is unresolved.

### 1.2 Where needs live in the tree

| Component | Its need |
|---|---|
| **Item** | Fields of one row of the iterated query |
| **List** | The **collection**: the loop's iterated query, plus the Item's fields (`array of Item`) |
| Any other component | Union of the needs of its own output nodes |

Needs are de-duplicated per component (same table + column + query = one need), kept in a stable order (first appearance in sequence order). Each need lists **every** timeline node id and source line that referenced it.

### 1.2a Needs from abstained content

Output nodes inside an **Abstain** node (e.g. an authorization guard) still produce needs, owned by the
nearest component above. Each such need carries `fromAbstained: true`, and its Stage 4 result is reported
but marked the same way: the boundary around it was not decided, so the evidence is weaker.

### 1.2b Source lines

Every reference carries `file` and `line`. The line comes from the output node's `loc.startLine`
(same shape as Member 01's AST node envelope). If a node has no `loc`, `line` is `null` and is shown
as `?` — never guessed.

### 1.3 What Stage 3 does not do

- Condition reads (what an `if` tests) are not available without condition text (Member 01 Q2). Not recovered.
- Function-call output from other files (Member 01 Q4). Not recovered.

---

## 2. Stage 4 — Contract reconciliation

**Input:** Stage 3 needs + the OpenAPI contract + an endpoint map. **Output:** a reconciliation report per component.

### 2.1 Endpoint map

Which endpoint serves which page is not yet produced by C1 (Member 01 Q8). Until it is, a **substitute** file `mocks/endpoint_map.json` maps each timeline `entrypoint` to an endpoint path, method and the response schema to read fields from. It must be marked as a substitute in its `_provenance`. When C1's map arrives, only the loader changes.

A timeline with no entry in the map → every field need is `cannot_reconcile`, reason `no_endpoint`. Never guess an endpoint.

### 2.2 Matching

For each field need, look for a property with the **same name** as the column in the endpoint's response schema (for a List: the array item schema).

| Result | Meaning | Flag? |
|---|---|---|
| **matched** | Property found | No |
| **missing** | Need has no property in the contract | **Yes** — `missing_in_contract`, with file, line and reason (NFR4) |
| **unused** | Contract property not referenced by any **output** read | Info only. **Not proof the property is unused**: condition reads (e.g. `if ($row['userStatus']==1)`) are invisible until Member 01 Q2. Never use this list to remove fields from the contract |
| **excluded** | Context need (`session`, `request`, `server`) | No — reason recorded |
| **cannot_reconcile** | Ambiguous or unresolved need, or no endpoint | **Yes** — with its reason |

**Collection (List) result.** Each List's collection need gets its own result: `mapped` (to exactly one
array in the response schema) or `cannot_reconcile`. Reported as a separate count, not mixed with field needs.

**Extra `cannot_reconcile` reasons (approved):**

| Reason | When |
|---|---|
| `no_endpoint` | Timeline not in the endpoint map |
| `endpoint_not_in_contract` | The map points at a path, method or response the contract does not have |
| `nested_list` | A List inside another List's rows (no rule yet; matches R-L4 being design-only) |
| `list_not_mapped_to_array` | Not exactly one List and one array, so the array holding the rows cannot be decided |
| `ambiguous_needs_schema` | `ambiguous` read (e.g. `SELECT *`) |
| `unresolved_read` | `unresolved` read, or a read with no `source` |

**Exact name match only.** No fuzzy matching, no case folding, no renaming. A case-insensitive near match may be **reported as a hint** on a `missing` need, but the need stays `missing` (sound-or-abstain).

### 2.3 Type check

Only when both types are known (contract type and source column type from C3's schema). Until C3's schema is available, matched needs carry `type: unverified`. Never invent a type.

### 2.4 Output

- `output/stage3_<name>.json` and `output/stage4_<name>.json` (deterministic, stable order).
- Stage 4's report is the artefact passed to C4 (validation).
- One summary line, e.g. `[Stage 4] 11 needs: 10 matched, 1 missing, 0 excluded, 0 cannot reconcile · 1 collection mapped · 2 unused`.

---

## 3. Expected results on the current mocks

| Mock | Expected |
|---|---|
| list_while, list_foreach | Exactly one `missing`: **`contact`**. Identical results for both |
| detail | No `missing` |
| admin | No `missing`; `username` and `role` **excluded** (`session`) — never reported as missing |
| edge_cases | The `SELECT *` join read → `cannot_reconcile` (`ambiguous_needs_schema`); the unresolved read → `cannot_reconcile` (`unresolved_read`) |
| wp_* | Every field need `cannot_reconcile` (`unresolved_read`). **Zero** `missing` (no false alarms) |
| Every mock | `sku` never appears as a need |

---

## 4. Metrics this produces (for SO2 / SO3 and §3.4 of the proposal)

| Metric | From |
|---|---|
| Data requirement recovery (precision / recall per component) | Stage 3 needs vs manual ground truth |
| Reconciliation correctness (correct / missed / false mismatches) | Stage 4 `missing` vs manually verified mismatches |
| Flag rate | `missing` + `cannot_reconcile` + Stage 2 abstains, over all needs / constructs |

---

## 5. Honest limits of the current evidence

| Limit | What to say in a demo or viva |
|---|---|
| The `contact` gap was **planted** in a mock contract, and the endpoint map is a substitute | The mocks prove the reconciliation **logic** works. Evidence that it finds **real** synthesis errors needs C1's real contract and endpoint map |
| WackoPicko gives 0 reconcilable needs | Its data comes through class methods the core does not trace yet (Member 01 Q11) |
| "unused" is a lower bound on use | Condition reads are not visible yet (Member 01 Q2) |
| No types checked | Needs C3's schema |

## 6. Decision log entries (for Appendix E)

| Date | Decision | Reason |
|---|---|---|
| 8 Oct 2026 | Accept the agent's 4 extra `cannot_reconcile` reasons | Each names a distinct, checkable condition; none occurs on the mocks |
| 8 Oct 2026 | Collection needs get their own result (`mapped` / `cannot_reconcile`) | They are needs, but not field needs; mixing them would distort the field counts |
| 8 Oct 2026 | Needs from abstained content are kept, marked `fromAbstained` | The data is still rendered; hiding it would under-report needs |
| 8 Oct 2026 | "unused" is information only, never a reason to prune | Condition reads are invisible until Member 01 Q2 |
| 8 Oct 2026 | Source line from `loc.startLine` (Member 01's AST envelope shape); mocks carry it as an extension until the core does | NFR4 requires file and line |

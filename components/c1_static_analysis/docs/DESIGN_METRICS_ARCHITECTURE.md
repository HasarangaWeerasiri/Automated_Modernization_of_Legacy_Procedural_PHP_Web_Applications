# Measuring the migration, and suggesting a target architecture

Two stages added on 2026-10-21 after the supervisor meeting. They run after `contract`, read only what earlier stages wrote, and are deterministic.

## 1. `metrics.json`: what the migration changed

The supervisor asked each component for a factor that shows the migration succeeded. Component 1 reports two, before (legacy PHP) and after (the generated backend).

### Security: attack surface

Manadhata & Wing (IEEE TSE 2011) measure how much of a system an attacker can reach, through entry points and the data behind them. We count the same thing for the web entry points.

| Id | Measure | Before | After |
|---|---|---|---|
| S1 | Missing authorization (CWE-862): data operations with no working check | unguarded pages and operations (`auth.json`) | 0 open: endpoints with a proven check need a token; those whose legacy page had none are **locked by default** (secure by default) and flagged for a person to mark public |
| S2 | Execution after redirect (CWE-698, Doupé et al. CCS 2011) | redirects without `exit` and broken guards | 0. `Depends(get_current_user)` raises before the handler runs |
| S3 | SQL text built from request input (CWE-89) | queries whose SQL carries a request value, directly (DFG) or through a function parameter (endpoint params) | 0 in the interface. Values arrive as typed arguments, and C3 must bind them |
| S4 | Request inputs with no declared type | distinct `$_GET`/`$_POST`/`$_COOKIE` reads | declared OpenAPI parameters, checked by Pydantic (422 on a wrong type) |
| S5 | Password columns sent to the client | `sensitive-field` flags | the same, still flagged (not hidden until a person decides) |

### Separation of concerns: tangling

Eaddy et al. (IEEE TSE 2008) call a module tangled when it implements several concerns.

- **T1:** files whose statements span at least 2 of presentation, business logic and data access, based on `labels.json`. Third-party code under `vendor/` is left out.
- **T2:** files that both print HTML and run SQL.

### Complexity

- **C1:** McCabe's V(G) = E − N + 2 per unit, from the CFGs (reachable blocks only).
- We report the legacy hotspots and how each page is split into endpoints.
- There is **no** before/after V(G) comparison, because the generated handlers stay thin until C3 fills the data layer, so the comparison would be unfair.

Every "after" number states its reason in `note`, and nothing is estimated.

## 2. `architecture.json`: which target architecture fits

Three candidates are scored 0–100 by explicit rules, and every rule that fires is listed with its points:

| Option | Generated today? |
|---|---|
| Layered monolith | yes |
| Modular monolith | planned (PP2) |
| Microservices | planned (PP2) |

### Finding business areas

1. **Identity:** login tables that at least 80% of pages read. The legacy code re-reads the user on every request; after migration that information comes from the token.
2. **Grouping tables:** two tables join the same area when the sets of pages using them overlap (Jaccard ≥ 0.5), or when one table is named after the other (`cart` / `cart_items`). Groups are merged with union-find, so the result does not depend on order.
3. **Placing endpoints:** each endpoint joins the area of its main table.

This is the "shared entity access" coupling signal used by Service Cutter (Gysel et al., ESOCC 2016) and Mono2Micro (Kalia et al., ESEC/FSE 2021), taken here from procedural PHP.

### Rules (summary)

- Small APIs and few areas favour the layered monolith.
- Two or more areas favour the modular monolith.
- Microservices need many areas, pages that rarely combine them, and a large API.
- Pages that combine several areas count against microservices, because they would need distributed joins or a shared database (the "shared persistence" smell, Taibi & Lenarduzzi, IEEE Software 2018).
- Ties go to the simpler option (Fowler, MonolithFirst, 2015).

### Results on the benchmark apps

| App | Layered | Modular | Microservices | Areas |
|---|---|---|---|---|
| HMS | 75 | **80** | 5 | 4 (46% of data pages span areas) |
| WackoPicko | 60 | **90** | 40 | 5 (6%) |
| crud-php-complete | **95** | 80 | 25 | 2 |
| core-php-admin-panel | 0 | 0 | 0 | no endpoints: "not enough evidence" |

### Using it

- The report shows the options as selectable cards.
- `--architecture modular-monolith` records the choice in `architecture.json` and the report.
- Only the layered monolith is generated. The others carry the module plan the generator will follow.

## Limits

- The thresholds (Jaccard 0.5, identity 80%, the size cut-offs) are first choices, documented in the code and not yet tuned on more apps.
- Areas come from page co-use, which says nothing about runtime load or team structure.

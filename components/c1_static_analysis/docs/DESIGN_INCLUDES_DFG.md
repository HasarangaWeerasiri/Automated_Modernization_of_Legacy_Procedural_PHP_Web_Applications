# Include resolver and DFG (schema 1.0)

```
python -m c1.includes analysis-out   # -> index/includes.json
python -m c1.dfg analysis-out        # -> dfg/<fileId>.json, dfg/index.json
```

## Include resolver

| Rule | Source |
|---|---|
| Fold the path expression: literals, `.`, `"…$x…"`, `__DIR__`, `__FILE__`, `dirname()`, `define()` constants, single-assignment variables | Hills, Klint & Vinju, ASE 2014 |
| Parts still unknown → match the known fragments against the real file list; accept only a **unique** match (`pattern-unique`), else `ambiguous` with candidates | Hills et al. 2014 (FLRES) |
| `/…`, `./…`, `../…` ignore include_path; `./` and `../` are relative to the current working directory | PHP manual, `include` |
| Other relative paths: include_path (`.` = cwd), then the calling script's own directory | PHP manual, `include` |
| cwd = the requested page's directory, so resolution is also done **per entry page** (`entries`) | PHP manual + web SAPI behaviour |
| Request input in the path (`$_GET['page'] . '.php'`) → `dynamic`, flagged out of scope | Master reference §2.2 (dynamic includes excluded) |

Statuses: `resolved`, `ambiguous`, `dynamic`, `unresolved`, `missing`, `excluded` (vendored folder). Each status comes with its basis.

Results:
- HMS: 12/13 resolved (1 is excluded TCPDF).
- WackoPicko: 116/117 resolved (1 is the `$_GET['page']` front controller).

## DFG

- **Reaching definitions:** a forward may-analysis over CFG blocks, iterated to a fixpoint (standard dataflow framework).
- **Use-def chains:** every variable read lists the definitions that can reach it.
- **Weak definitions:** `$a['k'] = v` and `$o->p = v` add a definition and kill nothing.
- **Includes:** an include site defines the variables the included file (and what it includes) writes at top level. The origin is looked up in that file (PHP manual: included code shares the caller's scope).
- **Unresolved include, `extract()`, `eval()`:** a "havoc" definition that reaches every later read. We abstain rather than guess.
- **Sources:** `$_GET`/`$_POST`/`$_REQUEST`/`$_COOKIE`/`$_FILES`/`$_SERVER`/`$_SESSION` reads, and session writes.

### Origins (`uses[].origins`)

`input:GET:id`, `session:role`, `query:<callNode>`, `row:<callNode>`, `column:<callNode>:<name>`, `literal`, `call:<fn>`, `fn:<userFn>`, `method:<Class::m>`, `param:x`, `global:x`, `context:x` (top-level code of an included file reading its includer's variable), `undefined`, `unknown:<why>`.

- **Query chain:** `mysqli_query` → `query:Q`; `mysqli_fetch_*`/`->fetch_*` → `row:Q`; `$row['name']` → `column:Q:name`. `foreach` over a result gives rows.
- **Calls:** for user functions and `Class::method` / `self::` / `$this->m()`, the result takes the callee's `return` origins, with `param:x` replaced by the call's argument origins. This is a context-insensitive summary.
- **Unknown functions:** the result may depend on every argument.
- **Ambiguous names** (defined twice): no summary is used.
- **`queries[]`:** every SQL call with the origins of its SQL argument. C3 parses the SQL; we provide its data flow.

### Results (echo reads)

| App | Echo reads | Reach a query column | Request/session input | Unresolved call |
|---|---|---|---|---|
| HMS | 139 | 122 | 10 | 1 |
| WackoPicko | 87 | 70 | 9 | 3 |

### Known limits (v1)

- Array cells are not tracked separately, which is the weak-update over-approximation.
- `$obj->method()` on a variable that is not `$this` is not resolved (receiver type unknown) and stays `method:<name>`.
- `global $x` gives origin `global:x`; it is not linked to the main scope yet.

# CFG stage (schema 1.0)

`python -m c1.cfg analysis-out` reads `manifest.json` + `ast/`, writes `cfg/<fileId>.json` and `cfg/index.json`.

## Why we build our own

`ircmaxell/php-cfg` outputs SSA instructions, not our AST node ids, so every later stage would need a mapping back. We build the CFG in Python straight from the AST JSON. Every block lists the AST node ids it evaluates, so C2, C3 and our own classifier join on node ids.

## Rules and where they come from

| Rule | Source |
|---|---|
| One graph per unit: file body `{main}`, function, method, closure, arrow fn | Xie & Aiken 2006 §3 (CFG per function); Pixy 2006 (per-function CFGs) |
| Basic block = maximal single-entry single-exit statement run | Xie & Aiken §3 |
| Branch edges labelled with the predicate (`terminator.node`) | Xie & Aiken §3 |
| `exit`/`die` ends the block, edge `exit` to EXIT, code after it is unreachable | Xie & Aiken §3 (exit blocks); PHP manual, `exit` |
| Call to a function that always exits is cut the same way | Xie & Aiken §3.1.6 |
| `X or die()`, `X && die()`, `X ?? die()`, `c ? a : die()` become real branches | PHP manual, logical operators (short-circuit, Example 1); RoleCast 2011 (quick-exit checks) |
| `$x = q() or die()` assigns first, then tests | PHP manual: `or` binds looser than `=` |
| `break N` / `continue N` count enclosing loops **and** `switch` | PHP manual, break / continue |
| `continue` inside `switch` acts like `break` (flagged, PHP warns) | PHP manual, continue |
| `finally` does not run after `exit` | PHP manual, `exit` |
| `include` is a point in the caller's scope; block ends after it | Xie & Aiken §3 (splice at program point); resolver does the splice |
| Dominators: Cooper–Harvey–Kennedy iterative algorithm | Cooper, Harvey, Kennedy 2006; checked against networkx |

## Always-exiting functions

A function whose every path ends in `exit` is "always exiting" (WackoPicko: `http_redirect`, `error_404`). Found by fixpoint over the whole app. A name defined more than once is ambiguous and never cut. Not cutting keeps extra paths, which is the safe direction.

## Output, per unit

```json
{"id":"f001#00000","kind":"main","name":"{main}","entry":"B0","exit":"B1",
 "blocks":[{"id":"B0","items":["f001#00002"],"terminator":{"kind":"branch","node":"f001#00005","stmt":"f001#00004"},"reachable":true,"role":"entry"}],
 "edges":[{"from":"B0","to":"B2","label":"true","node":"f001#00005"}],
 "idom":{"B2":"B0"},"alwaysExits":false,"includeSites":[{"node":"…","block":"B3"}],
 "exitSites":[{"node":"…","block":"B4","via":"exit"}],"unreachableItems":[],"flags":[]}
```

- `items`: evaluation points in order. Usually statement ids. Conditions, loop headers and the right side of `or die` use expression ids.
- Edge labels: `next true false iterate done notnull null back break continue return end exit throw catch goto error halt`.
- `idom`: immediate dominator of every reachable block (entry left out).
- `flags`: places we over-approximated, each with a reason. We never drop a path.

## Self-check

`validate_unit` checks, on every run in the tests:
- every simple statement is in exactly one block;
- edges point at real blocks;
- nothing leaves EXIT or enters ENTRY.

HMS and WackoPicko have 0 problems.

## Known limits (v1)

- Exceptions: only explicit `throw` and blocks inside `try` get exception edges. Calls outside `try` are not given an implicit throw edge, which is standard for CFGs.
- `finally` is approximated: `return`/`break` inside `try` skip it. This is flagged when it happens; none of the benchmark apps have a `finally`.
- Method calls (`$obj->m()`) are never cut as always-exiting, because the receiver type is unknown.

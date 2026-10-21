# C1: Static Analysis Core + Backend & Authentication Migration (J26-SE-329)

Turns a legacy procedural PHP app into JSON analyses, an OpenAPI 3.1 contract and a FastAPI backend skeleton. Every stage is deterministic and sound-or-abstain: what it cannot decide it **flags**, it never guesses.

Place this folder at `components/c1_static_analysis/`.

## Setup

```bash
composer install                 # nikic/php-parser ^5.9 (only the extract stage needs PHP)
pip install -r requirements.txt  # pytest; add fastapi "python-jose[cryptography]" httpx for the backend boot test
python -m pytest -q
```

Needs PHP 8.0+ (8.3 recommended) for extract, and Python 3.10+.

## Run

Once: double-click `install.bat` (Windows) or run `./install.sh` (macOS/Linux). It checks Python/PHP/Composer, installs the PHP parser and the tool, and puts `c1-migrate` on your PATH. (By hand: `pip install -e ".[backend,dev]"`.) After that the commands below work from any folder (no `PYTHONPATH` needed), and `c1-migrate` is a short name for `python -m c1`. The legacy app is only read; re-running into the same `--out` folder replaces the old results.

```bash
PYTHONPATH=src python -m c1 run /path/to/hms --out analysis-out --exclude-dir TCPDF --title HMS
# add --plain for no colours (the console is plain automatically in pipes/CI)
# or stage by stage (all but the first need no PHP):
PYTHONPATH=src python -m c1.extract  /path/to/hms --out analysis-out --exclude-dir TCPDF
PYTHONPATH=src python -m c1.cfg       analysis-out
PYTHONPATH=src python -m c1.includes  analysis-out
PYTHONPATH=src python -m c1.dfg       analysis-out
PYTHONPATH=src python -m c1.concerns  analysis-out
PYTHONPATH=src python -m c1.auth      analysis-out --src /path/to/hms
PYTHONPATH=src python -m c1.endpoints analysis-out --src /path/to/hms
PYTHONPATH=src python -m c1.contract  analysis-out --title HMS
PYTHONPATH=src python -m c1.timeline  analysis-out --src /path/to/hms
```

## Stages and outputs

| Stage | Output | What it is | Design note |
|---|---|---|---|
| extract | `manifest.json`, `ast/<fileId>.json` | AST per file (schema 1.0, stable node ids) | this file, below |
| cfg | `cfg/<fileId>.json` | control-flow graph per function/page, dominators | `docs/DESIGN_CFG.md` |
| includes | `index/includes.json` | include/require resolution, per entry page | `docs/DESIGN_INCLUDES_DFG.md` |
| dfg | `dfg/<fileId>.json` | reaching definitions, use-def chains, value origins | `docs/DESIGN_INCLUDES_DFG.md` |
| concerns | `labels.json` | presentation / business_logic / data_access per statement (C2 format) | `docs/DESIGN_CONCERNS_AUTH.md` |
| auth | `auth.json` | session checks, guards, page protection, redirect-without-exit | `docs/DESIGN_CONCERNS_AUTH.md` |
| endpoints | `endpoints.json`, `endpoint-map.json` | REST endpoints, params, used-column response shapes (C4 format) | `docs/DESIGN_ENDPOINTS_CONTRACT.md` |
| contract | `contract/openapi.json`, `backend/app/`, `flags.json` | OpenAPI 3.1, FastAPI + Pydantic + JWT skeleton, review list | `docs/DESIGN_ENDPOINTS_CONTRACT.md` |
| timeline | `timeline/<fileId>.json` | output timeline for C2 (enclosures, typed reads) | `docs/DESIGN_TIMELINE.md` |
| metrics | `metrics.json` | before/after: attack surface (CWE-862/698/89), concern tangling, V(G) | `docs/DESIGN_METRICS_ARCHITECTURE.md` |
| architecture | `architecture.json` | scores layered / modular monolith / microservices, with reasons and module plan | `docs/DESIGN_METRICS_ARCHITECTURE.md` |
| report | `report.html` | one readable page: what changed, target architecture, guards, endpoints, items to review | `src/c1/report.py` |

Every JSON output carries `generatedFor` = `manifest.contentHash`, so outputs from different runs are never mixed.

## Results on the benchmark apps

More apps and the screening greps are in `docs/BENCHMARKS.md`. Evaluation: `docs/eval/` (`python -m c1.evaluate`).

| | HMS (777fda4, TCPDF excluded) | WackoPicko |
|---|---|---|
| Files / AST nodes | 33 / 9,639 | 49 / 6,850 |
| CFG units, structural problems | 107, 0 | 106, 0 |
| Includes resolved | 12/13 (1 excluded lib) | 116/117 (1 `$_GET` front controller, flagged) |
| Echo reads traced to a DB column | 122/139 | 70/87 |
| Pages guarded / unguarded with data | 0 / **13** | 17 / 16 |
| Endpoints | 19 | 25 |
| Generated backend boots, served = contract | yes | yes |
| Timeline leaf reads traced to a DB column | 122/189 | 69/133 |

## AST schema 1.0 (extract stage)

```json
{"schemaVersion":"1.0","fileId":"f012","path":"admin/panel.php","sha256":"...","bytes":4210,
 "parseOk":true,"parseErrors":[],"rootId":"f012#00000","nodeCount":311,"nodes":[ ... ]}
```

Node: `{"id":"f012#00004","kind":"Stmt_If","parent":"f012#00000","loc":{...},"children":[...],"fields":{"cond":...},"attrs":{}}`

- **Ids:** `<fileId>#<5-digit pre-order ordinal>`. The root is `File` (`#00000`).
- **File ids:** taken from the sorted relative path. They are only comparable within one `contentHash`.
- **`loc`:** lines are 1-based; columns and bytes are 0-based **bytes**, end exclusive (`source[startByte:endByte]`).
- **`fields`:** the role of each child (`cond`, `stmts`, `else` …).
- **`attrs`:** plain values; `_kind`, `_rawValue`, `_parenthesized` are parser attributes.
- **Encodings:** non-UTF-8 strings are `{"__b64": …}`; non-finite floats are `{"__float": …}`.
- **Inline HTML:** `Stmt_InlineHTML.attrs.value` is the text as PHP outputs it (CRLF kept).
- **Comments** are not emitted.
- **Errors:** a file with syntax errors is reported (`parseOk: false`), never dropped.
- **Exclusions:** a skipped folder is recorded in `manifest.excluded`.

## Layout

```
tools/php_ast/extract_ast.php   PHP: one file in, one AST JSON out
src/c1/extract.py               walks the app, ids, manifest
src/c1/astdoc.py                shared AST reader
src/c1/cfg.py, dominance.py     CFG + Cooper-Harvey-Kennedy dominators
src/c1/includes.py              include resolver
src/c1/dfg.py                   reaching definitions, use-def, origins, call summaries
src/c1/concerns.py              concern labels
src/c1/auth.py                  authorization analysis
src/c1/sqlshape.py              SQL template + shape, schema dump reader
src/c1/endpoints.py             endpoints, response shape, endpoint map
src/c1/contract.py              OpenAPI 3.1, FastAPI backend, flags
src/c1/timeline.py              output timeline for C2
src/c1/evaluate.py              labelling sheet + scoring against a hand-labelled corpus
src/c1/report.py                HTML review report
src/c1/__main__.py              python -m c1 run / analyse
tests/                          pytest (fixtures/ast_cache lets stage tests run without PHP)
docs/                           design notes per stage, DEVLOG.md, COMMIT_GROUPS.md
ci/c1-determinism.yml           copy to .github/workflows/
```

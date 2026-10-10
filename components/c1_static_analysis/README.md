# C1 Static Analysis Core (J26-SE-329)

Turns a legacy PHP app into JSON that the other components can read. This first slice does one job: **parse every file into a stable, deterministic AST** and describe the run in `manifest.json`.

Place this folder at `components/c1_static_analysis/` (the same pattern as `c3_data_layer` and `c4_validation`).

## Setup

```bash
composer install            # installs nikic/php-parser ^5.9 (same version range as C3). Commit composer.lock.
pip install -r requirements.txt
python -m pytest -q
```

Needs PHP 8.0 or newer on the machine (8.3 recommended) and Python 3.11 or newer.

## Run

```bash
PYTHONPATH=src python -m c1.extract /path/to/legacy-app --out analysis-out
# vendored libraries: skip them, and the run records that it did
PYTHONPATH=src python -m c1.extract /path/to/hms --out analysis-out --exclude-dir TCPDF
```

Output:

```
analysis-out/
  manifest.json          run description, parser version, file list with hashes, excluded folders
  ast/f000.json ...      one file per PHP file
```

The run prints a warning when one folder holds half or more of all nodes (usually a vendored library).

## Guarantees (tested)

- Same input gives byte-identical output (`tests/test_determinism.py`).
- Output does not depend on where the app sits on disk, and holds no timestamps or absolute paths.
- File ids come from the sorted relative path: `f000`, `f001`, ... Adding a file shifts later ids, so ids are only comparable within one `manifest.contentHash`.
- Node ids are `<fileId>#<5-digit pre-order ordinal>`, root is `#00000`.
- A file with a syntax error is **reported** (`parseOk: false`, `parseErrors`) and the run continues. It is never dropped silently.
- A skipped folder is **recorded** in `manifest.excluded` with its file count.

## AST file shape (schema 1.0)

```json
{"schemaVersion":"1.0","fileId":"f012","path":"admin/panel.php","sha256":"...","bytes":4210,
 "parseOk":true,"parseErrors":[],"rootId":"f012#00000","nodeCount":311,"nodes":[ ... ]}
```

Each node:

```json
{"id":"f012#00004","kind":"Stmt_If","parent":"f012#00000",
 "loc":{"startLine":3,"endLine":6,"startCol":0,"endCol":1,"startByte":38,"endByte":120},
 "children":["f012#00005","f012#00010"],
 "fields":{"cond":"f012#00005","stmts":["f012#00010"],"elseifs":[],"else":null},
 "attrs":{}}
```

| Field | Meaning |
|---|---|
| `kind` | nikic/PHP-Parser type name (`Stmt_Echo`, `Stmt_InlineHTML`, `Expr_ArrayDimFetch`...). The root is `File`. |
| `loc` | Lines are 1-based. Columns and bytes are 0-based **byte** offsets, end exclusive. `source_bytes[startByte:endByte]` is the node text. |
| `children` | Child ids in pre-order. |
| `fields` | Which child plays which role (`cond`, `stmts`, `else`...). A `null` inside a list is a hole such as `list(, $b)`. |
| `attrs` | Plain values (`name`, `value`...). Keys starting with `_` are parser attributes: `_kind` (quote style or number base), `_rawValue` (number source text), `_parenthesized`. |

Special encodings:

- A string that is not valid UTF-8 is `{"__b64": "<base64>"}` (legacy Latin-1 pages exist).
- A non-finite float is `{"__float": "INF"}`.
- `Stmt_InlineHTML.attrs.value` is the text exactly as PHP outputs it, CRLF kept. PHP itself drops **one** newline directly after `?>`, so that newline is not in `value`. Use `loc` bytes for an exact source slice.
- Comments are not emitted in 1.0.

## Compared with what C2 was told earlier

Two additions, both extra fields (nothing removed):

1. `fields` names the role of each child. Without it `cond` and `then` cannot be told apart from `children` alone.
2. `loc` also carries `startByte` / `endByte`, and columns are defined as 0-based bytes.

The `File` root node and the `_`-prefixed attrs are new too. C2's `output-timeline.json` (`id`, `kind`, `raw`, `enclosedBy`, `reads`) is a **later stage** built from these ASTs. Its `raw` for `Stmt_InlineHTML` equals `attrs.value`.

## Known limits (v0.1)

- **Host PHP must support the syntax it lexes.** The parser is set to PHP 8.3 syntax (`TARGET_PHP_VERSION`), so CI should run PHP 8.3.
- **No CFG, DFG, includes, reads or labels yet.** Those are the next stages.
- Vendored libraries are not excluded by default. Excluding is a choice you make with `--exclude-dir`, and it is logged.

## Layout

```
tools/php_ast/extract_ast.php   one file in, one JSON document out
src/c1/extract.py               walks a tree, assigns ids, writes analysis-out/
tests/                          pytest, fixtures in tests/fixtures/php (CRLF and Latin-1 files are made at test time)
ci/c1-determinism.yml           copy to .github/workflows/
```

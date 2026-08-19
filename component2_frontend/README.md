# Component 2 — Frontend Migration

Part of the research project *Automated Modernization of Legacy Procedural PHP
Web Applications*.

## What this component does

Reads an Abstract Syntax Tree (AST) of legacy procedural PHP code, infers UI
component boundaries from the flat echo-based HTML output, traces what data
each inferred component needs via backward data-flow analysis, reconciles
those needs against an OpenAPI contract produced by another component (the
Static Analysis Core / Component 1), and generates Next.js TSX components.

The tool itself is written in Python. Its **output** is Next.js/TypeScript
code. This directory also hosts a running legacy PHP application (via Docker)
to study and test the pipeline against.

Because the real AST and OpenAPI contract inputs don't exist yet, development
happens against mocks in `mocks/`.

## Folder structure

```
component2_frontend/
├── src/
│   ├── main.py           # CLI entry point, pipeline orchestration
│   ├── mapping/           # Steps 1-4: isolate, trace, infer, reconcile
│   │   └── explore.py      # naive echo/print string-matcher (see below)
│   ├── conversion/        # Steps 5-7: HTML->JSX, API calls, SSR decision
│   └── generation/        # Steps 8-9: emit .tsx files
├── tests/                 # pytest test suite
├── mocks/                 # mock AST + mock OpenAPI contract
├── output/                # generated Next.js code (output/generated/ is
│                             gitignored) + the test-app scaffold
├── legacy-apps/           # cloned legacy PHP projects
│   └── sql/                # SQL dumps, auto-imported by the mysql container
├── docs/                  # paper summaries, analysis notes
├── requirements.txt
├── docker-compose.yml
└── venv/                  # Python virtual environment (gitignored)
```

## Setup

### 1. Python environment

Requires Python 3.10+ with prebuilt wheels available for pinned dependency
versions. On Windows, use Python 3.12 (Python 3.14 currently lacks prebuilt
wheels for pyyaml/pydantic-core, forcing a source build that needs a Rust +
MSVC toolchain).

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Docker environment (legacy PHP app)

```bash
docker-compose up -d
```

This starts:
- **php** — PHP 7.4 + Apache, serving `legacy-apps/` at http://localhost:8080
  (with `mysqli`, `pdo`, `pdo_mysql` extensions installed on startup)
- **mysql** — MySQL 5.7, port 3306, database `legacydb`, root password
  `root`. SQL dumps placed in `legacy-apps/sql/` are auto-imported on first
  container start.
- **phpmyadmin** — http://localhost:8081, logs in with root/root against the
  mysql service.

### 3. Next.js output target

`output/test-app` is a scaffolded Next.js (App Router, TypeScript, Tailwind)
project used to sanity-check that generated components actually build and
run. Generated components from the pipeline land in `output/generated/`.

## Running the pipeline against the mocks

```bash
venv\Scripts\python.exe src\main.py --input mocks\sample_ast.json --contract mocks\sample_contract.json --output output\generated
```

This loads and pretty-prints both mock structures. The nine pipeline steps
are stubbed with TODOs in `src/main.py` — implementing them is the actual
component work.

`src/mapping/explore.py` is a deliberately naive companion script — it
greps a raw PHP file for lines containing `echo`/`print` using plain string
matching, with no parsing. Run it against a real legacy file to see it
misfire on inline HTML and multi-line statements; that failure is the
motivation for doing this via a real AST instead:

```bash
venv\Scripts\python.exe src\mapping\explore.py legacy-apps\hms\admin-panel.php
```

(`index.php` has no `echo`/`print` at all, so it isn't a useful demo file —
`admin-panel.php` mixes inline HTML and PHP echo on the same line, which is
exactly what trips the naive matcher up.)

## Legacy application URLs

- Legacy PHP app: http://localhost:8080
- phpMyAdmin: http://localhost:8081 (root/root)

## Manual step required

`legacy-apps/hms/include/config.php` still points at `localhost` with an
empty password and database `hms`, matching how the app runs outside
Docker. To run it against the `mysql` container, update it to:

```php
define('DB_SERVER','mysql');
define('DB_USER','root');
define('DB_PASS' ,'root');
define('DB_NAME', 'legacydb');
```

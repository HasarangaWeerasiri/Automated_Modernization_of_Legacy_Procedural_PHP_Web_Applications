# PP1 demo: Component 1 in 2:30

**Goal:** show what the tool does, prove it with numbers, and state plainly what is not finished.

## Before you go on stage (5 minutes earlier)

1. **cmd window 1:** maximised (for the big header), in any folder. Paste this command but **don't press Enter yet**:
   ```cmd
   python -m c1 run "C:\Users\amindu\Desktop\Research\legacy systems\HMS" --out "C:\Users\amindu\Desktop\Research\HMS_migrated" --exclude-dir TCPDF --schema "C:\Users\amindu\Desktop\Research\legacy systems\HMS\myhmsdb.sql" --title HMS
   ```
2. **Run it once now**, so the backend folder exists. Write down the fingerprint. *(It should be `D012 91EA 3AC8 A46E` if your files match the cloud run. If it differs, just use the one you see.)*
3. **cmd window 2:** start the backend and leave it running:
   ```cmd
   cd "C:\Users\amindu\Desktop\Research\HMS_migrated\backend"
   python -m uvicorn app.main:app --reload
   ```
4. **Browser tabs:**
   - Tab 1: `c1_novelty_api_boundary.png` (the slide)
   - Tab 2: `HMS_migrated\report.html`
   - Tab 3: `http://localhost:8000`, which opens the API docs
5. **Backup:** if anything fails on stage, show the same files from the backup run. Do not debug live.

## The 2:30

| Time | Show | Say (short) |
|---|---|---|
| 0:00–0:25 | **Slide** (tab 1) | "Old PHP pages mix HTML, logic and SQL. This HMS admin page switches between them 54 times, has no working login check, and has no API at all. My component **derives an API boundary that never existed**: real endpoints with real login checks, by static analysis, not guessing." |
| 0:25–0:55 | **cmd 1**: press Enter | "One command reads all 33 PHP files and runs 12 steps: parse, control flow, data flow, concern labels, login guards, endpoints, contract…" *(point at the RESULTS box)* "…and it measures the change." |
| 0:55–1:20 | **Report** (tab 2), *What the migration changed* | "My success factor is **security**, measured as attack surface: 13 pages used data with no check; now 8 endpoints need a token and 10 are flagged for a decision, none silently open. Redirects that don't stop: 12 → 0. SQL built from user input: 26 → 0. Tangled files: 69% → 0%." |
| 1:20–1:40 | Scroll to **Target architecture**, click the cards | "Three-tier isn't always best. The tool scores three architectures from the code itself. For HMS it suggests a **modular monolith**; for a small CRUD app it picks layered. Today we generate the layered monolith; the others come in PP2." |
| 1:40–2:00 | **API docs** (tab 3): `GET /api/me/prestb` → Try it out → Execute → **401** | "This is the generated FastAPI backend with an OpenAPI 3.1 contract. In the old app this data was open; now, without a login token, the answer is 401." |
| 2:00–2:15 | **cmd 1**: press ↑ then Enter | "Run it again: **the same fingerprint**. Same input, byte-identical output, every time." |
| 2:15–2:30 | (talk) | "**Not finished yet:** the accuracy study (200 labelled statements, two people), generating the other architectures, and the data layer that Component 3 fills in. Comparing old and new behaviour is Component 4's test." |

## If they ask

- **"How is this different from existing tools?"**
  - Rector and similar tools upgrade PHP versions but don't re-architect.
  - Model-driven REST derivation (Rodríguez-Echeverría et al. 2014) needs MVC sources and models.
  - AI rewrites aren't repeatable or checkable.
  - We work on unstructured procedural PHP, deterministically, and we recover the authorization rules too.
- **"Why not Laravel?"** Framework apps already declare their routes, controllers and models, so there is no hidden API to recover. Our research gap is the code where none exists. Framework adapters are future work.
- **"Does the backend behave the same as the old one?"**
  - Every endpoint keeps the legacy SQL and its source line, and the served schema equals the contract (tested).
  - Full behaviour comparison is Component 4's job.
- **"What if the tool is unsure?"** It flags the case for a person (46 items on HMS, mostly one question: which pages need login). It never guesses a security rule.

"""Generate Component 2's mock output timelines (schema 1.0) and concern labels.

Every Stmt_InlineHTML `raw` is the exact T_INLINE_HTML token text produced by
PHP's own lexer (token_get_all, which nikic/PHP-Parser is built on), so it is
byte-exact against the source it was lexed from. Node ids are
"<fileId>#<token index>", so they are stable as long as the source is.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/gen_timelines.py

Needs PHP (PHP_BIN env var, `php` on PATH, or XAMPP at C:/xampp/php/php.exe)
and the HMS clone at legacy-apps/hms checked out at UPSTREAM_COMMIT.
"""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HMS = ROOT / "legacy-apps" / "hms"
MOCKS = ROOT / "mocks"
TOK_PHP = Path(__file__).with_name("tok.php")

SCHEMA_VERSION = "1.0"
UPSTREAM_COMMIT = "777fda46b77a820977a5ba616283dbfbc40bf7e1"
UPSTREAM = f"https://github.com/kishan0725/Hospital-Management-System @ {UPSTREAM_COMMIT}"

QUERY_FN = "mysqli_query"
FETCH_FNS = {"mysqli_fetch_array", "mysqli_fetch_assoc", "mysqli_fetch_all"}
SUPERGLOBALS = {"$_SESSION": "session", "$_GET": "request", "$_POST": "request", "$_REQUEST": "request",
                "$_COOKIE": "request", "$_SERVER": "server"}
INSIGNIFICANT = {"T_WHITESPACE", "T_COMMENT", "T_DOC_COMMENT"}
LITERALS = {"T_CONSTANT_ENCAPSED_STRING", "T_LNUMBER", "T_DNUMBER"}
CONFIDENCE_ORDER = ["resolved", "ambiguous", "unresolved"]

# If-condition source text per node id, filled during generation (tests read it).
CONDITIONS: dict[str, str] = {}


def find_php() -> str | None:
    for candidate in (os.environ.get("PHP_BIN"), shutil.which("php"), r"C:\xampp\php\php.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def php_version(php: str) -> str:
    return subprocess.run([php, "-r", "echo PHP_VERSION;"], capture_output=True, text=True, check=True).stdout


def upstream_blob(name: str) -> bytes:
    head = subprocess.run(["git", "-C", str(HMS), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    if head != UPSTREAM_COMMIT:
        raise RuntimeError(f"legacy-apps/hms is at {head}, expected {UPSTREAM_COMMIT}")
    return subprocess.run(["git", "-C", str(HMS), "show", f"HEAD:{name}"], capture_output=True, check=True).stdout


def lex(src: bytes, php: str) -> list[tuple[str, bytes, int]]:
    out = subprocess.run([php, str(TOK_PHP)], input=src, capture_output=True, check=True).stdout
    return [(n, base64.b64decode(t), line) for n, t, line in json.loads(out)]


class Source:
    """Token stream plus the block structure, calls and outputs found in it."""

    def __init__(self, tokens, file_id):
        self.t = tokens
        self.file_id = file_id
        self.outputs = []  # dicts: tok, line, end_line, kind, raw | expr (token range), frames
        self.ifs = {}  # root If node id -> {condNodeId, cond, line, body_has_query}
        self.loops = {}  # loop node id -> {kind, line, cond (lo, hi) token range}
        self.calls = {}  # call node id -> {fn, tok, line, args [(lo, hi)], assigned}
        self._walk()

    def nid(self, i):
        return f"{self.file_id}#{i:05d}"

    def text(self, lo, hi):
        """Exact source text of tokens lo..hi-1, outer whitespace trimmed."""
        return b"".join(tok[1] for tok in self.t[lo:hi]).decode("utf-8").strip()

    def sig(self, i, step=1):
        i += step
        while self.t[i][0] in INSIGNIFICANT:
            i += step
        return i

    def close_paren(self, i):
        """i is the index of '('; return the index of its matching ')'."""
        depth = 0
        for j in range(i, len(self.t)):
            name, text, _ = self.t[j]
            if name == "CHAR" and text == b"(":
                depth += 1
            elif name == "CHAR" and text == b")":
                depth -= 1
                if depth == 0:
                    return j
        raise ValueError(f"unbalanced '(' at token {i}")

    def split_args(self, lo, hi):
        """Top-level comma-separated (lo, hi) ranges inside a call's parens."""
        args, depth, start = [], 0, lo
        for j in range(lo, hi):
            name, text, _ = self.t[j]
            if name == "CHAR" and text in (b"(", b"["):
                depth += 1
            elif name == "CHAR" and text in (b")", b"]"):
                depth -= 1
            elif name == "CHAR" and text == b"," and depth == 0:
                args.append((start, j))
                start = j + 1
        args.append((start, hi))
        return args

    def _record_call(self, i, stack):
        """Record a mysqli query/fetch call at token i (a no-op for any other token)."""
        name, text, line = self.t[i]
        if name != "T_STRING" or (text.decode() != QUERY_FN and text.decode() not in FETCH_FNS):
            return
        p = self.sig(i)
        close = self.close_paren(p)
        prev = self.sig(i, -1)
        assigned = None
        if self.t[prev][:2] == ("CHAR", b"=") and self.t[self.sig(prev, -1)][0] == "T_VARIABLE":
            assigned = self.t[self.sig(prev, -1)][1].decode()
        self.calls[self.nid(i)] = {"fn": text.decode(), "tok": i, "line": line,
                                   "args": self.split_args(p + 1, close), "assigned": assigned}
        if text.decode() == QUERY_FN:
            for frame in stack:
                if frame["kind"] == "branch":
                    self.ifs[frame["id"]]["body_has_query"] = True

    def _walk(self):
        stack, pending, last_closed_if = [], None, None
        i = 0
        while i < len(self.t):
            name, text, line = self.t[i]

            if name in ("T_IF", "T_ELSEIF", "T_WHILE", "T_FOREACH"):
                # Headers are skipped below, so record the calls inside them here
                # (e.g. the mysqli_fetch_array in `while ($row = mysqli_fetch_array(...))`).
                p = self.sig(i)
                for j in range(p + 1, self.close_paren(p)):
                    self._record_call(j, stack)

            if name in ("T_IF", "T_ELSEIF"):
                p = self.sig(i)
                close = self.close_paren(p)
                cond_id = self.nid(self.sig(p))
                cond = self.text(p + 1, close)
                if name == "T_IF":
                    root = self.nid(i)
                    self.ifs[root] = {"condNodeId": cond_id, "cond": cond, "line": line, "body_has_query": False}
                    CONDITIONS[root] = cond
                    frame = {"kind": "branch", "id": root, "branch": "then", "condNodeId": cond_id}
                else:
                    frame = {"kind": "branch", "id": last_closed_if, "branch": "elseif", "condNodeId": cond_id}
                    CONDITIONS[cond_id] = cond
                pending = frame
                i = close + 1
                continue
            if name == "T_ELSE":
                if self.t[self.sig(i)][0] == "T_IF":
                    raise NotImplementedError(f"'else if' at line {line}")
                root = last_closed_if
                pending = {"kind": "branch", "id": root, "branch": "else",
                           "condNodeId": self.ifs[root]["condNodeId"]}
                i += 1
                continue
            if name in ("T_WHILE", "T_FOREACH"):
                p = self.sig(i)
                close = self.close_paren(p)
                self.loops[self.nid(i)] = {"kind": "Stmt_While" if name == "T_WHILE" else "Stmt_Foreach",
                                           "line": line, "cond": (p + 1, close)}
                pending = {"kind": "loop", "id": self.nid(i)}
                i = close + 1
                continue
            if name == "T_FUNCTION":
                pending = {"kind": "function"}
                i += 1
                continue

            self._record_call(i, stack)

            if (name == "CHAR" and text == b"{") or name in ("T_CURLY_OPEN", "T_DOLLAR_OPEN_CURLY_BRACES"):
                if pending and name == "CHAR":
                    stack.append(pending)
                    pending = None
                else:
                    stack.append({"kind": "generic"})
                i += 1
                continue
            if name == "CHAR" and text == b":" and pending:
                raise NotImplementedError(f"alternative syntax at line {line}")
            if name == "CHAR" and text == b"}":
                frame = stack.pop()
                last_closed_if = frame["id"] if frame["kind"] == "branch" else None
                i += 1
                continue

            if name in ("T_INLINE_HTML", "T_ECHO", "T_PRINT", "T_OPEN_TAG_WITH_ECHO"):
                if any(f["kind"] == "function" for f in stack):
                    i += 1  # output inside a function body is not page-level execution order
                    continue
                out = {"tok": i, "line": line, "end_line": line + text.count(b"\n") - text.endswith(b"\n"),
                       "frames": [f for f in stack if f["kind"] in ("loop", "branch")]}
                if name == "T_INLINE_HTML":
                    out |= {"kind": "Stmt_InlineHTML", "raw": text}
                else:
                    j, depth = i + 1, 0
                    while True:
                        n, t, _ = self.t[j]
                        if depth == 0 and (n == "T_CLOSE_TAG" or (n == "CHAR" and t == b";")):
                            break
                        if n == "CHAR" and t in (b"(", b"["):
                            depth += 1
                        elif n == "CHAR" and t in (b")", b"]"):
                            depth -= 1
                        j += 1
                    out |= {"kind": "Expr_Print" if name == "T_PRINT" else "Stmt_Echo", "expr": (i + 1, j)}
                self.outputs.append(out)
            i += 1
        assert not stack, f"unbalanced blocks: {stack}"

    # ---- data-flow helpers (reaching definitions by token order) -------------

    def latest_call(self, before, fn_set, assigned):
        hits = [c for c in self.calls.values() if c["fn"] in fn_set and c["assigned"] == assigned
                and c["tok"] < before]
        return max(hits, key=lambda c: c["tok"]) if hits else None

    def query_of_fetch(self, fetch):
        result_var = self.text(*fetch["args"][0])
        query = self.latest_call(fetch["tok"], {QUERY_FN}, result_var)
        if query is None:
            raise ValueError(f"no reaching mysqli_query for {result_var} at line {fetch['line']}")
        return query

    def raw_sql(self, query):
        """Source text of the SQL argument: the literal itself, or the reaching `$var = ...;` assignment."""
        lo, hi = query["args"][1]
        arg = self.text(lo, hi)
        if not arg.startswith("$"):
            return arg
        for j in range(query["tok"] - 1, -1, -1):
            n, t, _ = self.t[j]
            if n == "T_VARIABLE" and t.decode() == arg and self.t[self.sig(j)][1] == b"=":
                start = self.sig(j) + 1
                end = start
                while self.t[end][1] != b";":
                    end += 1
                return self.text(start, end)
        raise ValueError(f"no assignment reaching SQL argument {arg} at line {query['line']}")


def loop_object(src, loop_id):
    lp = src.loops[loop_id]
    lo, hi = lp["cond"]
    fetch = next((c for c in src.calls.values() if c["fn"] in FETCH_FNS and lo <= c["tok"] < hi), None)
    if lp["kind"] == "Stmt_While":
        eq = next(j for j in range(lo, hi) if src.t[j][:2] == ("CHAR", b"="))
        iter_expr, value_var, key_var = src.text(lo, hi), src.text(lo, eq), None
    else:
        as_tok = next(j for j in range(lo, hi) if src.t[j][0] == "T_AS")
        iter_expr = src.text(lo, as_tok)
        arrow = next((j for j in range(as_tok, hi) if src.t[j][0] == "T_DOUBLE_ARROW"), None)
        key_var = src.text(as_tok + 1, arrow) if arrow else None
        value_var = src.text(arrow + 1 if arrow else as_tok + 1, hi)
        if fetch is None and iter_expr.startswith("$"):
            fetch = src.latest_call(lo, FETCH_FNS, iter_expr)
    return {
        "nodeId": loop_id,
        "kind": lp["kind"],
        "role": "iteration",
        "iterExpr": iter_expr,
        "iterSourceKind": "db_row_field" if fetch else "unresolved",
        "valueVar": value_var,
        "keyVar": key_var,
    }, fetch


def parse_reads(src, lo, hi):
    """Unresolved reads in tokens lo..hi-1: var reads (with dims), literals, and calls (computed)."""
    idx = [j for j in range(lo, hi) if src.t[j][0] not in INSIGNIFICANT]
    reads, k = [], 0
    while k < len(idx):
        j = idx[k]
        name, text, _ = src.t[j]
        if name == "T_VARIABLE":
            path, end = [], k
            while (end + 3 < len(idx) and src.t[idx[end + 1]][1] == b"[" and src.t[idx[end + 3]][1] == b"]"
                   and src.t[idx[end + 2]][0] in ("T_CONSTANT_ENCAPSED_STRING", "T_STRING", "T_NUM_STRING")):
                path.append(src.t[idx[end + 2]][1].decode().strip("'\""))
                end += 3
            reads.append({"form": "var", "expr": src.text(j, idx[end] + 1), "var": text.decode(), "path": path})
            k = end + 1
            continue
        if name == "T_STRING" and k + 1 < len(idx) and src.t[idx[k + 1]][1] == b"(":
            close = src.close_paren(idx[k + 1])
            inner = [r for r in parse_reads(src, idx[k + 1] + 1, close) if r["form"] != "literal"]
            reads.append({"form": "call", "expr": src.text(j, close + 1), "inner": inner})
            k = next(n for n, x in enumerate(idx) if x > close) if close < idx[-1] else len(idx)
            continue
        if name in LITERALS:
            reads.append({"form": "literal", "expr": text.decode()})
        k += 1
    return reads


def build(spec, php, lexer_note):
    src_bytes = spec["src"]
    src = Source(lex(src_bytes, php), spec["file_id"])
    lo, hi = spec["lines"]
    selected = [o for o in src.outputs if lo <= o["line"] <= hi]
    query_cfg = spec["queries"]

    loop_objs, loop_fetch = {}, {}
    for lid in src.loops:
        loop_objs[lid], loop_fetch[lid] = loop_object(src, lid)

    queries, labels = {}, {}

    def db_source(fetch, path):
        query = src.query_of_fetch(fetch)
        qid = src.nid(query["tok"])
        cfg = query_cfg[query["line"]]
        queries[qid] = {"line": query["line"], "sql": src.raw_sql(query), "tables": cfg["tables"],
                        "columns": cfg["columns"], "resultVar": query["assigned"]}
        labels[src.nid(fetch["tok"])] = label("data_access", "C1-ROW-FETCH", "row_fetch")
        labels[qid] = label("data_access", "C1-SQL-EXEC", "sql_execution")
        if cfg["ambiguous"]:
            return "ambiguous", {"fetchNodeId": src.nid(fetch["tok"]), "queryNodeId": qid,
                                 "table": None, "column": None}
        column = path[0]
        assert column in cfg["columns"], f"{column} not selected by query at line {query['line']}"
        return "resolved", {"fetchNodeId": src.nid(fetch["tok"]), "queryNodeId": qid,
                            "table": cfg["tables"][0], "column": column}

    def resolve(r, out):
        if r["form"] == "literal":
            return {"expr": r["expr"], "var": None, "path": None, "sourceKind": "literal",
                    "confidence": "resolved", "source": None}
        if r["form"] == "call":
            derived = [resolve(x, out) for x in r["inner"]]
            worst = max((d["confidence"] for d in derived), key=CONFIDENCE_ORDER.index, default="resolved")
            return {"expr": r["expr"], "var": None, "path": None, "sourceKind": "computed",
                    "confidence": worst, "source": None, "derivedFrom": derived}
        var = r["var"]
        base = {"expr": r["expr"], "var": var, "path": r["path"]}
        loop = next((f for f in reversed(out["frames"])
                     if f["kind"] == "loop" and loop_objs[f["id"]]["valueVar"] == var), None)
        fetch = loop_fetch[loop["id"]] if loop else src.latest_call(out["tok"], FETCH_FNS, var)
        if fetch:
            confidence, source = db_source(fetch, r["path"])
            return base | {"sourceKind": "db_row_field", "confidence": confidence, "source": source}
        kind = SUPERGLOBALS.get(var) or spec["var_kinds"].get(var)
        if kind is None:
            raise ValueError(f"{spec['entrypoint']}: unresolved read {r['expr']} at line {out['line']}")
        confidence = "unresolved" if kind == "unresolved" else "resolved"
        return base | {"sourceKind": kind, "confidence": confidence, "source": None}

    sequence = []
    for out in selected:
        entry = {"id": src.nid(out["tok"]), "kind": out["kind"]}
        if out["kind"] == "Stmt_InlineHTML":
            entry["raw"] = out["raw"].decode("utf-8")
        else:
            entry["reads"] = [resolve(r, out) for r in parse_reads(src, *out["expr"])]
        enclosed = []
        for f in out["frames"]:
            if f["kind"] == "loop":
                enclosed.append(loop_objs[f["id"]])
                fetch = loop_fetch[f["id"]]
                header_lo, header_hi = src.loops[f["id"]]["cond"]
                if fetch and header_lo <= fetch["tok"] < header_hi:
                    labels[f["id"]] = label("mixed", "C1-FETCH-LOOP", "fetch_in_loop_header")
                else:
                    labels[f["id"]] = label("presentation", "C1-RENDER-LOOP", "iterates_prefetched_rows")
            else:
                enclosed.append({"nodeId": f["id"], "kind": "Stmt_If", "role": "branch",
                                 "branch": f["branch"], "condNodeId": f["condNodeId"]})
                labels[f["id"]] = if_label(src.ifs[f["id"]])
        entry["enclosedBy"] = enclosed
        labels[entry["id"]] = label("presentation", "C1-OUTPUT", "output_statement")
        sequence.append(entry)

    provenance = dict(spec["provenance"])
    provenance["notes"] = [n.format(query=", ".join(sorted(queries))) for n in provenance["notes"]]
    timeline = {
        "entrypoint": spec["entrypoint"],
        "schemaVersion": SCHEMA_VERSION,
        "sequence": sequence,
        "queries": dict(sorted(queries.items())),
        "_provenance": provenance | {
            "linesCovered": [selected[0]["line"], selected[-1]["end_line"]],
            "lexer": lexer_note,
        },
    }
    return timeline, {"schemaVersion": SCHEMA_VERSION, "labels": dict(sorted(labels.items()))}


def label(concern, rule_id, reason, basis="rule"):
    return {"concern": concern, "basis": basis, "ruleId": rule_id, "reason": reason}


def if_label(node):
    """Component 1's authorization rule: query in body -> logic; output-only body -> display, unless the
    condition is a session check, where display vs access control cannot be decided from this file."""
    session = "$_SESSION" in node["cond"]
    if node["body_has_query"]:
        return label("business_logic", "C1-AUTHZ-QUERY" if session else "C1-GATE-QUERY", "gates_data_access")
    if session:
        return label("undecided", "C1-AUTH-OR-DISPLAY", "auth_or_display", basis="abstain")
    return label("presentation", "C1-DISPLAY-COND", "display_conditional")


# ----------------------------------------------------------------------------- specs

APPOINTMENT_COLUMNS = ["pid", "ID", "fname", "lname", "gender", "email", "contact", "doctor",
                       "docFees", "appdate", "apptime", "userStatus", "doctorStatus"]
WHILE_HDR = b"while ($row = mysqli_fetch_array($result)){"
FOREACH_HDR = b"foreach (mysqli_fetch_all($result, MYSQLI_BOTH) as $row){"

ADMIN_SRC = b"""<!DOCTYPE html>
<?php
session_start();
$con=mysqli_connect("mysql","root","root","legacydb");
$username = $_SESSION['username'];
?>
<html lang="en">
  <body style="padding-top:50px;">
   <div class="container-fluid" style="margin-top:50px;">
    <h3 style = "margin-left: 40%;  padding-bottom: 20px; font-family: 'IBM Plex Sans', sans-serif;"> Welcome &nbsp<?php echo $username ?>
   </h3>
<?php if (isset($_SESSION['role']) && $_SESSION['role'] == 'admin') {
  $query = "select count(*) as total, sum(docFees) as revenue from appointmenttb where userStatus=1 and doctorStatus=1;";
  $result = mysqli_query($con,$query);
  $stats = mysqli_fetch_array($result);
?>
    <div class="admin-stats">
      <p>Active appointments: <?php echo $stats['total'];?></p>
      <p>Fees collected: &#8377;<?php print $stats['revenue'];?></p>
      <p>Signed in as <?php echo $_SESSION['role'];?></p>
    </div>
<?php } else { ?>
    <p class="text-muted">Statistics are visible to administrators only.</p>
<?php } ?>
<?php if ($_SESSION['role'] == 'admin') { ?>
    <a class="nav-link" href="admin-panel1.php">Receptionist panel</a>
<?php } ?>
   </div>
  </body>
</html>
"""

EDGE_SRC = b"""<!DOCTYPE html>
<?php
session_start();
$con=mysqli_connect("mysql","root","root","legacydb");
$pid = $_SESSION['pid'];
$settings = parse_ini_file('settings.ini');
?>
<html lang="en">
  <body style="padding-top:50px;">
    <h4>Bills for patient <?php echo $pid ?></h4>
<?php if (isset($_GET['ID'])) {
  $query = "select * from prestb p inner join appointmenttb a on p.ID=a.ID where p.pid='$pid' and p.ID='".$_GET['ID']."'";
  $result = mysqli_query($con,$query);
  $rows = mysqli_fetch_all($result, MYSQLI_ASSOC);
?>
    <h5>Appointment <?php echo $_GET['ID'] ?></h5>
    <table class="table table-hover">
<?php foreach ($rows as $i => $row) { ?>
      <tr>
        <td><?php echo $row['doctor'];?></td>
        <td><?php echo $row['prescription'];?></td>
        <td><?php echo number_format($row['docFees'], 2);?></td>
      </tr>
<?php } ?>
    </table>
<?php } ?>
    <p class="footer"><?php echo $settings['hospital_name'] ?></p>
  </body>
</html>
"""


def specs():
    list_while_src = upstream_blob("admin-panel1.php")
    assert list_while_src.count(WHILE_HDR) == 5
    list_queries = {458: {"tables": ["appointmenttb"], "columns": APPOINTMENT_COLUMNS, "ambiguous": False}}
    return {
        "list_while": {
            "file_id": "f001", "entrypoint": "legacy-apps/hms/admin-panel1.php", "src": list_while_src,
            "lines": (414, 562), "queries": list_queries, "var_kinds": {},
            "provenance": {
                "source": "legacy-apps/hms/admin-panel1.php", "upstream": UPSTREAM,
                "scenario": "list page: appointment table rendered by a Stmt_While loop",
                "notes": [
                    "Excerpt: the 'Appointment Details' tab (#list-app). Entries are whole lexer tokens, so the "
                    "first and last Stmt_InlineHTML also carry the neighbouring tabs' closing/opening markup.",
                    "{query} is `select * from appointmenttb;`; columns are the expansion of * from "
                    "legacy-apps/sql/myhmsdb.sql.",
                    "Deliberate contract gap: `contact` is selected by {query} and echoed (line 468) but is absent "
                    "from the contract's Appointment schema (GET /api/appointments).",
                ]},
        },
        "list_foreach": {
            "file_id": "f002",
            "entrypoint": "hand-written: list_foreach.php (legacy-apps/hms/admin-panel1.php with every while loop "
                          "rewritten as foreach)",
            "src": list_while_src.replace(WHILE_HDR, FOREACH_HDR),
            "lines": (414, 562), "queries": list_queries, "var_kinds": {},
            "provenance": {
                "source": "hand-written", "derivedFrom": "legacy-apps/hms/admin-panel1.php", "upstream": UPSTREAM,
                "scenario": "list page: same page as timeline_list_while.json, loop written as Stmt_Foreach",
                "rewrite": {"from": WHILE_HDR.decode(), "to": FOREACH_HDR.decode(),
                            "appliedTo": "all 5 loops in admin-panel1.php (lines 279, 330, 386, 459, 569); same "
                                         "line, so line numbers are unchanged"},
                "notes": [
                    "The only source change is the loop header. Stmt_InlineHTML raw values are therefore "
                    "byte-identical to timeline_list_while.json; ids differ because the file id and token "
                    "ordinals differ.",
                    "Must infer the same component tree as timeline_list_while.json.",
                ]},
        },
        "admin": {
            "file_id": "f003",
            "entrypoint": "hand-written: admin_dashboard.php (modelled on legacy-apps/hms/admin-panel.php)",
            "src": ADMIN_SRC, "lines": (1, 10_000),
            "queries": {14: {"tables": ["appointmenttb"], "columns": ["total", "revenue"], "ambiguous": False}},
            "var_kinds": {"$username": "session"},
            "provenance": {
                "source": "hand-written",
                "modelledOn": "legacy-apps/hms/admin-panel.php lines 9 and 225 "
                              "($_SESSION['username'] -> $username -> echo)",
                "scenario": "admin page: output gated by $_SESSION role checks",
                "handWrittenSource": ADMIN_SRC.decode(),
                "notes": [
                    "Hand-written because HMS has no $_SESSION guard around output anywhere "
                    "(include/checklogin.php is never included).",
                    "Guard at line 12 wraps a query (authorization: must stay server-side). Guard at line 25 wraps "
                    "only markup (display-only). Both test $_SESSION['role'].",
                    "Session reads ($username, $_SESSION['role']) are not API fields and do not appear in the "
                    "contract; reconciliation must exclude sourceKind 'session' rather than flag it.",
                    "Covers Expr_Print (line 19) and an HTML entity (&#8377;) in raw.",
                    "{query} columns are result-set names (aliases), since the query selects aggregates.",
                ]},
        },
        "detail": {
            "file_id": "f004", "entrypoint": "legacy-apps/hms/admin-panel.php",
            "src": upstream_blob("admin-panel.php"), "lines": (360, 519),
            "queries": {473: {"tables": ["appointmenttb"],
                              "columns": ["ID", "doctor", "docFees", "appdate", "apptime", "userStatus",
                                          "doctorStatus"],
                              "ambiguous": False}},
            "var_kinds": {},
            "provenance": {
                "source": "legacy-apps/hms/admin-panel.php", "upstream": UPSTREAM,
                "scenario": "detail page: static markup plus a guard nested inside a loop",
                "notes": [
                    "Excerpt: the booking form's tail and the 'Appointment History' tab (#app-hist). Chosen because "
                    "HMS has no DB-backed single-record page; this is a per-patient list, not a single record.",
                    "Source file uses CRLF line endings; raw values keep the \\r\\n.",
                    "The else branch (line 511) is recorded as branch \"else\" of the Stmt_If at line 504.",
                    "{query}'s WHERE clause filters on $fname/$lname, which the page reads from $_SESSION "
                    "(lines 11, 13).",
                    "No contract gap: every db field read here is in the PatientAppointment schema (success path).",
                ]},
        },
        "edge_cases": {
            "file_id": "f005",
            "entrypoint": "hand-written: edge_cases.php (modelled on generate_bill() in legacy-apps/hms/admin-panel.php)",
            "src": EDGE_SRC, "lines": (1, 10_000),
            "queries": {13: {"tables": ["prestb", "appointmenttb"], "columns": None, "ambiguous": True}},
            "var_kinds": {"$pid": "session", "$settings": "unresolved"},
            "provenance": {
                "source": "hand-written",
                "modelledOn": "generate_bill() in legacy-apps/hms/admin-panel.php (prestb JOIN appointmenttb)",
                "scenario": "schema 1.0 edge cases: ambiguous SELECT *, unresolved read, foreach nested in an if",
                "handWrittenSource": EDGE_SRC.decode(),
                "notes": [
                    "Hand-written: none of the other mocks can hold these cases without changing their raw values.",
                    "{query} is SELECT * over prestb JOIN appointmenttb. Both tables have ID, pid, fname, lname, "
                    "doctor, appdate and apptime, and mysqli_fetch_all(MYSQLI_ASSOC) keeps only the last duplicate, "
                    "so every $row read is confidence 'ambiguous' with table/column null.",
                    "$settings comes from parse_ini_file('settings.ini'): its values are only known at runtime, so "
                    "$settings['hospital_name'] is sourceKind 'unresolved'.",
                    "The foreach (line 18) is nested inside the if (line 11) and uses a key variable ($i).",
                    "number_format($row['docFees'], 2) is sourceKind 'computed' with derivedFrom.",
                    "$_GET['ID'] is echoed unescaped (line 16): a request read, and a reflected-XSS bug kept as-is.",
                    "Not covered by the contract: this page has no endpoint; use it for timeline parsing and "
                    "boundary inference, not reconciliation.",
                ]},
        },
    }


def generate_all() -> dict[str, dict]:
    """Return {filename: document} for every timeline and labels file."""
    php = find_php()
    if php is None:
        raise RuntimeError("PHP not found: set PHP_BIN or put php on PATH")
    lexer_note = f"PHP {php_version(php)} token_get_all(); raw = T_INLINE_HTML token text, byte-exact"
    CONDITIONS.clear()
    docs = {}
    for name, spec in specs().items():
        timeline, labels = build(spec, php, lexer_note)
        docs[f"timeline_{name}.json"] = timeline
        docs[f"labels_{name}.json"] = labels
    return docs


def main():
    for filename, doc in generate_all().items():
        with open(MOCKS / filename, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if filename.startswith("timeline_"):
            print(f"{filename}: {len(doc['sequence'])} entries, {len(doc['queries'])} queries, "
                  f"lines {doc['_provenance']['linesCovered']}")
        else:
            print(f"{filename}: {len(doc['labels'])} labels")


if __name__ == "__main__":
    main()

"""Output timeline for C2 (src/c1/timeline.py), format of component2_frontend/CONTEXT.md."""

from __future__ import annotations

from c1.endpoints import EndpointRecovery
from c1.timeline import Timeline
from pipeline import Run
from test_endpoints import SCHEMA


def timeline(files, schema=SCHEMA):
    r = Run(files)
    src = {a.file_id: r.files[a.path].encode() for a in r.asts}
    er = EndpointRecovery(r.asts, r.cfgs, r.includes, r.dfgs, r.labels, schema, src)
    tl = Timeline(er, src)
    tl.generated_for = "test"
    tl.entry_set = set(er.entry_pages())
    return r, {a.path: tl.file_doc(a.file_id) for a in r.asts}


LIST = """<?php
$result = mysqli_query($con, "select * from patreg");
?>
<table>
<?php while ($row = mysqli_fetch_array($result)) { ?>
  <tr><td><?php echo $row['fname']; ?></td>
  <?php if ($row['pid'] == 1) { echo "Active"; } else { echo "Cancelled"; } ?>
  </tr>
<?php } ?>
</table>
"""


def test_entries_are_output_statements_in_source_order_with_raw_and_loc():
    r, docs = timeline({"list.php": LIST})
    seq = docs["list.php"]["sequence"]
    kinds = [e["kind"] for e in seq]
    assert kinds[0] == "Stmt_InlineHTML" and "Stmt_Echo" in kinds
    assert [e["id"] for e in seq] == sorted(e["id"] for e in seq)
    first = seq[0]
    assert first["raw"] == r.asts[0].attr(first["id"], "value")
    assert set(first["loc"]) == {"startLine", "endLine", "startCol", "endCol", "startByte", "endByte"}


def test_loop_and_branch_enclosures_outermost_first():
    _, docs = timeline({"list.php": LIST})
    seq = docs["list.php"]["sequence"]
    active = next(e for e in seq if e.get("raw") == 'echo "Active";')
    loop, branch = active["enclosedBy"]
    assert loop["role"] == "iteration" and loop["kind"] == "Stmt_While"
    assert loop["iterExpr"] == "$row = mysqli_fetch_array($result)" and loop["valueVar"] == "$row"
    assert loop["iterSourceKind"] == "db_row_field" and loop["keyVar"] is None
    assert branch["role"] == "branch" and branch["branch"] == "then" and branch["condExpr"] == "$row['pid'] == 1"
    cancelled = next(e for e in seq if e.get("raw") == 'echo "Cancelled";')
    else_branch = cancelled["enclosedBy"][1]
    assert else_branch["branch"] == "else" and "condExpr" not in else_branch
    assert else_branch["condNodeId"] == branch["condNodeId"]


def test_db_read_with_schema_expanded_select_star():
    # C2 Q1: SELECT * resolved only when the schema has the table and there is one table
    _, docs = timeline({"list.php": LIST})
    echo = next(e for e in docs["list.php"]["sequence"] if e.get("raw") == "echo $row['fname'];")
    [rd] = echo["reads"]
    assert rd["sourceKind"] == "db_row_field" and rd["confidence"] == "resolved"
    assert rd["basis"] == "schema-expanded" and rd["var"] == "$row" and rd["path"] == ["fname"]
    assert rd["source"]["table"] == "patreg" and rd["source"]["column"] == "fname"
    assert rd["source"]["fetchNodeId"] and rd["source"]["queryNodeId"]


def test_select_star_without_schema_is_ambiguous():
    _, docs = timeline({"list.php": LIST}, schema={})
    echo = next(e for e in docs["list.php"]["sequence"] if e.get("raw") == "echo $row['fname'];")
    assert echo["reads"][0]["confidence"] == "ambiguous"


def test_session_request_literal_and_computed_reads():
    src = "<?php\necho $_SESSION['username'];\necho $_GET['q'];\necho 'hi';\necho htmlspecialchars($_GET['q']);\n"
    _, docs = timeline({"p.php": src})
    reads = [e["reads"][0] for e in docs["p.php"]["sequence"]]
    assert [x["sourceKind"] for x in reads] == ["session", "request", "literal", "computed"]
    assert reads[3]["derivedFrom"][0]["sourceKind"] == "request"


def test_interpolated_string_lists_its_parts():
    # C2 Q16: an echo of "<tr>...$x...</tr>" points to its string parts
    src = "<?php\n$row = mysqli_fetch_array(mysqli_query($c, 'select fname from patreg'));\necho \"<td>{$row['fname']}</td>\";\n"
    _, docs = timeline({"p.php": src})
    e = docs["p.php"]["sequence"][0]
    assert [p["kind"] for p in e["parts"]] == ["literal", "expr", "literal"]
    assert e["parts"][0]["text"] == "<td>"
    assert e["reads"][0]["source"]["column"] == "fname"


def test_call_entry_for_a_function_that_outputs_and_its_own_timeline():
    # C2 Q4: no inlining; the call site gets an entry, the function keeps its echoes
    src = "<?php\nfunction menu() { echo '<ul></ul>'; }\nfunction calc() { return 1; }\nmenu();\ncalc();\n"
    _, docs = timeline({"p.php": src})
    d = docs["p.php"]
    calls = [e for e in d["sequence"] if e["kind"] == "call"]
    assert [(c["calleeName"], c["outputsInside"]) for c in calls] == [("menu", True)]
    [fn] = d["functions"].values()
    assert fn["name"] == "menu" and fn["sequence"][0]["kind"] == "Stmt_Echo"


def test_include_entry():
    _, docs = timeline({"p.php": "<?php\ninclude 'h.php';\n?>\n<p>x</p>\n", "h.php": "<header></header>\n"})
    inc = docs["p.php"]["sequence"][0]
    assert inc["kind"] == "include" and inc["target"] == "h.php" and inc["outputsInside"] is True


def test_value_from_a_helper_returning_row_or_false_is_still_the_column():
    files = {"lib.php": """<?php
class P { static function get($id) {
  $r = mysql_query(sprintf("SELECT * from patreg where pid = '%d'", $id));
  if ($row = mysql_fetch_assoc($r)) { return $row; } else { return False; } } }
""", "v.php": "<?php\nrequire_once('lib.php');\n$p = P::get($_GET['id']);\necho h($p['fname']);\n"}
    _, docs = timeline(files)
    e = docs["v.php"]["sequence"][-1]
    leaf = e["reads"][0]["derivedFrom"][0]
    assert leaf["sourceKind"] == "db_row_field" and leaf["mayBeAbsent"] is True and leaf["source"]["column"] == "fname"


def test_timeline_is_deterministic():
    assert timeline({"list.php": LIST})[1] == timeline({"list.php": LIST})[1]

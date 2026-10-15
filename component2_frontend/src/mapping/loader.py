"""The only module that reads the backend member's JSON formats.

It validates timeline_*.json / labels_*.json against schema 1.0 and converts
them into the types in src/model.py. Nothing downstream touches the JSON.
"""

import json
import re
from pathlib import Path

from src.model import (
    Ast,
    AstNode,
    CallArg,
    CallEntry,
    Concern,
    Contract,
    EchoPart,
    Enclosure,
    Endpoint,
    FunctionInfo,
    FunctionParam,
    EndpointRef,
    Label,
    Loc,
    OutputNode,
    Query,
    Read,
    ReadSource,
    Redirect,
    ResponseShape,
    SchemaType,
    Timeline,
)

SCHEMA_VERSION = "1.0"
TIMELINE_KEYS = ("entrypoint", "schemaVersion", "sequence")  # "queries" is an optional extension
# Expr_Exit: exit("msg") / die("msg") as a statement prints its message (harness extension, pending Q20)
OUTPUT_KINDS = ("Stmt_InlineHTML", "Stmt_Echo", "Expr_Print", "Expr_Exit")
CALL_KIND = "call"  # a call site (Member 01 Q4, proposed): {id, kind, callee, outputsInside, enclosedBy}
# an include / require at its position (harness extension, pending Q4): {id, kind, path, outputsInside, enclosedBy}
INCLUDE_KIND = "include"
SLOT_KINDS = (CALL_KIND, INCLUDE_KIND)
LOOP_KINDS = ("Stmt_While", "Stmt_Foreach", "Stmt_For", "Stmt_Do")
BRANCHES = ("then", "elseif", "else")
# In the schema, but their fields are not specified yet: accepted and carried through as-is.
OTHER_ROLES = ("switch_case", "try_catch")
ENCLOSURE_BASE_KEYS = {"nodeId", "kind", "role"}
SOURCE_KINDS = ("db_row_field", "session", "request", "server", "literal", "computed", "unresolved")
CONFIDENCES = ("resolved", "ambiguous", "unresolved")
CONCERNS = tuple(c.value for c in Concern)
NODE_ID = re.compile(r"^[A-Za-z0-9_]+#\d+$")

LOOP_KEYS = {"nodeId", "kind", "role", "iterExpr", "iterSourceKind", "valueVar", "keyVar"}
BRANCH_KEYS = {"nodeId", "kind", "role", "branch", "condNodeId"}
READ_KEYS = {"expr", "var", "path", "sourceKind", "confidence", "source"}
SOURCE_KEYS = {"fetchNodeId", "queryNodeId", "table", "column"}
QUERY_KEYS = {"line", "sql", "tables", "columns", "resultVar"}
PART_KEYS = {"literal": {"kind", "value", "nodeId"}, "expr": {"kind", "expr", "nodeId", "read"}}
LABEL_KEYS = {"concern", "basis", "ruleId", "reason"}
LOC_KEYS = {"startLine", "endLine", "startCol", "endCol", "startByte", "endByte"}
AST_KEYS = {"schemaVersion", "fileId", "path", "sha256", "bytes", "parseOk", "parseErrors", "rootId", "nodeCount",
            "nodes"}
AST_NODE_KEYS = {"id", "kind", "parent", "children", "fields", "attrs", "loc"}
AST_ROOT_KIND = "File"


# ----------------------------------------------------------------------------- schema 1.0 checks


def _check_node_id(value, where: str) -> None:
    if not isinstance(value, str) or not NODE_ID.match(value):
        raise ValueError(f"{where}: {value!r} is not a '<fileId>#<ordinal>' node id")


def _check_read(read: dict, where: str) -> None:
    keys = READ_KEYS | ({"derivedFrom"} if read.get("sourceKind") == "computed" else set())
    # basis is optional (Member 01 Q1, proposed): how a resolved db read was decided ("schema-expanded").
    if not keys <= set(read) <= keys | {"basis"}:
        raise ValueError(f"{where}: read keys {sorted(read)} != {sorted(keys)} (and optionally basis)")
    where = f"{where} read {read['expr']!r}"
    kind, confidence = read["sourceKind"], read["confidence"]
    if kind not in SOURCE_KINDS:
        raise ValueError(f"{where}: sourceKind {kind!r} not in {SOURCE_KINDS}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"{where}: confidence {confidence!r} not in {CONFIDENCES}")
    if kind in ("literal", "computed"):
        if read["var"] is not None or read["path"] is not None:
            raise ValueError(f"{where}: {kind} reads have null var and path")
    elif not isinstance(read["path"], list):
        raise ValueError(f"{where}: path must be a list")
    if kind == "unresolved" and confidence != "unresolved":
        raise ValueError(f"{where}: sourceKind 'unresolved' needs confidence 'unresolved'")

    source = read["source"]
    if kind == "db_row_field":
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            raise ValueError(f"{where}: db_row_field source needs keys {sorted(SOURCE_KEYS)}")
        _check_node_id(source["fetchNodeId"], where)
        # Not looked up in the queries map: that map is optional, and a read stands on its queryNodeId alone.
        _check_node_id(source["queryNodeId"], where)
        null_target = source["table"] is None and source["column"] is None
        if confidence == "ambiguous" and not null_target:
            raise ValueError(f"{where}: ambiguous db reads have null table and column")
        if confidence == "resolved" and (source["table"] is None or source["column"] is None):
            raise ValueError(f"{where}: resolved db reads name their table and column")
    elif source is not None:
        raise ValueError(f"{where}: only db_row_field reads carry a source")

    if "basis" in read and (not isinstance(read["basis"], str) or not read["basis"]
                            or (kind, confidence) != ("db_row_field", "resolved")):
        raise ValueError(f"{where}: basis is a non-empty string on a resolved db_row_field read only")

    if kind == "computed":
        if not read["derivedFrom"]:
            raise ValueError(f"{where}: computed reads list what they derive from")
        for inner in read["derivedFrom"]:
            _check_read(inner, f"{where} derivedFrom")


def _check_parts(entry: dict, where: str) -> None:
    """parts is optional (harness extension, pending Member 01 Q16): what an echo prints, piece by piece."""
    parts, reads, last = entry["parts"], entry["reads"], -1
    if not isinstance(parts, list):
        raise ValueError(f"{where}: parts must be a list")
    for part in parts:
        kind = part.get("kind") if isinstance(part, dict) else None
        if kind not in PART_KEYS or not PART_KEYS[kind] <= set(part) <= PART_KEYS[kind] | {"loc"}:
            raise ValueError(f"{where}: part {part!r} is not a literal {{kind, value, nodeId}} or an expr "
                             "{kind, expr, nodeId, read}")
        _check_node_id(part["nodeId"], f"{where} part")
        if "loc" in part:
            _check_loc(part["loc"], f"{where} part {part['nodeId']}")
        if kind == "literal":
            if not isinstance(part["value"], str):
                raise ValueError(f"{where}: a literal part's value is its text")
        else:
            index = part["read"]
            if not isinstance(part["expr"], str) or isinstance(index, bool) or not isinstance(index, int) \
                    or not last < index < len(reads):
                raise ValueError(f"{where}: an expr part names its own read by index, in order")
            last = index


def _check_argument(arg, where: str) -> None:
    """A call argument or parameter default (harness extension): a typed literal, or an expression's source."""
    literal = isinstance(arg, dict) and arg.get("kind") == "literal" and set(arg) == {"kind", "type", "value"} \
        and arg["type"] in ("string", "number")
    expression = isinstance(arg, dict) and arg.get("kind") == "expr" and set(arg) == {"kind", "expr"} \
        and isinstance(arg["expr"], str)
    if not (literal or expression):
        raise ValueError(f"{where}: {arg!r} is not {{kind: literal, type, value}} or {{kind: expr, expr}}")


def _check_function(function, where: str) -> None:
    """`function` on a function's own timeline (harness extension): its node id, name and parameters."""
    if not isinstance(function, dict) or set(function) != {"nodeId", "name", "params"} \
            or not isinstance(function["name"], str) or not isinstance(function["params"], list):
        raise ValueError(f"{where}: function needs exactly nodeId, name and params")
    _check_node_id(function["nodeId"], f"{where} function")
    for param in function["params"]:
        if not isinstance(param, dict) or not {"name", "default"} <= set(param) <= {"name", "default", "readOnly"} \
                or not isinstance(param["name"], str) or not isinstance(param.get("readOnly", False), bool):
            raise ValueError(f"{where}: parameter {param!r} needs name and default, and optionally readOnly")
        if param["default"] is not None:
            _check_argument(param["default"], f"{where} default of {param['name']}")


def _check_loc(loc, where: str) -> None:
    """loc follows Member 01's convention (Q13-Q15): 1-based lines; 0-based columns and bytes; exclusive ends."""
    if not isinstance(loc, dict) or set(loc) != LOC_KEYS:
        raise ValueError(f"{where}: loc needs exactly {sorted(LOC_KEYS)}")
    if not all(isinstance(loc[k], int) and not isinstance(loc[k], bool) for k in LOC_KEYS):
        raise ValueError(f"{where}: loc positions must be integers")
    if min(loc["startLine"], loc["endLine"]) < 1:
        raise ValueError(f"{where}: loc lines are 1-based")
    if min(loc["startCol"], loc["endCol"], loc["startByte"], loc["endByte"]) < 0:
        raise ValueError(f"{where}: loc columns and bytes are 0-based")
    if loc["endByte"] < loc["startByte"] or (loc["endLine"], loc["endCol"]) < (loc["startLine"], loc["startCol"]):
        raise ValueError(f"{where}: loc ends before it starts")


def _check_exit_output(found, where: str) -> None:
    """exitOutput (harness extension, R-I8c): [{file, line}] of the exit arms a call or include reaches."""
    if not isinstance(found, list) or not all(
            isinstance(x, dict) and set(x) == {"file", "line"} and isinstance(x["file"], str)
            and isinstance(x["line"], int) for x in found):
        raise ValueError(f"{where}: exitOutput must be a list of {{file, line}}")


def _loc(d: dict | None) -> Loc | None:
    if d is None:
        return None
    return Loc(d["startLine"], d["endLine"], d["startCol"], d["endCol"], d["startByte"], d["endByte"])


def _check_enclosure(enc: dict, where: str, loops_seen: dict) -> None:
    """One enclosedBy item (of a sequence entry or a redirect)."""
    _check_node_id(enc.get("nodeId"), where)
    if "loc" in enc and enc.get("role") in ("iteration", "branch"):
        _check_loc(enc["loc"], f"{where} enclosure {enc['nodeId']}")  # optional (Member 01 Q15)
    if enc.get("role") == "iteration":
        if (not LOOP_KEYS <= set(enc) <= LOOP_KEYS | {"loc", "fetchNodeId"} or enc["kind"] not in LOOP_KINDS
                or enc["iterSourceKind"] not in SOURCE_KINDS):
            raise ValueError(f"{where}: malformed loop {enc}")
        if "fetchNodeId" in enc:
            _check_node_id(enc["fetchNodeId"], where)
        # The loop object is repeated on every entry it encloses; copies must agree.
        if loops_seen.setdefault(enc["nodeId"], enc) != enc:
            raise ValueError(f"{where}: loop {enc['nodeId']} differs from its earlier copy")
    elif enc.get("role") == "branch":
        # condExpr is optional (agent extension, pending Member 01 Q2): the condition's PHP source.
        if (not BRANCH_KEYS <= set(enc) <= BRANCH_KEYS | {"condExpr", "loc", "exits"}
                or enc["kind"] != "Stmt_If" or enc["branch"] not in BRANCHES
                or not isinstance(enc.get("exits", False), bool)):
            raise ValueError(f"{where}: malformed branch {enc}")
        _check_node_id(enc["condNodeId"], where)
        if "condExpr" in enc and (enc["branch"] == "else" or not isinstance(enc["condExpr"], str)
                                  or not enc["condExpr"].strip()):
            raise ValueError(f"{where}: condExpr must be non-empty source text, and an else has none")
    elif enc.get("role") in OTHER_ROLES:
        # Only what every enclosure has is checked; any other field is carried through.
        if not isinstance(enc.get("kind"), str):
            raise ValueError(f"{where}: {enc['role']} enclosure {enc['nodeId']} has no kind")
    else:
        roles = ("iteration", "branch", *OTHER_ROLES)
        raise ValueError(f"{where}: enclosedBy role {enc.get('role')!r}, expected one of {roles}")


def load_timeline_json(path: Path) -> dict:
    """Read a timeline file and check it against schema 1.0; return the validated JSON.

    Raises ValueError naming the first offending key, so a malformed mock
    or a format drift in the real analysis output fails loudly here rather
    than deep inside a pipeline step.
    """
    # newline="" keeps "\r\n" inside JSON strings untouched; Stmt_InlineHTML
    # raw values must stay byte-exact.
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        timeline = json.load(f)

    if "guards" in timeline or "loops" in timeline:
        raise ValueError(f"{path}: schema 0.1 timeline (guards/loops maps); migrate it to schema {SCHEMA_VERSION}")
    missing = [k for k in TIMELINE_KEYS if k not in timeline]
    if missing:
        raise ValueError(f"{path}: timeline is missing top-level keys {missing}")
    if timeline["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"{path}: schemaVersion {timeline['schemaVersion']!r}, expected {SCHEMA_VERSION!r}")
    if "function" in timeline:
        _check_function(timeline["function"], str(path))
    content_type = timeline.get("responseContentType")
    if "responseContentType" in timeline and (not isinstance(content_type, str) or not content_type.strip()):
        raise ValueError(f"{path}: responseContentType must be a media type such as \"text/xml\"")

    for qid, query in timeline.get("queries", {}).items():
        _check_node_id(qid, f"{path}: queries")
        if not QUERY_KEYS <= set(query) <= QUERY_KEYS | {"valuesInterpolated"}:
            raise ValueError(f"{path}: query {qid} keys {sorted(query)} != {sorted(QUERY_KEYS)}")
        if not isinstance(query.get("valuesInterpolated", False), bool):
            raise ValueError(f"{path}: query {qid}: valuesInterpolated must be true or false")

    seen, loops_seen = set(), {}
    for entry in timeline["sequence"]:
        eid = entry.get("id", "<no id>")
        where = f"{path}: {eid}"
        _check_node_id(eid, where)
        if eid in seen:
            raise ValueError(f"{where}: duplicate sequence id")
        seen.add(eid)
        if entry.get("kind") == CALL_KIND:
            required = {"id", "kind", "enclosedBy", "callee", "outputsInside"}
            if not required <= set(entry) <= required | {"loc", "args", "exitOutput"}:
                raise ValueError(f"{where}: call keys {sorted(entry)}; expected {sorted(required)} and optionally "
                                 "loc / args / exitOutput")
            _check_exit_output(entry.get("exitOutput", []), where)
            for arg in entry.get("args", []):
                _check_argument(arg, f"{where} argument")
            if entry["callee"] is not None:
                _check_node_id(entry["callee"], f"{where} callee")
            if not isinstance(entry["outputsInside"], bool) and entry["outputsInside"] != "unknown":
                raise ValueError(f"{where}: outputsInside {entry['outputsInside']!r}, expected true, false or "
                                 '"unknown"')
        elif entry.get("kind") == INCLUDE_KIND:
            required = {"id", "kind", "enclosedBy", "path", "outputsInside"}
            if not required <= set(entry) <= required | {"loc", "exitOutput", "whitespace"}:
                raise ValueError(f"{where}: include keys {sorted(entry)}; expected {sorted(required)} and "
                                 "optionally loc / exitOutput / whitespace")
            _check_exit_output(entry.get("exitOutput", []), where)
            if "whitespace" in entry:  # boundary-rules v1.0 section 4: whitespace-only included output
                text = entry["whitespace"]
                if not isinstance(text, str) or not text or text.strip(" \t\n\f\r") \
                        or entry["outputsInside"] is not False:
                    raise ValueError(f"{where}: whitespace must be non-empty HTML whitespace, on an include that "
                                     "does not print otherwise")
            if entry["path"] is not None and (not isinstance(entry["path"], str) or not entry["path"]):
                raise ValueError(f"{where}: include path {entry['path']!r}, expected a file's entrypoint or null")
            if not isinstance(entry["outputsInside"], bool) and entry["outputsInside"] != "unknown":
                raise ValueError(f"{where}: outputsInside {entry['outputsInside']!r}, expected true, false or "
                                 '"unknown"')
        elif entry.get("kind") not in OUTPUT_KINDS:
            raise ValueError(f"{where}: kind {entry.get('kind')!r}, expected one of {OUTPUT_KINDS + SLOT_KINDS}")
        else:
            payload = "raw" if entry["kind"] == "Stmt_InlineHTML" else "reads"
            required = {"id", "kind", "enclosedBy", payload}
            optional = {"loc"} | ({"parts"} if payload == "reads" else set())
            if not required <= set(entry) <= required | optional:
                raise ValueError(f"{where}: {entry['kind']} keys {sorted(entry)}; expected id/kind/enclosedBy/"
                                 f"{payload} and optionally {' / '.join(sorted(optional))}")
        if "loc" in entry:
            _check_loc(entry["loc"], where)
        for read in entry.get("reads", []):
            _check_read(read, where)
        if "parts" in entry:
            _check_parts(entry, where)

        for enc in entry["enclosedBy"]:
            _check_enclosure(enc, where, loops_seen)

    for redirect in timeline.get("redirects", []):  # harness extension (spec v0.10, header_redirect)
        where = f"{path}: redirect {redirect.get('nodeId', '<no id>')}"
        _check_node_id(redirect.get("nodeId"), where)
        if not {"nodeId", "target", "enclosedBy"} <= set(redirect) <= {"nodeId", "target", "enclosedBy", "loc"}                 or not isinstance(redirect["target"], str):
            raise ValueError(f"{where}: redirect keys {sorted(redirect)}; expected nodeId / target / enclosedBy "
                             "and optionally loc")
        if "loc" in redirect:
            _check_loc(redirect["loc"], where)
        for enc in redirect["enclosedBy"]:
            _check_enclosure(enc, where, loops_seen)

    return timeline


def load_labels_json(path: Path) -> dict:
    """Read a concern-labels file (labels_*.json), check it against schema 1.0; return the validated JSON."""
    with Path(path).open("r", encoding="utf-8") as f:
        labels = json.load(f)
    if set(labels) != {"schemaVersion", "labels"} or labels["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"{path}: expected {{schemaVersion: {SCHEMA_VERSION!r}, labels}}")
    for node_id, lab in labels["labels"].items():
        _check_node_id(node_id, f"{path}: labels")
        if set(lab) != LABEL_KEYS:
            raise ValueError(f"{path}: {node_id} label keys {sorted(lab)} != {sorted(LABEL_KEYS)}")
        if lab["concern"] not in CONCERNS:
            raise ValueError(f"{path}: {node_id} concern {lab['concern']!r} not in {CONCERNS}")
    return labels


# ----------------------------------------------------------------------------- JSON -> model


def _read(d: dict) -> Read:
    src = d["source"]
    return Read(
        expr=d["expr"],
        var=d["var"],
        path=None if d["path"] is None else tuple(d["path"]),
        source_kind=d["sourceKind"],
        confidence=d["confidence"],
        source=None if src is None else ReadSource(src["fetchNodeId"], src["queryNodeId"], src["table"], src["column"]),
        derived_from=tuple(_read(x) for x in d.get("derivedFrom", ())),
        basis=d.get("basis"),
    )


def _argument(d: dict) -> CallArg:
    return CallArg(kind=d["kind"], type=d.get("type"), value=d.get("value"), expr=d.get("expr"))


def _enclosure(d: dict) -> Enclosure:
    if d["role"] == "iteration":
        return Enclosure(node_id=d["nodeId"], kind=d["kind"], role="iteration", iter_expr=d["iterExpr"],
                         iter_source_kind=d["iterSourceKind"], value_var=d["valueVar"], key_var=d["keyVar"],
                         loc=_loc(d.get("loc")), fetch_node_id=d.get("fetchNodeId"))
    if d["role"] == "branch":
        return Enclosure(node_id=d["nodeId"], kind=d["kind"], role="branch", branch=d["branch"],
                         cond_node_id=d["condNodeId"], cond_expr=d.get("condExpr"), loc=_loc(d.get("loc")),
                         exits=d.get("exits", False))
    extra = tuple(sorted((k, v) for k, v in d.items() if k not in ENCLOSURE_BASE_KEYS))
    return Enclosure(node_id=d["nodeId"], kind=d["kind"], role=d["role"], extra=extra)


def load_timeline(path: Path) -> Timeline:
    """Load and validate a timeline_*.json file."""
    doc = load_timeline_json(path)
    sequence = tuple(
        OutputNode(
            id=e["id"],
            kind=e["kind"],
            raw=e.get("raw"),
            reads=tuple(_read(r) for r in e.get("reads", ())),
            enclosed_by=tuple(_enclosure(x) for x in e["enclosedBy"]),
            loc=_loc(e.get("loc")),
            parts=tuple(EchoPart(kind=p["kind"], node_id=p["nodeId"], value=p.get("value"), expr=p.get("expr"),
                                 read=p.get("read"), loc=_loc(p.get("loc"))) for p in e.get("parts", ())),
        )
        for e in doc["sequence"] if e["kind"] not in SLOT_KINDS
    )
    calls = tuple(
        CallEntry(id=e["id"], callee=e["callee"] if e["kind"] == CALL_KIND else e["path"],
                  outputs_inside=e["outputsInside"],
                  enclosed_by=tuple(_enclosure(x) for x in e["enclosedBy"]), loc=_loc(e.get("loc")),
                  args=None if "args" not in e else tuple(_argument(a) for a in e["args"]), kind=e["kind"],
                  exit_output=tuple((x["file"], x["line"]) for x in e.get("exitOutput", ())),
                  whitespace=e.get("whitespace", ""))
        for e in doc["sequence"] if e["kind"] in SLOT_KINDS
    )
    function = doc.get("function")
    if function is not None:
        function = FunctionInfo(function["nodeId"], function["name"], tuple(
            FunctionParam(p["name"], None if p["default"] is None else _argument(p["default"]),
                          p.get("readOnly", False))
            for p in function["params"]))
    queries = {
        qid: Query(node_id=qid, line=q["line"], sql=q["sql"], tables=tuple(q["tables"]),
                   columns=None if q["columns"] is None else tuple(q["columns"]), result_var=q["resultVar"],
                   values_interpolated=q.get("valuesInterpolated", False))
        for qid, q in doc.get("queries", {}).items()
    }
    redirects = tuple(Redirect(node_id=r["nodeId"], target=r["target"], loc=_loc(r.get("loc")),
                               enclosed_by=tuple(_enclosure(x) for x in r["enclosedBy"]))
                      for r in doc.get("redirects", ()))
    return Timeline(entrypoint=doc["entrypoint"], schema_version=doc["schemaVersion"], sequence=sequence,
                    queries=queries, provenance=doc.get("_provenance", {}), calls=calls, function=function,
                    order=tuple(e["id"] for e in doc["sequence"]),
                    content_type=doc.get("responseContentType"), redirects=redirects)


def load_labels(path: Path) -> dict[str, Label]:
    """Load and validate a labels_*.json file, keyed by node id."""
    doc = load_labels_json(path)
    return {
        node_id: Label(concern=Concern(lab["concern"]), basis=lab["basis"], rule_id=lab["ruleId"],
                       reason=lab["reason"])
        for node_id, lab in doc["labels"].items()
    }


# ----------------------------------------------------------------------------- AST (Member 01, ast/<fileId>.json)


def _check_ast_ref(value, nodes: dict, where: str) -> None:
    if not isinstance(value, str) or value not in nodes:
        raise ValueError(f"{where}: {value!r} is not a node of this AST")


def load_ast_json(path: Path) -> dict:
    """Read an AST file and check it against schema 1.0; return the validated JSON.

    Node ids are "<fileId>#<ordinal>", numbered in pre-order from the root `File` at ordinal 0, so the
    order of `nodes`, every `parent` and every `children` list must agree with the ids.
    """
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or set(doc) != AST_KEYS:
        keys = sorted(doc) if isinstance(doc, dict) else type(doc).__name__
        raise ValueError(f"{path}: AST keys {keys} != {sorted(AST_KEYS)}")
    if doc["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"{path}: schemaVersion {doc['schemaVersion']!r}, expected {SCHEMA_VERSION!r}")
    if not isinstance(doc["fileId"], str) or not isinstance(doc["path"], str):
        raise ValueError(f"{path}: fileId and path must be strings")
    if not isinstance(doc["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", doc["sha256"]):
        raise ValueError(f"{path}: sha256 must be 64 lower-case hex digits")
    if not isinstance(doc["bytes"], int) or isinstance(doc["bytes"], bool) or doc["bytes"] < 0:
        raise ValueError(f"{path}: bytes must be a non-negative integer")
    if not isinstance(doc["parseOk"], bool) or not isinstance(doc["parseErrors"], list):
        raise ValueError(f"{path}: parseOk must be a boolean and parseErrors a list")
    if not isinstance(doc["nodes"], list) or not doc["nodes"] or doc["nodeCount"] != len(doc["nodes"]):
        raise ValueError(f"{path}: nodeCount {doc['nodeCount']!r} does not match the nodes list")

    nodes = {}
    for ordinal, node in enumerate(doc["nodes"]):
        if not isinstance(node, dict) or set(node) != AST_NODE_KEYS:
            keys = sorted(node) if isinstance(node, dict) else type(node).__name__
            raise ValueError(f"{path}: node {ordinal} keys {keys} != {sorted(AST_NODE_KEYS)}")
        expected = f"{doc['fileId']}#{ordinal:05d}"
        if node["id"] != expected:
            raise ValueError(f"{path}: node {ordinal} has id {node['id']!r}; pre-order numbering gives {expected!r}")
        if not isinstance(node["kind"], str) or not node["kind"]:
            raise ValueError(f"{path}: {expected} has no kind")
        if not isinstance(node["children"], list) or not isinstance(node["fields"], dict) \
                or not isinstance(node["attrs"], dict):
            raise ValueError(f"{path}: {expected} needs a children list and fields / attrs objects")
        if node["loc"] is not None:
            _check_loc(node["loc"], f"{path}: {expected}")
            if node["loc"]["endByte"] > doc["bytes"]:
                raise ValueError(f"{path}: {expected} ends after the file's {doc['bytes']} bytes")
        nodes[expected] = node
    root = doc["nodes"][0]
    if doc["rootId"] != root["id"] or root["kind"] != AST_ROOT_KIND or root["parent"] is not None:
        raise ValueError(f"{path}: the first node must be the root {AST_ROOT_KIND!r} named by rootId, with no parent")

    # In pre-order a node's first child follows it and each subtree is contiguous.
    stack = [(root["id"], list(root["children"]))]
    for node in doc["nodes"][1:]:
        where = f"{path}: {node['id']}"
        while stack and not stack[-1][1]:
            stack.pop()
        if not stack or stack[-1][1].pop(0) != node["id"] or node["parent"] != stack[-1][0]:
            raise ValueError(f"{where}: parent / children do not agree with the pre-order ids")
        stack.append((node["id"], list(node["children"])))
    if any(pending for _, pending in stack):
        raise ValueError(f"{path}: children name nodes that are not in the nodes list")
    for node in doc["nodes"]:
        where = f"{path}: {node['id']}"
        for child in node["children"]:
            _check_ast_ref(child, nodes, where)
        for role, value in node["fields"].items():
            for ref in value if isinstance(value, list) else [value]:
                if ref is not None:
                    _check_ast_ref(ref, nodes, f"{where} field {role!r}")
                    if ref not in node["children"]:
                        raise ValueError(f"{where}: field {role!r} names {ref}, which is not one of its children")
    return doc


def load_ast(path: Path) -> Ast:
    """Load and validate one AST file (Member 01's ast/<fileId>.json, or the harness's copy of that format)."""
    doc = load_ast_json(path)
    nodes = {
        n["id"]: AstNode(
            id=n["id"], kind=n["kind"], parent=n["parent"], children=tuple(n["children"]),
            fields={role: tuple(v) if isinstance(v, list) else v for role, v in n["fields"].items()},
            attrs=dict(n["attrs"]), loc=_loc(n["loc"]))
        for n in doc["nodes"]
    }
    return Ast(schema_version=doc["schemaVersion"], file_id=doc["fileId"], path=doc["path"], sha256=doc["sha256"],
               bytes=doc["bytes"], parse_ok=doc["parseOk"], parse_errors=tuple(doc["parseErrors"]),
               root_id=doc["rootId"], node_count=doc["nodeCount"], nodes=nodes)


# ----------------------------------------------------------------------------- legacy fixture


def load_legacy_ast(path: Path) -> dict:
    """Load the pre-timeline node-list fixture (mocks/sample_ast.json).

    Kept so the old fixture still loads; new work should use load_timeline.
    """
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_contract_json(path: Path) -> dict:
    """Read Component 1's OpenAPI contract as JSON (used by the legacy --input/--contract path)."""
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------- contract (OpenAPI 3.x)

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def _is_type(schema: dict, name: str) -> bool:
    kind = schema.get("type")
    return kind == name or (isinstance(kind, list) and name in kind)


def _resolve(doc: dict, schema, path: Path):
    """Follow local $refs (#/components/...) until a schema without one is reached."""
    seen = set()
    while isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise ValueError(f"{path}: unsupported or circular $ref {ref!r}")
        seen.add(ref)
        node = doc
        for part in ref[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                raise ValueError(f"{path}: $ref {ref!r} points nowhere")
            node = node[key]
        schema = node
    return schema


def _response_shape(doc: dict, schema, path: Path) -> ResponseShape | None:
    schema = _resolve(doc, schema, path)
    if not isinstance(schema, dict):
        return None
    if _is_type(schema, "array"):
        items = _resolve(doc, schema.get("items", {}), path)
        return ResponseShape(("",), (("", tuple(items.get("properties", {}))),))
    if _is_type(schema, "object") or "properties" in schema:
        properties = schema.get("properties", {})
        arrays = []
        for name, sub in properties.items():
            sub = _resolve(doc, sub, path)
            if isinstance(sub, dict) and _is_type(sub, "array"):
                items = _resolve(doc, sub.get("items", {}), path)
                arrays.append((name, tuple(items.get("properties", {})) if isinstance(items, dict) else ()))
        return ResponseShape(tuple(properties), tuple(arrays))
    return None


SCHEMA_REF_PREFIX = "#/components/schemas/"
SCALAR_TYPES = ("string", "number", "integer", "boolean")


def _schema_type(doc: dict, schema, path: Path, refs: tuple[str, ...] = ()) -> SchemaType:
    """A response schema as Stage 5 types it. Anything not plainly typed is "unknown", never guessed."""
    ref = None
    if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"].removeprefix(SCHEMA_REF_PREFIX) if schema["$ref"].startswith(SCHEMA_REF_PREFIX) else None
        if schema["$ref"] in refs:  # a recursive schema: the inner use stays unknown
            return SchemaType("unknown", ref=ref)
        refs = (*refs, schema["$ref"])
    schema = _resolve(doc, schema, path)
    if not isinstance(schema, dict):
        return SchemaType("unknown", ref=ref)
    declared = schema.get("type")
    types = [declared] if isinstance(declared, str) else list(declared) if isinstance(declared, list) else []
    nullable = "null" in types or schema.get("nullable") is True
    types = [t for t in types if t != "null"]
    if not types and "properties" in schema:
        types = ["object"]
    if len(types) != 1 or any(k in schema for k in ("oneOf", "anyOf", "allOf")):
        return SchemaType("unknown", ref=ref, nullable=nullable)
    kind = types[0]
    if kind == "object":
        properties = tuple((name, _schema_type(doc, sub, path, refs))
                           for name, sub in schema.get("properties", {}).items())
        return SchemaType("object", ref=ref, properties=properties, required=tuple(schema.get("required", ())),
                          nullable=nullable)
    if kind == "array":
        return SchemaType("array", ref=ref, items=_schema_type(doc, schema.get("items", {}), path, refs),
                          nullable=nullable)
    return SchemaType(kind if kind in SCALAR_TYPES else "unknown", ref=ref, nullable=nullable)


def load_contract(path: Path) -> Contract:
    """Load an OpenAPI 3.x contract: for every response with a body, the fields it offers."""
    doc = load_contract_json(path)
    if not str(doc.get("openapi", "")).startswith("3."):
        raise ValueError(f"{path}: not an OpenAPI 3 document")
    endpoints = {}
    for route, item in doc.get("paths", {}).items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            for status, response in operation.get("responses", {}).items():
                response = _resolve(doc, response, path)
                for media_type, content in response.get("content", {}).items():
                    schema = content.get("schema")
                    shape = _response_shape(doc, schema, path) if schema is not None else None
                    typed = _schema_type(doc, schema, path) if schema is not None else None
                    endpoints[(method, route, str(status), media_type)] = Endpoint(
                        method, route, str(status), media_type, shape, typed)
    return Contract(title=doc.get("info", {}).get("title", ""), endpoints=endpoints)


# ----------------------------------------------------------------------------- endpoint map (substitute)

ENDPOINT_REF_KEYS = {"method", "path", "status", "mediaType"}


def load_endpoint_map(path: Path) -> dict[str, EndpointRef]:
    """Load the endpoint map: which contract response serves each timeline entrypoint.

    Component 1 does not produce this map yet; mocks/endpoint_map.json is a substitute.
    When C1's map arrives, only this function changes.
    """
    with Path(path).open("r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc.get("endpoints"), dict):
        raise ValueError(f"{path}: expected an 'endpoints' object keyed by timeline entrypoint")
    refs = {}
    for entrypoint, entry in doc["endpoints"].items():
        if not isinstance(entry, dict) or set(entry) != ENDPOINT_REF_KEYS:
            raise ValueError(f"{path}: {entrypoint!r} needs exactly {sorted(ENDPOINT_REF_KEYS)}")
        if entry["method"].lower() not in HTTP_METHODS:
            raise ValueError(f"{path}: {entrypoint!r} has unknown method {entry['method']!r}")
        refs[entrypoint] = EndpointRef(entry["method"].lower(), entry["path"], str(entry["status"]),
                                       entry["mediaType"])
    return refs


def load_input(path: Path) -> tuple[str, dict]:
    """Detect whether `path` is a timeline or the legacy fixture and return its validated JSON."""
    with Path(path).open("r", encoding="utf-8") as f:
        top_level = json.load(f)
    if "sequence" in top_level:
        return "timeline", load_timeline_json(path)
    if "nodes" in top_level:
        return "legacy_ast", load_legacy_ast(path)
    raise ValueError(f"{path}: neither a timeline ('sequence') nor a legacy AST ('nodes')")

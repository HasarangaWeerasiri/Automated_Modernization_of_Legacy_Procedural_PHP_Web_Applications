"""The only module that reads the backend member's JSON formats.

It validates timeline_*.json / labels_*.json against schema 1.0 and converts
them into the types in src/model.py. Nothing downstream touches the JSON.
"""

import json
import re
from pathlib import Path

from src.model import (
    Concern,
    Contract,
    Enclosure,
    Endpoint,
    EndpointRef,
    Label,
    OutputNode,
    Query,
    Read,
    ReadSource,
    ResponseShape,
    Timeline,
)

SCHEMA_VERSION = "1.0"
TIMELINE_KEYS = ("entrypoint", "schemaVersion", "sequence")  # "queries" is an optional extension
OUTPUT_KINDS = ("Stmt_InlineHTML", "Stmt_Echo", "Expr_Print")
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
LABEL_KEYS = {"concern", "basis", "ruleId", "reason"}


# ----------------------------------------------------------------------------- schema 1.0 checks


def _check_node_id(value, where: str) -> None:
    if not isinstance(value, str) or not NODE_ID.match(value):
        raise ValueError(f"{where}: {value!r} is not a '<fileId>#<ordinal>' node id")


def _check_read(read: dict, where: str) -> None:
    keys = READ_KEYS | ({"derivedFrom"} if read.get("sourceKind") == "computed" else set())
    if set(read) != keys:
        raise ValueError(f"{where}: read keys {sorted(read)} != {sorted(keys)}")
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

    if kind == "computed":
        if not read["derivedFrom"]:
            raise ValueError(f"{where}: computed reads list what they derive from")
        for inner in read["derivedFrom"]:
            _check_read(inner, f"{where} derivedFrom")


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

    for qid, query in timeline.get("queries", {}).items():
        _check_node_id(qid, f"{path}: queries")
        if set(query) != QUERY_KEYS:
            raise ValueError(f"{path}: query {qid} keys {sorted(query)} != {sorted(QUERY_KEYS)}")

    seen, loops_seen = set(), {}
    for entry in timeline["sequence"]:
        eid = entry.get("id", "<no id>")
        where = f"{path}: {eid}"
        _check_node_id(eid, where)
        if eid in seen:
            raise ValueError(f"{where}: duplicate sequence id")
        seen.add(eid)
        if entry.get("kind") not in OUTPUT_KINDS:
            raise ValueError(f"{where}: kind {entry.get('kind')!r}, expected one of {OUTPUT_KINDS}")
        payload = "raw" if entry["kind"] == "Stmt_InlineHTML" else "reads"
        if set(entry) != {"id", "kind", "enclosedBy", payload}:
            raise ValueError(f"{where}: {entry['kind']} keys {sorted(entry)}; expected id/kind/enclosedBy/{payload}")
        for read in entry.get("reads", []):
            _check_read(read, where)

        for enc in entry["enclosedBy"]:
            _check_node_id(enc.get("nodeId"), where)
            if enc.get("role") == "iteration":
                if set(enc) != LOOP_KEYS or enc["kind"] not in LOOP_KINDS or enc["iterSourceKind"] not in SOURCE_KINDS:
                    raise ValueError(f"{where}: malformed loop {enc}")
                # The loop object is repeated on every entry it encloses; copies must agree.
                if loops_seen.setdefault(enc["nodeId"], enc) != enc:
                    raise ValueError(f"{where}: loop {enc['nodeId']} differs from its earlier copy")
            elif enc.get("role") == "branch":
                if set(enc) != BRANCH_KEYS or enc["kind"] != "Stmt_If" or enc["branch"] not in BRANCHES:
                    raise ValueError(f"{where}: malformed branch {enc}")
                _check_node_id(enc["condNodeId"], where)
            elif enc.get("role") in OTHER_ROLES:
                # Only what every enclosure has is checked; any other field is carried through.
                if not isinstance(enc.get("kind"), str):
                    raise ValueError(f"{where}: {enc['role']} enclosure {enc['nodeId']} has no kind")
            else:
                roles = ("iteration", "branch", *OTHER_ROLES)
                raise ValueError(f"{where}: enclosedBy role {enc.get('role')!r}, expected one of {roles}")

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
    )


def _enclosure(d: dict) -> Enclosure:
    if d["role"] == "iteration":
        return Enclosure(node_id=d["nodeId"], kind=d["kind"], role="iteration", iter_expr=d["iterExpr"],
                         iter_source_kind=d["iterSourceKind"], value_var=d["valueVar"], key_var=d["keyVar"])
    if d["role"] == "branch":
        return Enclosure(node_id=d["nodeId"], kind=d["kind"], role="branch", branch=d["branch"],
                         cond_node_id=d["condNodeId"])
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
        )
        for e in doc["sequence"]
    )
    queries = {
        qid: Query(node_id=qid, line=q["line"], sql=q["sql"], tables=tuple(q["tables"]),
                   columns=None if q["columns"] is None else tuple(q["columns"]), result_var=q["resultVar"])
        for qid, q in doc.get("queries", {}).items()
    }
    return Timeline(entrypoint=doc["entrypoint"], schema_version=doc["schemaVersion"], sequence=sequence,
                    queries=queries, provenance=doc.get("_provenance", {}))


def load_labels(path: Path) -> dict[str, Label]:
    """Load and validate a labels_*.json file, keyed by node id."""
    doc = load_labels_json(path)
    return {
        node_id: Label(concern=Concern(lab["concern"]), basis=lab["basis"], rule_id=lab["ruleId"],
                       reason=lab["reason"])
        for node_id, lab in doc["labels"].items()
    }


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
                    endpoints[(method, route, str(status), media_type)] = Endpoint(
                        method, route, str(status), media_type, shape)
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

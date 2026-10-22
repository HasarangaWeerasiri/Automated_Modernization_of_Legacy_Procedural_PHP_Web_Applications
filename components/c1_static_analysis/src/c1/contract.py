"""API contract and backend skeleton (Component 1 steps 9-14).

    python -m c1.contract analysis-out [--title "HMS"]
        -> contract/openapi.json        OpenAPI 3.1, the contract C2 consumes
        -> backend/app/{main,models,auth,routes,data_access}.py   FastAPI + Pydantic v2
        -> flags.json                   everything a human must look at (step 11)

* Pydantic models are the contract (master reference step 13). We also write the
  OpenAPI document ourselves from endpoints.json, so it is deterministic and carries
  `x-c1` provenance (legacy pages, query nodes, guards, flags). `tests/test_contract.py`
  checks that the generated FastAPI app serves the same paths, methods and fields.
* Session -> JWT (step 9): a page guard becomes `Depends(get_current_user)`; a session
  value used in SQL (`WHERE pid = $_SESSION['pid']`) becomes a claim of the token;
  role checks become `require_claim(...)`. JWT via python-jose (RFC 7519).
* Data access is C3's job (injection-safe ORM queries). Each route calls a typed stub
  in data_access.py whose docstring holds the recovered SQL template and the parameter
  mapping, so C3 can implement it, or generate it, without re-reading the PHP.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .extract import canonical_json

PY_TYPES = {"integer": "int", "number": "float", "boolean": "bool"}
PY_FORMATS = {"date": "datetime.date", "date-time": "datetime.datetime", "time": "datetime.time"}



LOCKED_NOTE = ("Locked by default: the legacy page had no login check here. "
               "Mark it public if anyone should reach it (see flags.json).")


def _description(ep: dict) -> str:
    """One text for openapi.json and for the route docstring (which Swagger shows), so they never differ."""
    srcs = ", ".join(f"{s['page']} ({s['legacy']['method']})" for s in ep["sources"][:4])
    flags = sorted({f["flag"] for f in ep["flags"]})
    text = f"{ep['kind']} {ep.get('table') or ep['resource']}. Legacy: {srcs}. Flags: {', '.join(flags) or 'none'}."
    if ep["auth"].get("required") and ep["auth"].get("securedByDefault"):
        text = f"{LOCKED_NOTE} {text}"
    return text

def op_id(ep: dict[str, Any]) -> str:
    parts = re.sub(r"[{}]", "", ep["path"]).strip("/").replace("/", "_")
    return re.sub(r"[^a-zA-Z0-9_]", "_", f"{ep['method'].lower()}_{parts}")


def camel(s: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[_\W]+", s) if p)


def py_type(schema: dict[str, Any]) -> str:
    if schema.get("format") in PY_FORMATS:
        return PY_FORMATS[schema["format"]]
    return PY_TYPES.get(schema.get("type", "string"), "str")


def param_schema(ep: dict[str, Any], p: dict[str, Any], schema_db: dict | None) -> dict[str, Any]:
    tbl, _, col = (p.get("column") or ".").partition(".")
    t = (schema_db or {}).get(tbl, {}).get(col)
    if t:
        from .sqlshape import json_type
        return json_type(t["sqlType"])
    return {"type": "string"}


# -- OpenAPI ---------------------------------------------------------------------------
def build_openapi(rec: dict[str, Any], title: str, schema_db: dict | None) -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    schemas: dict[str, Any] = {
        "Status": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        "Token": {"type": "object", "properties": {"access_token": {"type": "string"}, "token_type": {"type": "string"}},
                  "required": ["access_token", "token_type"]},
    }
    for ep in rec["endpoints"]:
        oid = op_id(ep)
        name = camel(oid)
        op: dict[str, Any] = {"operationId": oid, "tags": [ep["resource"]],
                              "summary": f"{ep['kind']} {ep.get('table') or ep['resource']}"}
        params = []
        body_props: dict[str, Any] = {}
        for p in ep["params"]:
            sch = param_schema(ep, p, schema_db)
            if p["in"] in ("path", "query"):
                params.append({"name": p["name"], "in": p["in"], "required": p["in"] == "path", "schema": sch})
            else:
                body_props[p["name"]] = sch
        # one name may be filled from different legacy fields (several pages): keep one parameter
        seen = set()
        params = [x for x in params if not ((x["name"], x["in"]) in seen or seen.add((x["name"], x["in"])))]
        if params:
            op["parameters"] = sorted(params, key=lambda x: (x["in"] != "path", x["name"]))
        if body_props:
            schemas[f"{name}Body"] = {"type": "object", "properties": dict(sorted(body_props.items())),
                                      "required": sorted(body_props)}
            op["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{name}Body"}}}}
        resp = ep["response"]
        if resp["kind"] in ("list", "object"):
            schemas[f"{name}Item"] = {"type": "object", "properties": dict(sorted(resp["fields"].items())),
                                      "required": sorted(resp["fields"])}
            if resp["kind"] == "list":
                schemas[f"{name}Response"] = {"type": "object", "properties": {
                    resp["recordsAt"]: {"type": "array", "items": {"$ref": f"#/components/schemas/{name}Item"}}},
                    "required": [resp["recordsAt"]]}
                ref = f"#/components/schemas/{name}Response"
            else:
                ref = f"#/components/schemas/{name}Item"
        elif resp["kind"] == "token":
            ref = "#/components/schemas/Token"
        else:
            ref = "#/components/schemas/Status"
        op["responses"] = {"200": {"description": "OK", "content": {"application/json": {"schema": {"$ref": ref}}}}}
        if ep["auth"].get("required"):
            op["security"] = [{"bearerAuth": []}]
            op["responses"]["401"] = {"description": "missing or invalid token"}
        op["description"] = _description(ep)
        op["x-c1"] = {
            "endpointId": ep["id"],
            "sources": [{"page": s["page"], "method": s["legacy"]["method"], "line": s.get("code", {}).get("line"),
                         "code": s.get("code", {}).get("path"), "queries": s["queries"]} for s in ep["sources"]],
            "auth": ep["auth"], "sessionBindings": ep.get("sessionBindings", []),
            "flags": ep["flags"],
        }
        if ep.get("alsoWrites"):
            op["x-c1"]["alsoWrites"] = ep["alsoWrites"]
        if ep.get("sideEffects"):
            op["x-c1"]["sideEffects"] = ep["sideEffects"]
        paths.setdefault(ep["path"], {})[ep["method"].lower()] = op
    return {
        "openapi": "3.1.0",
        "info": {"title": f"{title} API", "version": "0.1.0",
                 "description": "Recovered from the legacy PHP application by Component 1 "
                                "(static analysis, sound-or-abstain). See x-c1 on each operation."},
        "paths": dict(sorted(paths.items())),
        "components": {"schemas": dict(sorted(schemas.items())),
                       "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}},
        "x-c1": {"generatedFor": rec.get("generatedFor"), "tool": __version__},
    }


# -- FastAPI code generation --------------------------------------------------------------------
HEADER = "# Generated by Component 1 (c1-static-analysis-core {v}). Do not edit by hand: re-run c1.contract.\n"


def gen_models(oa: dict[str, Any]) -> str:
    out = [HEADER.format(v=__version__), "from __future__ import annotations\n", "import datetime\n",
           "from pydantic import BaseModel\n\n"]
    schemas = oa["components"]["schemas"]
    # items before responses that reference them
    order = sorted(schemas, key=lambda n: (n.endswith("Response"), n))
    for name in order:
        s = schemas[name]
        out.append(f"class {name}(BaseModel):\n")
        props = s.get("properties", {})
        if not props:
            out.append("    pass\n\n\n")
            continue
        for field, fs in props.items():
            if fs.get("type") == "array":
                inner = fs["items"]["$ref"].rsplit("/", 1)[1]
                out.append(f"    {_py_name(field)}: list[{inner}]{_alias(field)}\n")
            else:
                req = field in s.get("required", [])
                t = py_type(fs)
                out.append(f"    {_py_name(field)}: {t if req else t + ' | None'}{_alias(field, None if req else 'None')}\n")
        out.append("\n\n")
    return "".join(out).rstrip() + "\n"


def _py_name(field: str) -> str:
    n = re.sub(r"\W", "_", field)
    if n in ("from", "class", "def", "return", "global", "pass", "in", "is", "not", "and", "or", "id_") or n[0].isdigit():
        n = n + "_"
    return n


def _alias(field: str, default: str | None = None) -> str:
    if _py_name(field) != field:
        d = f"default={default}, " if default else ""
        return f" = Field({d}alias={field!r})"
    return f" = {default}" if default else ""


def gen_auth(rec: dict[str, Any]) -> str:
    claims = sorted({b["sessionKey"] for ep in rec["endpoints"] for b in ep.get("sessionBindings", [])} |
                    {k for ep in rec["endpoints"] for r in ep["auth"].get("roles", []) for k in r["sessionKeys"]})
    return HEADER.format(v=__version__) + f'''"""Session -> JWT (master reference 3.1 step 9).

Legacy                                   | Here
`session_start()` + `$_SESSION[...]` check | `Depends(get_current_user)` (401 when the token is missing/invalid)
`$_SESSION['<key>']` used in SQL           | claim `<key>` of the token: `user.claims["<key>"]`
`$_SESSION['role'] == 'admin'`             | `Depends(require_claim("role", "admin"))`
Session keys the legacy app reads: {claims}
"""

from __future__ import annotations

import datetime
import os
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

SECRET_KEY = os.environ.get("C1_JWT_SECRET", "change-me")  # set in deployment
ALGORITHM = "HS256"
TOKEN_MINUTES = int(os.environ.get("C1_JWT_MINUTES", "60"))
SESSION_CLAIMS = {claims!r}

_bearer = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, claims: dict[str, Any]):
        self.claims = claims


def create_access_token(claims: dict[str, Any]) -> str:
    payload = dict(claims)
    payload["exp"] = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=TOKEN_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> CurrentUser:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    try:
        return CurrentUser(jwt.decode(cred.credentials, SECRET_KEY, algorithms=[ALGORITHM]))
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from None


def require_claim(name: str, value: Any):
    def check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.claims.get(name) != value:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires {{name}} = {{value!r}}")
        return user
    return check
'''


def _role_literal(cond: str | None) -> tuple[str, str] | None:
    m = re.search(r"\$_SESSION\[['\"](\w+)['\"]\]\s*===?\s*['\"]([^'\"]+)['\"]", cond or "")
    return (m.group(1), m.group(2)) if m else None


def gen_routes(rec: dict[str, Any], oa: dict[str, Any]) -> tuple[str, str]:
    routes = [HEADER.format(v=__version__), "from __future__ import annotations\n\n", "import datetime\n\n",
              "from fastapi import APIRouter, Depends\n\n", "from . import data_access\n",
              "from .auth import CurrentUser, create_access_token, get_current_user, require_claim\n",
              "from .models import *  # noqa: F401,F403\n\n", "router = APIRouter()\n\n"]
    da = [HEADER.format(v=__version__),
          '"""Data access interface. Component 3 implements these (injection-safe SQLAlchemy).\n\n'
          "Each stub lists the legacy SQL template recovered by C1 (§n§ = request value n)\n"
          'and which parameter fills which value. Until C3 lands they raise NotImplementedError."""\n\n',
          "from __future__ import annotations\n\n", "from typing import Any\n\n"]
    for ep in rec["endpoints"]:
        oid = op_id(ep)
        name = camel(oid)
        op = oa["paths"][ep["path"]][ep["method"].lower()]
        args, call_args = [], []
        for p in op.get("parameters", []):
            t = py_type(p["schema"])
            args.append(f"{p['name']}: {t}" if p["required"] else f"{p['name']}: {t} | None = None")
            call_args.append(f"{p['name']}={p['name']}")
        if "requestBody" in op:
            args.insert(0, f"body: {name}Body")
            call_args.append("**body.model_dump(by_alias=True)")
        needs_user = ep["auth"].get("required") or ep.get("sessionBindings")
        role = next((_role_literal(r.get("condExpr")) for r in ep["auth"].get("roles", []) if _role_literal(r.get("condExpr"))), None)
        if role:
            args.append(f"user: CurrentUser = Depends(require_claim({role[0]!r}, {role[1]!r}))")
        elif needs_user:
            args.append("user: CurrentUser = Depends(get_current_user)")
        for b in ep.get("sessionBindings", []):
            call_args.append(f"{_py_name(b['sessionKey'])}=user.claims.get({b['sessionKey']!r})")
        resp = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
        routes.append(f'@router.{ep["method"].lower()}({ep["path"]!r}, response_model={resp}, tags=[{ep["resource"]!r}])\n')
        routes.append(f"def {oid}({', '.join(args)}) -> {resp}:\n")
        # Swagger shows this docstring, so it carries the same text as the contract's description.
        routes.append('    """' + _description(ep) + '"""\n')
        if ep["kind"] == "login":
            routes.append(f"    claims = data_access.{oid}({', '.join(call_args)})\n")
            routes.append('    return Token(access_token=create_access_token(claims), token_type="bearer")\n\n\n')
        elif ep["kind"] == "logout":
            routes.append("    return Status(ok=True)  # stateless JWT: the client drops the token\n\n\n")
        elif ep["response"]["kind"] == "list":
            ra = ep["response"]["recordsAt"]
            routes.append(f"    rows = data_access.{oid}({', '.join(call_args)})\n")
            routes.append(f"    return {resp}(**{{{ra!r}: [{name}Item(**r) for r in rows]}})\n\n\n")
        elif ep["response"]["kind"] == "object":
            routes.append(f"    return {resp}(**data_access.{oid}({', '.join(call_args)}))\n\n\n")
        else:
            routes.append(f"    data_access.{oid}({', '.join(call_args)})\n    return Status(ok=True)\n\n\n")
        # data access stub
        da_params = [a.split(":")[0] for a in args if not a.startswith(("body", "user"))]
        da_params += sorted({p["name"] for p in ep["params"] if p["in"] == "body"})
        da_params += [_py_name(b["sessionKey"]) for b in ep.get("sessionBindings", [])]
        sig = ", ".join(f"{p}: Any = None" for p in dict.fromkeys(da_params))
        da.append(f"def {oid}({sig}) -> Any:\n" if sig else f"def {oid}() -> Any:\n")
        da.append(f'    """{ep["id"]} ({ep["kind"]} {ep.get("table") or ""}).\n\n')
        for s in ep["sources"][:3]:
            for q in s["queries"][:2]:
                sql = rec.get("sql", {}).get(q)
                if sql:
                    da.append(f"    Legacy SQL ({s['code']['path']}:{s['code']['line']}): {sql}\n")
        for p in ep["params"]:
            da.append(f"    {p['name']} <- legacy {p['legacy_in']} field {p['legacy_name']!r} ({p.get('column')})\n")
        for b in ep.get("sessionBindings", []):
            da.append(f"    {b['sessionKey']} <- token claim (legacy $_SESSION[{b['sessionKey']!r}]) ({b['column']})\n")
        da.append('    """\n')
        da.append(f"    raise NotImplementedError({('C3: ' + ep['id'])!r})\n\n\n")
    return "".join(routes).rstrip() + "\n", "".join(da).rstrip() + "\n"


MAIN = '''"""Generated by Component 1. Run: uvicorn app.main:app --reload"""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .routes import router

app = FastAPI(title={title!r}, version="0.1.0")
app.include_router(router)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    """The API has no page of its own at "/": send visitors to the interactive docs."""
    return RedirectResponse("/docs")
'''


def collect_flags(out_dir: Path) -> dict[str, Any]:
    """One file listing everything the tool did not decide (master reference step 11)."""
    items: list[dict[str, Any]] = []
    auth = json.loads((out_dir / "auth.json").read_text(encoding="utf-8"))
    for p in auth["pages"]:
        if p.get("needsDecision"):
            items.append({"stage": "auth", "kind": "unguarded-page", "page": p["page"], "status": p["status"],
                          "unguardedWrites": p.get("unguardedWrites"), "decision": "public page or missing guard?"})
    for c in auth["checks"]:
        if c["effect"] in ("ambiguous", "broken-guard") or c["kind"] == "unknown":
            items.append({"stage": "auth", "kind": f"check:{c['effect']}", "page": c["path"], "line": c["line"],
                          "condExpr": c.get("condExpr"), "reason": c.get("reason")})
    for e in auth["redirectWithoutExit"]:
        items.append({"stage": "auth", "kind": "redirect-without-exit", "page": e["path"], "line": e["line"],
                      "statementsAfter": e["statementsAfter"]})
    eps = json.loads((out_dir / "endpoints.json").read_text(encoding="utf-8"))
    for ep in eps["endpoints"]:
        for f in ep["flags"]:
            items.append({"stage": "endpoints", "kind": f["flag"], "endpoint": ep["id"],
                          **{k: v for k, v in f.items() if k != "flag"}})
    labels = json.loads((out_dir / "labels.json").read_text(encoding="utf-8"))
    und = sum(1 for v in labels["labels"].values() if v["concern"] == "undecided")
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    paths = {f["fileId"]: f["path"] for f in manifest["files"]}
    for nid, v in sorted(labels["labels"].items()):
        if v["ruleId"] == "R19":
            items.append({"stage": "concerns", "kind": "orm-data-access-not-recovered", "page": paths.get(nid.split("#")[0]),
                          "node": nid, "reason": "query-builder/ORM call: no SQL to recover, no endpoint made (out of scope)"})
    inc = json.loads((out_dir / "index" / "includes.json").read_text(encoding="utf-8"))
    for s in inc["sites"]:
        if s["self"]["status"] not in ("resolved", "excluded"):
            items.append({"stage": "includes", "kind": f"include-{s['self']['status']}", "page": s["path"],
                          "line": s["line"], "pattern": s["self"].get("pattern"), "reason": s["self"].get("reason")})
    counts: dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {"schemaVersion": "1.0", "generatedFor": eps.get("generatedFor"),
            "undecidedLabels": und, "items": items, "totals": dict(sorted(counts.items()))}


def run(out_dir: Path, title: str) -> dict[str, Any]:
    rec = json.loads((out_dir / "endpoints.json").read_text(encoding="utf-8"))
    schema_db = rec.get("schema")
    oa = build_openapi(rec, title, schema_db)
    (out_dir / "contract").mkdir(parents=True, exist_ok=True)
    (out_dir / "contract" / "openapi.json").write_bytes(canonical_json(oa, pretty=True))
    write_backend(out_dir / "backend", rec, oa, title)
    flags = collect_flags(out_dir)
    (out_dir / "flags.json").write_bytes(canonical_json(flags, pretty=True))
    return {"operations": sum(len(v) for v in oa["paths"].values()), "schemas": len(oa["components"]["schemas"]),
            "flags": len(flags["items"])}


def write_backend(root: Path, rec: dict[str, Any], oa: dict[str, Any], title: str) -> None:
    app = root / "app"
    app.mkdir(parents=True, exist_ok=True)
    routes, da = gen_routes(rec, oa)
    models = gen_models(oa)
    if "Field(" in models:
        models = models.replace("from pydantic import BaseModel", "from pydantic import BaseModel, Field")
    files = {
        "__init__.py": "",
        "models.py": models,
        "auth.py": gen_auth(rec),
        "routes.py": routes,
        "data_access.py": da,
        "main.py": MAIN.format(title=f"{title} API"),
    }
    for name, text in files.items():
        (app / name).write_text(text, encoding="utf-8", newline="\n")
    (root / "requirements.txt").write_text("fastapi>=0.115\npydantic>=2.6\npython-jose[cryptography]>=3.3\nuvicorn>=0.30\n",
                                           encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.contract", description="OpenAPI 3.1 + FastAPI skeleton")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--title", default="Migrated")
    args = ap.parse_args(argv)
    if not (args.out_dir / "endpoints.json").exists():
        print("error: run `python -m c1.endpoints` first", file=sys.stderr)
        return 1
    r = run(args.out_dir, args.title)
    print(f"{r['operations']} operations, {r['schemas']} schemas, {r['flags']} flags -> {args.out_dir}/contract, backend/, flags.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

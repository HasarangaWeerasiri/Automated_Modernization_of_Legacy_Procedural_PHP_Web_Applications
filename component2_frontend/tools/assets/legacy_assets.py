"""Assets layer (opt-in deployment aid, not measured): a generated app loads the legacy page's own stylesheets and
script files, and serves the legacy app's static files.

The pipeline drops a plain page's `<head>` (flag `document_shell`) and every `<script>` (flag `inline_js_dropped`:
"detect and flag it, never convert it"); a shell keeps its `<head>` links in its layout (generation-spec section 8a).
This layer, added to a build project after Stage 5, puts back what the legacy page loaded, **as it was**: no
JavaScript is converted, rewritten or run by the tool; the browser loads the legacy files.

What it reads: the run's index.json (pages, groups, shells) and the legacy PHP source (the pinned clone under
legacy-apps/, through PHP's own tokenizer, tools/tok.php). Never a timeline, a stage output or Member 01's JSON.

Per generated page, in source order:
- the **scopes**: the page file and the files it includes by a literal path (recursively), top-level code only
  (function and class bodies are left out); for a page in a shell group, first the shell's header and last its
  footer (a whole file's top-level code, or a function's body);
- in their literal HTML (inline HTML and string literals), every `<link rel="stylesheet" ...>` and every
  `<script src="..." ...>` that is complete in one literal; one built by a PHP expression is **skipped and listed**,
  never guessed. HTML comments are left out. Inline `<script>` blocks are not loaded (still `inline_js_dropped`).
  A stylesheet a shell's layout already has is not added twice.

Into the build project (output/test-app/c2-build/<name>/) it writes:
- `public/`: the legacy app's static files (css, js, images, fonts, static .html pages) under their path from the document root, so the
  legacy relative URLs resolve as they did on the PHP server;
- `src/c2-legacy-assets.tsx` (the per-page lists and two server components), `src/proxy.ts` (tells them which page
  is requested), and in each group's `layout.tsx` the stylesheets at the end of `<head>` and the scripts at the
  end of `<body>` (scripts keep their order; ones the legacy page had in `<head>` now load at the end of `<body>`);
- `assets.json`: the table.
Page files and every Stage 1-5 output stay as they are. Never part of an evaluation run (eval-mapping section 4).

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/assets/legacy_assets.py <index.json> [--inline-js] [--out assets.json] [--build <name> [--no-build]]
(`--inline-js`: also the inline scripts and event handlers, run verbatim; tools/assets/inline_js.py.)
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # component2_frontend/
TOK_PHP = ROOT / "tools" / "tok.php"
TEST_APP = ROOT / "output" / "test-app"
BUILD_DIR = TEST_APP / "c2-build"
STATIC = {".css", ".js", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
          ".woff", ".woff2", ".ttf", ".eot", ".otf",
          ".html", ".htm"}  # a static page is served as it is, as the PHP server served it (HMS services.html)
LITERAL = {"T_INLINE_HTML", "T_CONSTANT_ENCAPSED_STRING", "T_ENCAPSED_AND_WHITESPACE", "T_START_HEREDOC"}
INCLUDE = {"T_INCLUDE", "T_INCLUDE_ONCE", "T_REQUIRE", "T_REQUIRE_ONCE"}
OPEN = {"{", "T_CURLY_OPEN", "T_DOLLAR_OPEN_CURLY_BRACES"}
TAG_START = re.compile(r"<(link|script)\b", re.I)
TAG = re.compile(r"<(link|script)\b([^<>]*)>", re.I)
ATTR = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'<>]+))?""")
COMMENT = re.compile(r"<!--.*?-->", re.S)
REACT_NAME = {"crossorigin": "crossOrigin", "charset": "charSet", "referrerpolicy": "referrerPolicy",
              "nomodule": "noModule", "class": "className", "hreflang": "hrefLang"}
KEEP = {"link": ("rel", "href", "type", "media", "integrity", "crossorigin", "id", "title", "referrerpolicy"),
        "script": ("src", "type", "integrity", "crossorigin", "async", "defer", "charset", "nomodule",
                   "referrerpolicy", "id")}


def find_php() -> str | None:
    for candidate in (os.environ.get("PHP_BIN"), shutil.which("php"), r"C:\xampp\php\php.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def lex(source: bytes, php: str) -> list[tuple[str, str, int]]:
    """PHP's own tokens: (name, text, line)."""
    out = subprocess.run([php, str(TOK_PHP)], input=source, capture_output=True, check=True).stdout
    return [(n, base64.b64decode(t).decode("utf-8", errors="replace"), line) for n, t, line in json.loads(out)]


def _body_end(tokens: list, start: int) -> int:
    """The index of the `}` closing the block opened at tokens[start]."""
    depth = 0
    for i in range(start, len(tokens)):
        if tokens[i][0] in OPEN or tokens[i][1] == "{" and tokens[i][0] == "CHAR":
            depth += 1
        elif tokens[i][0] == "CHAR" and tokens[i][1] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(tokens) - 1


def _bodies(tokens: list) -> list[tuple[str | None, int, int]]:
    """(function name or None, first, last token index) of every function / class body, outermost only."""
    found, i = [], 0
    while i < len(tokens):
        name, text, _ = tokens[i]
        if name in ("T_FUNCTION", "T_CLASS", "T_TRAIT", "T_INTERFACE"):
            label = next((t for n, t, _ in tokens[i + 1:i + 4] if n == "T_STRING"), None)
            j = i + 1
            while j < len(tokens) and not (tokens[j][0] == "CHAR" and tokens[j][1] in "{;"):
                j += 1
            if j < len(tokens) and tokens[j][1] == "{":
                end = _body_end(tokens, j)
                found.append((label if name == "T_FUNCTION" else None, j, end))
                i = end
        i += 1
    return found


def _string_value(name: str, text: str) -> str:
    if name == "T_CONSTANT_ENCAPSED_STRING" and len(text) >= 2 and text[0] in "'\"":
        body = text[1:-1]
        return body.replace("\\'", "'") if text[0] == "'" else body.replace('\\"', '"')
    return text


class Scanner:
    """The tags and literal includes of a legacy app's files, by scope."""

    def __init__(self, app_root: Path, php: str):
        self.app_root, self.php, self.cache = app_root, php, {}

    def tokens(self, path: str) -> list:
        if path not in self.cache:
            file = self.app_root / path
            self.cache[path] = lex(file.read_bytes(), self.php) if file.is_file() else None
        return self.cache[path]

    def scope(self, path: str, function: str | None) -> list[tuple[int, list]]:
        """The token ranges of a scope: a file's top-level code, or a function's body."""
        tokens = self.tokens(path)
        if tokens is None:
            return []
        bodies = _bodies(tokens)
        if function is not None:
            return [(s + 1, tokens[s + 1:e]) for name, s, e in bodies if name == function][:1]
        ranges, at = [], 0
        for _, s, e in bodies:
            ranges.append((at, tokens[at:s]))
            at = e + 1
        ranges.append((at, tokens[at:]))
        return ranges

    def items(self, path: str, function: str | None, seen: set) -> list[dict]:
        """In source order: tags, skipped tags, and (expanded) literal includes of a scope."""
        out = []
        for _, part in self.scope(path, function):
            k = 0
            while k < len(part):
                name, text, line = part[k]
                if name in INCLUDE:
                    target, k = self._include_target(part, k + 1, path)
                    if target is None:
                        out.append({"skip": "include_not_literal", "file": path, "line": line})
                    elif target not in seen and (self.app_root / target).is_file():
                        seen.add(target)
                        out.extend(self.items(target, None, seen))
                    continue
                if name in LITERAL:
                    out.extend(self._tags(_string_value(name, text), path, line))
                k += 1
        return out

    def _include_target(self, part: list, k: int, path: str) -> tuple[str | None, int]:
        """The file an include names by one literal path (parenthesised or not), relative to the including file's
        folder, else to the app root; None when the path is an expression."""
        literal, depth = None, 0
        start = k
        while k < len(part) and not (part[k][0] == "CHAR" and part[k][1] == ";"):
            name, text, _ = part[k]
            if name in ("T_WHITESPACE", "T_COMMENT"):
                pass
            elif name == "CHAR" and text in "()":
                depth += 1
            elif name == "T_CONSTANT_ENCAPSED_STRING" and literal is None:
                literal = _string_value(name, text)
            else:
                literal = "\0"  # anything else: an expression
            k += 1
        if literal in (None, "\0") or k == start:
            return None, k + 1
        folder = Path(path).parent
        for candidate in ((folder / literal).as_posix(), literal):
            normal = os.path.normpath(candidate).replace("\\", "/")
            if not normal.startswith("..") and (self.app_root / normal).is_file():
                return normal, k + 1
        return literal, k + 1  # a file that is not there: nothing to expand

    @staticmethod
    def _tags(text: str, path: str, line: int) -> list[dict]:
        found = []
        text = COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
        complete = {m.start(): m for m in TAG.finditer(text)}
        for start in TAG_START.finditer(text):
            at = line + text.count("\n", 0, start.start())
            match = complete.get(start.start())
            if match is None:
                if start.group(1).lower() == "script" and re.match(r"<script\b[^<>]*$", text[start.start():]) is None:
                    continue  # an inline block started elsewhere; nothing to load
                found.append({"skip": "tag_built_by_php", "file": path, "line": at})
                continue
            kind = match.group(1).lower()
            attrs = {}
            for a in ATTR.finditer(match.group(2)):
                value = a.group(2)
                attrs[a.group(1).lower()] = value[1:-1] if value and value[0] in "'\"" else (value or True)
            if kind == "link" and "stylesheet" not in str(attrs.get("rel", "")).lower().split():
                continue
            if kind == "script" and "src" not in attrs:
                continue
            url = attrs.get("href" if kind == "link" else "src")
            if not isinstance(url, str) or not url or "<?" in url:
                found.append({"skip": "tag_built_by_php", "file": path, "line": at})
                continue
            found.append({"kind": "style" if kind == "link" else "script", "file": path, "line": at,
                          "attrs": {k: v for k, v in attrs.items() if k in KEEP[kind]}})
        return found


def _layout_hrefs(build: Path | None, group: str) -> set[str]:
    """The stylesheet hrefs a group's generated layout already has."""
    if build is None or not (build / "src" / "app" / group / "layout.tsx").exists():
        return set()
    return set(re.findall(r'<link [^>]*href="([^"]+)"', (build / "src" / "app" / group / "layout.tsx")
                          .read_text(encoding="utf-8")))


def asset_table(index: dict, php: str, build: Path | None = None, inline: bool = False) -> dict:
    """Per generated page: its stylesheets, its script files and what was skipped (`inline`: also its inline
    scripts and event handlers, tools/assets/inline_js.py)."""
    pages = [p for p in index["pages"] if p["role"] == "page" and not p.get("crash") and not p.get("notGenerated")]
    if not pages:
        return {"app": index["app"], "docroot": index.get("docroot") or "", "pages": []}
    first = pages[0]
    app_root = ROOT / first["entrypoint"][: -len(first["path"])].rstrip("/")
    scanner = Scanner(app_root, php)
    prefix = first["entrypoint"][: -len(first["path"])]
    shells = {s["group"]: s for s in index["shells"]}

    def part(node: str) -> tuple[str, str | None]:
        """A shell's header / footer as a scope: (file, None) for a file, (file, function) for a function."""
        if node in (index.get("functions") or {}):
            return index["functions"][node]["path"], index["functions"][node]["name"]
        return node[len(prefix):], None

    rows = []
    for page in sorted(pages, key=lambda p: p["path"]):
        group = page.get("group") or "(plain)"
        scopes = [(page["path"], None)]
        if group in shells:
            scopes = [part(shells[group]["header"]), (page["path"], None), part(shells[group]["footer"])]
        items, seen = [], {path for path, _ in scopes}
        for path, function in scopes:
            items.extend(scanner.items(path, function, seen))
        have = _layout_hrefs(build, group)
        styles, scripts, skipped = [], [], []
        for item in items:
            if "skip" in item:
                skipped.append(item)
            elif item["kind"] == "style":
                if item["attrs"]["href"] not in have and item["attrs"] not in [s["attrs"] for s in styles]:
                    styles.append(item)
            elif item["attrs"] not in [s["attrs"] for s in scripts]:
                scripts.append(item)
        row = {"path": page["path"], "route": page["route"], "group": group,
               "styles": styles, "scripts": scripts, "skipped": skipped}
        if inline:
            import inline_js  # (it imports from this module)

            seen = {path for path, _ in scopes}
            row["inline"] = inline_js.collect([p for path, function in scopes
                                               for p in inline_js.pieces(scanner, path, function, seen)])
        rows.append(row)
    root = app_root.relative_to(ROOT) if app_root.is_relative_to(ROOT) else app_root
    return {"app": index["app"], "docroot": index.get("docroot") or "", "appRoot": root.as_posix(), "pages": rows}


# ---------------------------------------------------------------- the build project


def _react(attrs: dict) -> dict:
    return {REACT_NAME.get(k, k): v for k, v in attrs.items()}


def _url(path: str, docroot: str) -> str:
    return "/" + (path[len(docroot) + 1:] if docroot and path.startswith(docroot + "/") else path)


def data_module(table: dict) -> str:
    """src/c2-legacy-assets-data.ts: the lists per page, and the URLs (route and legacy) of each page."""
    pages, urls = {}, {}
    for row in table["pages"]:
        pages[row["path"]] = {"styles": [_react(s["attrs"]) for s in row["styles"]],
                              "scripts": [_react(s["attrs"]) for s in row["scripts"]]}
        if "inline" in row:  # the loader runs every script, in order, after hydration
            pages[row["path"]] |= {"scripts": [], "steps": [{k: s[k] for k in ("src", "code") if k in s}
                                                            for s in row["inline"]["steps"]],
                                   "handlers": [{k: h[k] for k in ("selector", "names", "event", "code")}
                                                for h in row["inline"]["handlers"]]}
        legacy =_url(row["path"], table["docroot"])
        folder = (legacy[: -len("index.php")].rstrip("/") or "/",) if legacy.endswith("/index.php") else ()
        for url in (row["route"], legacy) + folder:
            urls.setdefault(url, row["path"])
    return ("// Assets layer for " + table["app"] + " (tools/assets/legacy_assets.py): the legacy page's own\n"
            "// stylesheets and script files. A deployment aid, not part of the pipeline and not measured.\n"
            "export type Attributes = Record<string, string | boolean>;\n"
            + (INLINE_TYPES if any("inline" in row for row in table["pages"]) else
               "export type Assets = { styles: Attributes[]; scripts: Attributes[] };\n\n")
            + "export const PAGES: Record<string, Assets> = " + json.dumps(pages, indent=2, sort_keys=True) + ";\n\n"
            "export const PAGE_OF_URL: Record<string, string> = " + json.dumps(urls, indent=2, sort_keys=True)
            + ";\n\n"
            "export function pageOf(pathname: string): string {\n"
            '  const key = pathname.length > 1 ? pathname.replace(/\\/+$/, "") : pathname;\n'
            '  return PAGE_OF_URL[key] ?? "";\n'
            "}\n")


COMPONENTS = """// Assets layer (tools/assets/legacy_assets.py): loads what the legacy page loaded, as it was.
import { headers } from "next/headers";
import type { LinkHTMLAttributes, ScriptHTMLAttributes } from "react";
import { PAGES } from "./c2-legacy-assets-data";

async function current() {
  return PAGES[(await headers()).get("x-c2-legacy-page") ?? ""];
}

export async function LegacyStyles() {
  const page = await current();
  return page ? page.styles.map((a, i) => <link key={i} {...(a as LinkHTMLAttributes<HTMLLinkElement>)} />) : null;
}

export async function LegacyScripts() {
  const page = await current();
  return page
    ? page.scripts.map((a, i) => <script key={i} {...(a as ScriptHTMLAttributes<HTMLScriptElement>)} />)
    : null;
}
"""

INLINE_TYPES = ('import type { Handler, Step } from "./c2-legacy-script-loader";\n'
                "export type Assets = { styles: Attributes[]; scripts: Attributes[]; steps?: Step[]; "
                "handlers?: Handler[] };\n\n")

COMPONENTS_INLINE = COMPONENTS.replace(
    'import { PAGES } from "./c2-legacy-assets-data";\n',
    'import { PAGES } from "./c2-legacy-assets-data";\nimport { LegacyScriptLoader } from "./c2-legacy-script-loader";\n',
).replace(
    "export async function LegacyScripts() {\n  const page = await current();\n",
    "export async function LegacyScripts() {\n  const page = await current();\n"
    "  if (page?.steps) return <LegacyScriptLoader steps={page.steps} handlers={page.handlers ?? []} />;\n",
)

PROXY = """// Assets layer (tools/assets/legacy_assets.py): tells the layouts which legacy page is requested.
import { NextResponse, type NextRequest } from "next/server";
import { pageOf } from "./c2-legacy-assets-data";

export function proxy(request: NextRequest) {
  const headers = new Headers(request.headers);
  headers.set("x-c2-legacy-page", pageOf(request.nextUrl.pathname));
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: ["/((?!_next/).*)"] };
"""

IMPORT = 'import { LegacyScripts, LegacyStyles } from "../../c2-legacy-assets";\n'


def edit_layout(text: str) -> str:
    """A group layout with the stylesheets at the end of <head> and the scripts at the end of <body>."""
    if "LegacyStyles" in text:
        return text
    if text.count("{children}") != 1:
        raise ValueError("layout without exactly one {children}")
    if "</head>" in text:
        text = text.replace("</head>", "  <LegacyStyles />\n      </head>", 1)
    else:
        text = re.sub(r"(<html\b[^>]*>)", r"\1\n      <head>\n        <LegacyStyles />\n      </head>", text, count=1)
    text = text.replace("{children}", "{children}\n        <LegacyScripts />\n      ", 1)
    return IMPORT + text


def copy_static(app_root: Path, docroot: str, public: Path) -> int:
    """The legacy app's static files, under their path from the document root."""
    base = app_root / docroot if docroot else app_root
    count = 0
    for file in sorted(base.rglob("*")):
        relative = file.relative_to(base)
        if not file.is_file() or file.suffix.lower() not in STATIC or {".git", "node_modules"} & set(relative.parts):
            continue
        target = public / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        count += 1
    return count


def write(target: Path, table: dict) -> int:
    """Write the layer into a build project; the number of static files copied."""
    if not (target / "src" / "app").is_dir():
        raise FileNotFoundError(f"{target} is not a build project (no src/app/): build it first")
    for layout in sorted((target / "src" / "app").glob("*/layout.tsx")):
        layout.write_bytes(edit_layout(layout.read_text(encoding="utf-8")).encode("utf-8"))
    (target / "src" / "c2-legacy-assets-data.ts").write_bytes(data_module(table).encode("utf-8"))
    inline = any("inline" in row for row in table["pages"])
    (target / "src" / "c2-legacy-assets.tsx").write_bytes((COMPONENTS_INLINE if inline else COMPONENTS).encode("utf-8"))
    if inline:
        import inline_js

        (target / "src" / "c2-legacy-script-loader.tsx").write_bytes(inline_js.LOADER.encode("utf-8"))
    (target / "src" / "proxy.ts").write_bytes(PROXY.encode("utf-8"))
    (target / "assets.json").write_bytes((json.dumps(table, indent=2) + "\n").encode("utf-8"))
    return copy_static(ROOT / table["appRoot"], table["docroot"], target / "public") if table.get("appRoot") else 0


def _php() -> str:
    php = find_php()
    if php is None:
        raise FileNotFoundError("PHP not found (PHP_BIN, php on PATH, or C:/xampp/php/php.exe)")
    return php


def apply(name: str, index_path: Path, build: bool = True, inline: bool = False) -> tuple[dict, int, bool, str]:
    """Write the assets layer into output/test-app/c2-build/<name>/ and (by default) rebuild it."""
    sys.path.insert(0, str(ROOT / "tools" / "routing"))
    import routes  # the same in-place `next build`

    target = BUILD_DIR / name
    table = asset_table(json.loads(index_path.read_text(encoding="utf-8")), _php(), target, inline)
    copied = write(target, table)
    ok, text = routes.rebuild(target) if build else (True, "")
    return table, copied, ok, text


def summary(table: dict) -> str:
    rows = table["pages"]
    return (f"{table['app']}: {len(rows)} pages, {sum(len(r['styles']) for r in rows)} stylesheets, "
            f"{sum(len(r['scripts']) for r in rows)} script files, {sum(len(r['skipped']) for r in rows)} skipped"
            + ("" if not any("inline" in r for r in rows) else
               f"; inline JS: {sum(sum('code' in s for s in r['inline']['steps']) for r in rows)} script blocks, "
               f"{sum(len(r['inline']['handlers']) for r in rows)} handlers, "
               f"{sum(len(r['inline']['skipped']) for r in rows)} skipped, "
               f"{sum(len(r['inline']['notes']) for r in rows)} notes"))


def main(argv: list[str]) -> int:
    named = [a for i, a in enumerate(argv) if not a.startswith("--")
             and (i == 0 or argv[i - 1] not in ("--out", "--build"))]
    if len(named) != 1:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    index_path = Path(named[0])
    try:
        if "--build" in argv:
            table, copied, ok, text = apply(argv[argv.index("--build") + 1], index_path,
                                            build="--no-build" not in argv, inline="--inline-js" in argv)
        else:
            table = asset_table(json.loads(index_path.read_text(encoding="utf-8")), _php(),
                                inline="--inline-js" in argv)
            copied, ok, text = 0, True, ""
    except (FileNotFoundError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    if "--out" in argv:
        Path(argv[argv.index("--out") + 1]).write_bytes((json.dumps(table, indent=2) + "\n").encode("utf-8"))
    print(summary(table) + (f"; {copied} static files" if "--build" in argv else "")
          + (f"; next build {'ok' if ok else 'FAILED'}" if "--build" in argv and "--no-build" not in argv else ""))
    for row in table["pages"]:
        for s in row["skipped"]:
            print(f"  skipped: {row['path']}: {s['file']}:{s['line']} ({s['skip']})")
    if not ok:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

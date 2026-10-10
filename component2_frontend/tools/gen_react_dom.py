"""Extract what React's TypeScript types accept, for Stage 5 (docs/generation-spec.md section 5).

Stage 5 must emit JSX that compiles, so for every HTML element it needs to know which props
React's types declare, under which name, and which values they accept; and for every CSS
property which values `style` accepts. This script reads those declarations from the pinned
test app's node_modules (@types/react and csstype) and writes config/react_dom.json. The
generator never reads node_modules itself, so generation stays deterministic and runs
without Node.

Usage, from component2_frontend/ (after `npm ci` in output/test-app):
    venv/Scripts/python.exe tools/gen_react_dom.py

Value kinds per prop: "s" accepts any string, "n" a number, "b" a boolean; text after ":" lists
the string literals accepted ("|"-separated). "x" accepts none of these (e.g. a function).
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_MODULES = ROOT / "output" / "test-app" / "node_modules"
REACT_DTS = NODE_MODULES / "@types" / "react" / "index.d.ts"
CSSTYPE_DTS = NODE_MODULES / "csstype" / "index.d.ts"
OUT = ROOT / "config" / "react_dom.json"

# CSSProperties extends CSS.Properties<string | number>: TLength accepts any string.
CSS_STRINGISH = {"TLength", "string", "(string&{})"}
CSS_INTERFACES = ("StandardLonghandPropertiesHyphen", "StandardShorthandPropertiesHyphen",
                  "VendorLonghandPropertiesHyphen", "VendorShorthandPropertiesHyphen",
                  "ObsoletePropertiesHyphen", "SvgPropertiesHyphen")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def split_top(text: str, sep: str) -> list[str]:
    """Split on `sep` outside brackets and string/template literals."""
    parts, depth, start, quote = [], 0, 0, None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "<({[":
            depth += 1
        elif ch in ">)}]" and not (ch == ">" and i > 0 and text[i - 1] == "="):
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts]


def block(text: str, open_at: int) -> tuple[str, int]:
    """Body of the `{...}` starting at text[open_at] == '{', and the index after its '}'."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i], i + 1
    raise ValueError("unbalanced block")


def members(body: str) -> dict[str, str]:
    """`name?: type;` members of an interface body, by name (quotes removed)."""
    out = {}
    for statement in split_top(body, ";"):
        found = re.match(r'^("[^"]+"|[A-Za-z_$][\w$]*)\??\s*:\s*(.+)$', statement, re.S)
        if found:
            out[found.group(1).strip('"')] = " ".join(found.group(2).split())
    return out


def aliases(text: str, prefix: str = "") -> dict[str, str]:
    """`type Name<...> = type;` declarations (top level of `text`), by name."""
    out = {}
    # Generic parameters may have defaults (`<TLength = (string & {}) | 0>`), so they may contain "=".
    for found in re.finditer(r"(?:export\s+)?type\s+([A-Za-z_]\w*)(?:<[^;\n]*?>)?\s*=(?!>)", text):
        start = found.end()
        depth, i, quote = 0, start, None
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'`":
                quote = ch
            elif ch in "<({[":
                depth += 1
            elif ch in ">)}]" and text[i - 1] != "=":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            i += 1
        out[prefix + found.group(1)] = " ".join(text[start:i].split())
    return out


class Resolver:
    """Reduces a TypeScript type to the kinds of JSX attribute value it accepts."""

    def __init__(self, table: dict[str, str], stringish: set[str], namespaces: tuple[str, ...] = ()):
        self.table, self.stringish, self.namespaces = table, stringish, namespaces

    def kinds(self, type_text: str, seen=frozenset()) -> tuple[set[str], set[str]]:
        kinds, literals = set(), set()
        for alt in split_top(type_text.lstrip("| "), "|"):
            alt = alt.strip()
            compact = alt.replace(" ", "")
            if not alt or alt in ("undefined", "null", "never"):
                continue
            if compact in self.stringish or alt == "any":
                kinds.add("s")
            elif compact in ("number", "(number&{})") or re.fullmatch(r"-?\d+(\.\d+)?", alt):
                kinds.add("n")
            elif alt in ("boolean", "true", "false"):
                kinds.add("b")
            elif re.fullmatch(r'"[^"]*"', alt):
                literals.add(alt[1:-1])
            elif alt.startswith("(") and alt.endswith(")") and "=>" not in alt:
                inner_kinds, inner_literals = self.kinds(alt[1:-1], seen)
                kinds |= inner_kinds
                literals |= inner_literals
            else:
                name = re.sub(r"<.*$", "", alt).removeprefix("React.").removeprefix("CSS.")
                target = next((n for n in (name, *(f"{ns}.{name}" for ns in self.namespaces)) if n in self.table),
                              None)
                if target is None or target in seen:
                    kinds.add("x")
                    continue
                inner_kinds, inner_literals = self.kinds(self.table[target], seen | {target})
                kinds |= inner_kinds
                literals |= inner_literals
        return kinds, literals

    def code(self, type_text: str) -> str:
        kinds, literals = self.kinds(type_text)
        letters = "".join(k for k in "snb" if k in kinds)
        if "s" in kinds:
            return letters  # any string: the literals add nothing
        if not letters and not literals:
            return "x"
        return letters + (":" + "|".join(sorted(literals)) if literals else "")


def react_tables(text: str):
    text = strip_comments(text)
    interfaces, extends = {}, {}
    for found in re.finditer(r"interface\s+(\w+)(?:<[^>{]*>)?\s*(?:extends\s+([^{]+))?\{", text):
        body, _ = block(text, found.end() - 1)
        interfaces[found.group(1)] = members(body)
        extends[found.group(1)] = [re.sub(r"<.*$", "", e.strip()) for e in split_top(found.group(2) or "", ",") if e]
    elements = dict(re.findall(r"^\s+(\w+): React\.DetailedHTMLProps<React\.(\w+)<", text, re.M))
    return interfaces, extends, elements, aliases(text)


def css_table(text: str) -> dict[str, str]:
    text = strip_comments(text)
    table = aliases(text)
    for namespace in ("Property", "DataType"):
        found = re.search(rf"export namespace {namespace}\s*\{{", text)
        body, _ = block(text, found.end() - 1)
        table |= aliases(body, f"{namespace}.")
    resolver = Resolver(table, CSS_STRINGISH, ("Property", "DataType"))
    properties = {}
    for name in CSS_INTERFACES:
        found = re.search(rf"export interface {name}<[^\n]*>\s*\{{", text)  # its generics contain "{{}}"
        body, _ = block(text, found.end() - 1)
        for prop, type_text in members(body).items():
            type_text = re.sub(r"\|\s*undefined$", "", type_text).strip()
            properties[prop] = resolver.code(type_text)
    return dict(sorted(properties.items()))


def main():
    react_text = REACT_DTS.read_text(encoding="utf-8")
    interfaces, extends, elements, react_aliases = react_tables(react_text)
    resolver = Resolver(react_aliases, {"string", "(string&{})"})

    def chain(name):
        seen, order = set(), []
        stack = [name]
        while stack:
            current = stack.pop(0)
            if current in seen or current not in interfaces:
                continue
            seen.add(current)
            order.append(current)
            stack += extends.get(current, [])
        return order

    used = sorted({i for name in set(elements.values()) for i in chain(name)} - {"DOMAttributes"})
    props = {name: {prop: resolver.code(type_text) for prop, type_text in sorted(interfaces[name].items())
                    if not prop.startswith("on") or name == "AriaAttributes"}
             for name in used}
    doc = {
        "_provenance": {
            "generatedBy": "tools/gen_react_dom.py",
            "from": [f"@types/react {json.loads((REACT_DTS.parent / 'package.json').read_text())['version']}",
                     f"csstype {json.loads((CSSTYPE_DTS.parent / 'package.json').read_text())['version']}"],
            "kinds": "s = any string, n = number, b = boolean; after ':' the accepted string literals; "
                     "x = none of these",
            "note": "Event handler props (DOMAttributes, on*) are left out: Stage 5 drops inline JS.",
        },
        "elements": dict(sorted(elements.items())),
        "extends": {name: [e for e in extends.get(name, []) if e in props] for name in used},
        "props": props,
        "css": css_table(CSSTYPE_DTS.read_text(encoding="utf-8")),
    }
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"{OUT.relative_to(ROOT)}: {len(elements)} elements, {len(props)} interfaces, {len(doc['css'])} css")


if __name__ == "__main__":
    main()

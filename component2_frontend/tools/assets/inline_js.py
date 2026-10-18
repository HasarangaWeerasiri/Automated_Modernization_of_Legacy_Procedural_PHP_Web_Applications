"""Inline JS layer (opt-in, part of the assets layer; a deployment aid, not measured): the legacy page's inline
scripts and event-handler attributes, run **verbatim** by the browser. No JavaScript is converted or rewritten: the
rule "detect and flag it, never convert it" and the flag `inline_js_dropped` stand.

Collected per page, from the same scopes as the assets layer (tools/assets/legacy_assets.py), in source order:
- **steps**: every `<script src>` (as the assets layer) and every inline `<script>` block, complete in one literal;
- **handlers**: every `on*="..."` attribute of a complete start tag, with a CSS selector of all the tag's static
  attributes (not `style`) and their names; the loader sets it only on elements with exactly those attributes (so a
  sidebar link that shares the card's `href` but has more attributes is not touched).
Skipped and listed, never guessed (`skipped`):
- `conditional`: inline code or a handler under an `if` / `else` / `switch` (it runs only on some requests, e.g. an
  `alert()` after a POST); a loop is fine (the same handler on every row);
- `script_built_by_php` / `handler_built_by_php`: a block or a tag that PHP builds (`<?= ?>`, concatenation);
- `document_write`: a block that calls `document.write` (it writes where the script stood; run later it would
  replace the page);
- `ambiguous`: two legacy elements with the same selector but different (or no) code for the event;
  `no_selector`: a tag with no static attribute to find it by.
Listed but run (`notes`): `dom_sink` (innerHTML, outerHTML, insertAdjacentHTML, eval, new Function: the legacy
code's own risk, kept as it was); `load_event` (code that waits for `load` / `DOMContentLoaded`: the loader calls it
once if the event has already passed).

The browser side (`src/c2-legacy-script-loader.tsx`, a client component): after hydration, it sets the handler
attributes, then loads the steps one after the other (a file waits for its `load`), so legacy code never changes the
DOM before React has hydrated it; a `load` / `DOMContentLoaded` listener added after that event is called once.
"""

import re

from legacy_assets import ATTR, COMMENT, INCLUDE, LITERAL, KEEP, _string_value

IF_KIND = {"T_IF", "T_ELSEIF", "T_ELSE", "T_SWITCH"}
LOOP_KIND = {"T_WHILE", "T_FOR", "T_FOREACH", "T_DO"}
ALT_END = {"T_ENDIF", "T_ENDWHILE", "T_ENDFOR", "T_ENDFOREACH", "T_ENDSWITCH"}
START_TAG = re.compile(r"<([a-zA-Z][\w-]*)\b([^<>]*)>")
OPEN_TAG = re.compile(r"<([a-zA-Z][\w-]*)\b[^<>]*$")
SCRIPT = re.compile(r"<script\b([^<>]*)>(.*?)</script\s*>", re.I | re.S)
HANDLER = re.compile(r"^on[a-z]+$")
JS_TYPES = {"", "text/javascript", "application/javascript", "javascript", "text/ecmascript"}
SINK = re.compile(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|\beval\s*\(|new\s+Function\s*\(")
LOAD = re.compile(r"window\.onload\s*=|addEventListener\s*\(\s*['\"](load|DOMContentLoaded)['\"]|"
                  r"\$\(\s*window\s*\)\.(on\(\s*['\"]load|load\s*\()")


def _skip_parens(tokens: list, i: int) -> int:
    """The index after a parenthesised condition starting at or after i (or i when there is none)."""
    j = i
    while j < len(tokens) and tokens[j][0] in ("T_WHITESPACE", "T_COMMENT"):
        j += 1
    if j >= len(tokens) or tokens[j][1] != "(":
        return i
    depth = 0
    for k in range(j, len(tokens)):
        if tokens[k][0] == "CHAR" and tokens[k][1] == "(":
            depth += 1
        elif tokens[k][0] == "CHAR" and tokens[k][1] == ")":
            depth -= 1
            if depth == 0:
                return k + 1
    return len(tokens)


def pieces(scanner, path: str, function: str | None, seen: set, outer_if: bool = False) -> list[dict]:
    """The literal HTML of a scope in source order, each piece with whether it is under an if / else / switch;
    literal includes are expanded where they stand (under the include's condition)."""
    out = []
    for _, part in scanner.scope(path, function):
        stack = []  # stack entries: (kind, how) with how "brace" | "alt" | "stmt"
        k = 0
        while k < len(part):
            name, text, line = part[k]
            under_if = outer_if or any(kind == "if" for kind, _ in stack)
            if name in IF_KIND | LOOP_KIND:
                kind = "if" if name in IF_KIND else "loop"
                k = _skip_parens(part, k + 1)
                j = k
                while j < len(part) and part[j][0] in ("T_WHITESPACE", "T_COMMENT"):
                    j += 1
                nxt = part[j][1] if j < len(part) else ""
                if nxt == ":":
                    if not (name in ("T_ELSEIF", "T_ELSE") and stack and stack[-1][1] == "alt"):
                        stack.append((kind, "alt"))
                    k = j + 1
                elif nxt == "{":
                    stack.append((kind, "brace"))
                    k = j + 1
                elif name == "T_ELSE" and j < len(part) and part[j][0] == "T_IF":
                    k = j  # `else if`: the if pushes
                else:
                    stack.append((kind, "stmt"))
                continue
            if name in ALT_END:
                while stack and stack[-1][1] != "alt":
                    stack.pop()
                if stack:
                    stack.pop()
            elif name == "CHAR" and text == "{" or name in ("T_CURLY_OPEN", "T_DOLLAR_OPEN_CURLY_BRACES"):
                stack.append(("block", "brace"))
            elif name == "CHAR" and text == "}":
                while stack and stack[-1][1] == "stmt":
                    stack.pop()
                if stack:
                    stack.pop()
                while stack and stack[-1][1] == "stmt":  # `if (x) { } ` ends a statement too
                    stack.pop()
            elif name == "CHAR" and text == ";":
                while stack and stack[-1][1] == "stmt":
                    stack.pop()
            elif name in INCLUDE:
                target, k = scanner._include_target(part, k + 1, path)
                if target is None:
                    out.append({"skip": "include_not_literal", "file": path, "line": line})
                elif target not in seen and (scanner.app_root / target).is_file():
                    seen.add(target)
                    out.extend(pieces(scanner, target, None, seen, under_if))
                continue
            elif name in LITERAL:
                out.append({"text": _string_value(name, text), "file": path, "line": line,
                            "conditional": under_if, "inline": name == "T_INLINE_HTML"})
            k += 1
    return out


def _attrs(source: str) -> dict:
    found = {}
    for a in ATTR.finditer(source):
        value = a.group(2)
        found[a.group(1).lower()] = value[1:-1] if value and value[0] in "'\"" else (value or True)
    return found


def _css(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\a ")


def static(attrs: dict) -> dict:
    """A tag's attributes without its handlers and its style (React rewrites a style attribute)."""
    return {k: v for k, v in attrs.items() if not HANDLER.match(k) and k != "style"}


def selector(tag: str, attrs: dict) -> str | None:
    """A CSS selector of every static attribute; the loader also checks the element has no other attribute."""
    parts = [f'[{k}="{_css(v)}"]' if isinstance(v, str) else f"[{k}]" for k, v in sorted(static(attrs).items())]
    return tag.lower() + "".join(parts) if parts else None


def collect(items: list[dict]) -> dict:
    """Steps, handlers, skipped and notes of a page from its pieces."""
    steps, elements, skipped, notes = [], [], [], []
    open_tag = None  # a start tag a piece left open (PHP builds the rest)
    for item in items:
        if "skip" in item:
            skipped.append(item)
            continue
        text, at = COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), item["text"]), item
        line = lambda pos: at["line"] + text.count("\n", 0, pos)  # noqa: E731
        if open_tag is not None:
            rest = text.split(">", 1)[0]
            if re.search(r"\son[a-z]+\s*=", " " + rest, re.I):
                skipped.append({"skip": "handler_built_by_php", "file": item["file"], "line": item["line"]})
            open_tag = None
        scripts = list(SCRIPT.finditer(text))
        inside = [(m.start(), m.end()) for m in scripts]
        for m in scripts:
            attrs = _attrs(m.group(1))
            if "src" in attrs:
                if isinstance(attrs["src"], str) and attrs["src"] and "<?" not in attrs["src"]:
                    step = {"src": {k: v for k, v in attrs.items() if k in KEEP["script"]}}
                    if step not in steps:
                        steps.append(step)
                continue
            if str(attrs.get("type", "")).lower() not in JS_TYPES:
                continue
            where = {"file": item["file"], "line": line(m.start())}
            code = m.group(2)
            if item["conditional"]:
                skipped.append({"skip": "conditional", **where})
            elif "document.write" in code:
                skipped.append({"skip": "document_write", **where})
            else:
                steps.append({"code": code, **where})
                notes.extend({"note": n, **where} for n in _notes(code))
        unclosed = re.search(r"<script\b(?![^<>]*\bsrc\s*=)[^<>]*>(?![\s\S]*</script)", text, re.I)
        if unclosed and not any(s <= unclosed.start() < e for s, e in inside):
            skipped.append({"skip": "script_built_by_php", "file": item["file"], "line": line(unclosed.start())})
        for m in START_TAG.finditer(text):
            if any(s <= m.start() < e for s, e in inside) or m.group(1).lower() == "script":
                continue
            attrs = _attrs(m.group(2))
            dynamic = "<?" in m.group(2)
            elements.append({"tag": m.group(1).lower(), "attrs": attrs, "file": item["file"],
                             "line": line(m.start()), "conditional": item["conditional"], "dynamic": dynamic})
        tail = OPEN_TAG.search(text)
        if tail is not None:
            if re.search(r"\son[a-z]+\s*=", text[tail.start():], re.I):
                skipped.append({"skip": "handler_built_by_php", "file": item["file"], "line": line(tail.start())})
            else:
                open_tag = tail.group(1)
    handlers = _handlers(elements, skipped, notes)
    return {"steps": steps, "handlers": handlers, "skipped": skipped, "notes": notes}


def _notes(code: str) -> list[str]:
    return (["dom_sink"] if SINK.search(code) else []) + (["load_event"] if LOAD.search(code) else [])


def _handlers(elements: list[dict], skipped: list, notes: list) -> list[dict]:
    groups = {}
    for e in elements:
        key = "body" if e["tag"] in ("body", "html") else selector(e["tag"], e["attrs"])
        e["selector"] = key
        if key is not None:
            groups.setdefault(key, []).append(e)
    handlers = []
    for e in elements:
        for event, code in sorted((k, v) for k, v in e["attrs"].items() if HANDLER.match(k)):
            where = {"file": e["file"], "line": e["line"], "event": event}
            if not isinstance(code, str):
                continue
            if e["dynamic"] or "<?" in code:
                skipped.append({"skip": "handler_built_by_php", **where})
            elif e["conditional"]:
                skipped.append({"skip": "conditional", **where})
            elif e["selector"] is None:
                skipped.append({"skip": "no_selector", **where})
            elif e["selector"] != "body" and any(o["attrs"].get(event) != code or o["conditional"]
                                                 for o in groups[e["selector"]]):
                skipped.append({"skip": "ambiguous", **where})
            else:
                handler = {"selector": e["selector"], "names": " ".join(sorted(static(e["attrs"]))),
                           "event": event, "code": code}
                if handler not in [{k: h[k] for k in ("selector", "names", "event", "code")} for h in handlers]:
                    handlers.append(handler | {"file": e["file"], "line": e["line"]})
                    notes.extend({"note": n, **where} for n in _notes(code))
    return handlers


LOADER = """"use client";
// Inline JS layer (tools/assets/inline_js.py): the legacy page's inline scripts and event-handler attributes, run
// verbatim by the browser after hydration. Nothing is converted. A deployment aid, not measured.
import { useEffect } from "react";

export type Step = { src?: Record<string, string | boolean>; code?: string };
export type Handler = { selector: string; names: string; event: string; code: string };

let ran = false; // once per page load

function passed(type: string): boolean {
  if (type === "load") return document.readyState === "complete";
  if (type === "DOMContentLoaded") return document.readyState !== "loading";
  return false;
}

function callOnce(target: EventTarget, type: string, listener: EventListenerOrEventListenerObject | null) {
  if (!listener) return;
  window.setTimeout(() => {
    const event = new Event(type);
    if (typeof listener === "function") listener.call(target, event);
    else listener.handleEvent(event);
  });
}

// A load / DOMContentLoaded listener the legacy code adds after that event has passed is called once.
function patchLoadEvents(): () => void {
  const targets: EventTarget[] = [window, document];
  const originals = targets.map((t) => t.addEventListener);
  targets.forEach((target, i) => {
    target.addEventListener = function (type: string, listener: EventListenerOrEventListenerObject | null, options?: boolean | AddEventListenerOptions) {
      if (passed(type)) return callOnce(target, type, listener);
      return originals[i].call(target, type, listener, options);
    } as typeof target.addEventListener;
  });
  return () => targets.forEach((target, i) => { target.addEventListener = originals[i]; });
}

function attributeNames(element: Element): string {
  return element.getAttributeNames().filter((n) => !/^on[a-z]+$/.test(n) && n !== "style").sort().join(" ");
}

function run(step: Step): Promise<void> {
  return new Promise((resolve) => {
    const script = document.createElement("script");
    if (step.src) {
      for (const [name, value] of Object.entries(step.src)) script.setAttribute(name, value === true ? "" : String(value));
      script.async = false;
      script.onload = script.onerror = () => resolve();
      document.body.appendChild(script);
    } else {
      script.text = step.code ?? "";
      document.body.appendChild(script); // an inline script runs when inserted
      resolve();
    }
  });
}

export function LegacyScriptLoader({ steps, handlers }: { steps: Step[]; handlers: Handler[] }) {
  useEffect(() => {
    if (ran) return;
    ran = true;
    const onloadBefore = window.onload;
    for (const h of handlers) {
      const found = h.selector === "body" ? [document.body] : Array.from(document.querySelectorAll(h.selector))
        .filter((element) => attributeNames(element) === h.names); // exactly the legacy tag's attributes
      for (const element of found) element.setAttribute(h.event, h.code);
    }
    const restore = patchLoadEvents();
    (async () => {
      try {
        for (const step of steps) await run(step);
      } finally {
        restore();
      }
      if (window.onload && window.onload !== onloadBefore && passed("load")) callOnce(window, "load", window.onload);
      else if (window.onload && passed("load") && handlers.some((h) => h.selector === "body" && h.event === "onload"))
        callOnce(window, "load", window.onload);
    })();
  }, [steps, handlers]);
  return null;
}
"""

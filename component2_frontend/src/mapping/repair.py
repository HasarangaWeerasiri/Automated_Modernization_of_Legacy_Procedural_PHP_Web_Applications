# What this file does: fixes invalid HTML the same way a browser does.
"""Browser-equivalent markup (docs/generation-spec.md v0.6 section 5b).

Legacy pages were rendered by browsers, which repair some invalid HTML by fixed HTML5 parsing
rules. React renders the tree as written. So two repairs that the HTML5 tree-construction rules
fully determine are applied to the markup tree before it is generated:

  implicit <tbody>   `<tr>` directly inside `<table>`: each run of rows is wrapped in a `<tbody>`
                     (what "in table" insertion mode builds). A loop whose body is only `<tr>`
                     elements lies inside the run, and (spec v0.7) is a run by itself too: the
                     `.map()` goes inside the `<tbody>`.
  implicit </p>      an open `<p>` is closed before a block start tag ("close a p element"). The
                     block element and what follows it move out, after the `<p>`; the `</p>` that
                     was written for it then has no `<p>` to close, so it makes an empty `<p>`,
                     exactly as a browser does.

Each repair stays inside one component's static markup. A `<p>` is repaired only when every direct
child is text or an element (no loop, if, call or expression at that level), it is not the root of
a loop body, and nothing was left open inside it. A table is repaired only when it holds nothing
that an HTML5 parser would move out of it (foster parenting): text, expressions, ifs, or elements
that are not table content. Whatever is not repaired is still reported by the nesting check
(`html_nesting_changed`).
"""

from src.mapping.markup import HTML_SPACE, MarkupFlag, MBranch, MElement, MIf, MLoop, MText

# HTML5 "in body": a start tag with one of these names closes an open <p> (section 5b's list).
P_CLOSERS = frozenset((
    "address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre",
    "section", "table", "ul"))
# What may be a child of <table> without being moved by the parser.
TABLE_CONTENT = frozenset(("caption", "colgroup", "col", "thead", "tbody", "tfoot", "template", "script", "style"))


def _blank(node) -> bool:
    """True for a text node that is only HTML whitespace."""
    return isinstance(node, MText) and node.text.strip(HTML_SPACE) == ""


def _row_loop(node) -> bool:
    """A loop whose body is only `<tr>` elements (and whitespace)."""
    return isinstance(node, MLoop) and any(isinstance(c, MElement) for c in node.children) and all(
        _blank(c) or (isinstance(c, MElement) and c.tag == "tr") for c in node.children)


def _split_paragraph(p: MElement, owner, flags: list) -> list | None:
    """What a browser builds from a <p> that holds a block element, or None when no repair applies."""
    if p.tag != "p" or not p.clean or isinstance(owner, MLoop):
        return None
    if not all(isinstance(c, (MText, MElement)) for c in p.children):
        return None  # a loop, if, call or expression at this level: the result would depend on what runs
    index = next((i for i, c in enumerate(p.children) if isinstance(c, MElement) and c.tag in P_CLOSERS), None)
    if index is None:
        return None
    moved, p.children = p.children[index:], p.children[:index]
    out = [p, *moved]
    if p.end == "explicit":  # its </p> finds no open <p>: an HTML5 parser inserts an empty one there
        out.append(MElement("p", [], p.node_id, end="explicit"))
    flags.append(MarkupFlag("implicit_p_close", "info", p.node_id,
                            f"<p> closed before <{moved[0].tag}>, as a browser does"
                            + ("; its </p> then makes an empty <p>" if p.end == "explicit" else ""), p))
    return out


def _wrap_rows(table: MElement, flags: list) -> None:
    """Wrap rows placed directly in a <table> in a <tbody>, as a browser does (flag implicit_tbody); tables with other
    content are left alone."""
    children = table.children
    if not all(_blank(c) or _row_loop(c) or (isinstance(c, MElement) and (c.tag == "tr" or c.tag in TABLE_CONTENT))
               for c in children):
        return  # something a parser would move out of the table: not repaired, the nesting check reports it
    out, run = [], []

    def close() -> None:
        """Put the rows collected so far in one <tbody>, leaving trailing whitespace outside it."""
        while run and _blank(run[-1]):  # trailing whitespace stays outside the tbody
            tail.insert(0, run.pop())
        if any(not _blank(c) for c in run):  # static rows, a loop of rows (spec v0.7), or both
            first = next(c for c in run if not _blank(c))
            if isinstance(first, MLoop):
                first = next(c for c in first.children if isinstance(c, MElement))
            body = MElement("tbody", [], first.node_id, parent=table, end="explicit")
            body.children = list(run)
            for child in run:
                child.parent = body
            out.append(body)
            flags.append(MarkupFlag("implicit_tbody", "info", body.node_id,
                                    "<tr> directly in <table>: wrapped in <tbody>, as a browser does", table))
        else:
            out.extend(run)
        out.extend(tail)
        run.clear()
        tail.clear()

    tail: list = []
    for child in children:
        row = _row_loop(child) or (isinstance(child, MElement) and child.tag == "tr")
        if row or (run and _blank(child)):
            run.append(child)
        else:
            close()
            out.append(child)
    close()
    table.children = out


def _repair(children: list, owner, flags: list) -> list:
    """Apply the browser-equivalent repairs to a list of children, recursively: split paragraphs, wrap table rows."""
    out = []
    for child in children:
        split = _split_paragraph(child, owner, flags) if isinstance(child, MElement) else None
        for node in split or [child]:
            node.parent = owner
            out.append(node)
    for node in out:
        if isinstance(node, MElement):
            if node.content is None:
                node.children = _repair(node.children, node, flags)
            if node.tag == "table":
                _wrap_rows(node, flags)
        elif isinstance(node, MLoop):
            node.children = _repair(node.children, node, flags)
        elif isinstance(node, MIf):
            for branch in node.branches:
                branch.children = _repair(branch.children, branch, flags)
    return out


def repair(markup) -> None:
    """Apply section 5b's repairs to a markup tree, in place; each one adds an info flag."""
    markup.children = _repair(markup.children, None, markup.flags)


__all__ = ["repair", "P_CLOSERS", "TABLE_CONTENT", "MBranch"]

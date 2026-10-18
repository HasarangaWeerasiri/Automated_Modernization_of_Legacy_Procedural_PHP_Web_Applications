"""Stage 2 — extract data values from legacy HTML.

Only the values a page displays are kept. Markup is discarded and raw HTML is never
stored (FR7). What a value is called comes from the page itself: a table's header
text labels its columns. Nothing here knows any particular application.

This version reads data held in HTML tables, and the messages a page shows in a
pop-up. Other structures are added when the engine is generalised. A page it cannot
read yields no tables, which a caller must treat as "could not extract", never as
"the page shows no data".

Pages also use tables only to line up a form, a label in one cell and a field in the
next. Such a table is marked `form_layout` and gives no records. It is a table
without a header row where at least half of the rows hold a field for typing (a
text, password, file or similar input, or a text area). Choice lists, checkboxes,
radio buttons, buttons and hidden fields do not count: a table of data often has one
of them per row. A header row, all of its cells headers, makes a table one of data.
A table of data with no header row and a typing field in most rows is misread as a
form layout; the caller then finds no data to read and abstains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from bs4.element import Tag


@dataclass(frozen=True)
class ExtractedTable:
    labels: tuple[str, ...]
    records: tuple[dict[str, str], ...]
    # The table only lines up a form; it holds no records.
    form_layout: bool = False


# Input types a user types a value into. An input without a type is a text field.
_TYPING_INPUTS = frozenset({
    "", "text", "password", "email", "number", "tel", "url", "search",
    "date", "datetime-local", "month", "week", "time", "file",
})


# alert('...') or alert("...") with a literal text, as legacy pages write it to tell
# the user what happened.
_ALERT = re.compile(r"""\balert\s*\(\s*(['"])((?:\\.|(?!\1).)*)\1\s*\)""", re.DOTALL)
_ESCAPED = re.compile(r"\\(.)", re.DOTALL)
_ESCAPES = {"n": "\n", "t": "\t"}


def _text(cell: Tag) -> str:
    # A value is what a reader sees, as data. Code and styling inside a cell are never
    # shown, and the label of a button or a list of choices is a control for the user,
    # not data. Line breaks and runs of whitespace, including non-breaking spaces, are
    # layout and collapse to single spaces.
    for unseen in cell.find_all(["script", "style", "button", "select", "textarea"]):
        unseen.extract()
    for line_break in cell.find_all("br"):
        line_break.replace_with("\n")
    return " ".join(cell.get_text().split())


def _own_rows(table: Tag) -> list[Tag]:
    # A table nested inside a cell is a table of its own, not more rows of this one.
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _own_cells(row: Tag) -> list[Tag]:
    return [cell for cell in row.find_all(["th", "td"]) if cell.find_parent("tr") is row]


def _is_header(row: Tag) -> bool:
    return row.find_parent("thead") is not None or all(cell.name == "th" for cell in _own_cells(row))


def _has_typing_field(row: Tag, table: Tag) -> bool:
    for field in row.find_all(["input", "textarea"]):
        if field.find_parent("table") is not table:
            continue  # it belongs to a table nested in this row
        if field.name == "textarea" or field.get("type", "").strip().lower() in _TYPING_INPUTS:
            return True
    return False


def _labels(header: list[str], width: int) -> tuple[str, ...]:
    """One distinct label per column, from the header where it gives one."""
    labels: list[str] = []
    for index in range(width):
        label = header[index] if index < len(header) and header[index] else f"column {index + 1}"
        duplicate, count = label, 1
        while duplicate in labels:
            count += 1
            duplicate = f"{label} ({count})"
        labels.append(duplicate)
    return tuple(labels)


def extract_tables(html: str | bytes) -> list[ExtractedTable]:
    """Return every table of a page, in document order, as labelled records.

    Pass the response body as bytes where possible, so the page's own declared
    encoding is used.
    """
    # html5lib builds the same tree a browser builds, so a value here is the value
    # a reader of the page sees. Legacy pages routinely leave cells and rows
    # unclosed; Python's built-in parser nests those wrongly and merges their text.
    soup = BeautifulSoup(html, "html5lib")
    tables = []
    for table in soup.find_all("table"):
        header: list[str] = []
        rows: list[list[str]] = []
        filled = [row for row in _own_rows(table) if _own_cells(row)]
        # Read before the cells' text is taken, which removes the fields.
        typing = sum(_has_typing_field(row, table) for row in filled)
        if not any(map(_is_header, filled)) and typing and typing * 2 >= len(filled):
            tables.append(ExtractedTable(labels=(), records=(), form_layout=True))
            continue
        for row in filled:
            cells = _own_cells(row)
            if _is_header(row) and not header and not rows:
                header = [_text(cell) for cell in cells]
            else:
                rows.append([_text(cell) for cell in cells])

        labels = _labels(header, max([len(header), *map(len, rows)]))
        tables.append(ExtractedTable(
            labels=labels,
            records=tuple(dict(zip(labels, row)) for row in rows),
        ))
    return tables


def extract_messages(html: str | bytes) -> list[str]:
    """Return the pop-up messages a page shows, in document order.

    Legacy pages commonly answer a form by printing a script that calls alert()
    with a fixed text. That text is what the user is told, so it is kept. Only
    literal texts are read; a message built at run time in the browser is not.
    """
    soup = BeautifulSoup(html, "html5lib")
    messages = []
    for script in soup.find_all("script"):
        # get_text() leaves script content out, so the strings are read directly.
        code = "".join(str(piece) for piece in script.find_all(string=True))
        for match in _ALERT.finditer(code):
            text = _ESCAPED.sub(
                lambda escape: _ESCAPES.get(escape.group(1), escape.group(1)), match.group(2)
            )
            messages.append(" ".join(text.split()))
    return messages

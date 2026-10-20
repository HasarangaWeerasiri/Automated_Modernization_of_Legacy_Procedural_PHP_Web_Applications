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

Data a page shows outside tables is read by `extract_records`, with the database as
it is after the request. A record there is one database row the page shows: an
element that shows at least two values of the row, in two separate places (one value
that several columns of the row hold counts once), at least one of them distinctive
(text of three characters or more that only this row holds in its column), and no
smaller element that does. Values are looked for in the page's text and in its link
and image addresses, each as a whole piece (bounded by the start, the end or a
character that is not a letter or digit): "adam" in "- by adam", "10" in
"view.php?picid=10". A record keeps only those values; the words around them are the
page's own and are not data. Records are grouped by the place they sit in on the
page, the path of tags and classes that leads to them. Rows of one table that share
the smallest element showing them, as entries written as loose paragraphs do, are
records of their own. A row of another table shown inside a record, and alone there,
is part of that record, as a picture and its owner in one list item are; values are
collected from the largest element around the record that holds no other record.
What a value is called comes from the page: the element it sits in ("p.comment"), or
"link" and "image" for an address.

Limits, all of which lose coverage and none of which makes a wrong record: a value
the page computes or reformats is not found; a value split across several pieces of
text is not found; a row that shows only one value, or none that is distinctive, is
not a record.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from itertools import chain
from typing import Any, Iterable, Mapping
from urllib.parse import unquote

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag


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


# ------------------------------------------------------------ records outside tables


@dataclass(frozen=True)
class ExtractedRecords:
    """The records a page shows outside tables, all from one place on the page."""

    place: str
    records: tuple[dict[str, str], ...]


# Never shown to a reader, or a control for the user rather than data.
_UNSEEN = ("head", "script", "style", "template", "noscript", "input", "select", "textarea", "button", "option")
# Addresses whose values a reader acts on: the element, its attribute, the label.
_ADDRESSES = (("a", "href", "link"), ("img", "src", "image"))


def _name(element: Tag) -> str:
    classes = element.get("class") or []
    return f"{element.name}.{classes[0]}" if classes else element.name


def _spans(form: str, text: str) -> list[tuple[int, int]]:
    """Where `form` appears in `text` as a whole piece."""
    spans = []
    start = text.find(form)
    while start != -1:
        end = start + len(form)
        if (start == 0 or not text[start - 1].isalnum()) and (end == len(text) or not text[end].isalnum()):
            spans.append((start, end))
        start = text.find(form, start + 1)
    return spans


def extract_records(
    html: str | bytes, tables: Mapping[str, Iterable[Mapping[str, Any]]]
) -> list[ExtractedRecords]:
    """Return the records a page shows outside tables, one entry per place, in page order.

    `tables` holds the rows of every database table after the request.
    """
    # Here, not at the top: compare imports the modules that import this one.
    from c4.compare import _NUMBER, normalise

    # Every form of every stored value, and the rows that hold it.
    held: dict[str, list[tuple[str, int, str]]] = {}
    for table, rows in tables.items():
        for number, row in enumerate(rows):
            for column, value in row.items():
                if value is None or value == "":
                    continue
                for form in {" ".join(str(value).split()), normalise(value)} - {""}:
                    held.setdefault(form, []).append((table, number, column))
    holders = Counter((form, table, column) for form, places in held.items() for table, _, column in places)

    def distinctive(form: str, table: str, column: str) -> bool:
        return not _NUMBER.fullmatch(form) and len(form) >= 3 and holders[(form, table, column)] == 1

    soup = BeautifulSoup(html, "html5lib")
    body = soup.body or soup
    for unseen in body.find_all(_UNSEEN):
        unseen.extract()

    # The pieces a reader sees or follows, in page order: (element, label, text).
    pieces: list[tuple[Tag, str, str]] = []
    for node in body.descendants:
        if type(node) is NavigableString:
            text = " ".join(node.split())
            if text:
                pieces.append((node.parent, _name(node.parent), text))
        elif isinstance(node, Tag):
            for tag, attribute, label in _ADDRESSES:
                if node.name == tag and node.get(attribute):
                    pieces.append((node, label, " ".join(unquote(node[attribute]).split())))

    # Where each row's values appear: row -> [(piece, column, form, distinctive)].
    shown: dict[tuple[str, int], list[tuple[int, str, str, bool]]] = {}
    for index, (_, _, text) in enumerate(pieces):
        here: dict[tuple[str, int], list[tuple[str, str, list[tuple[int, int]]]]] = {}
        for form, places in held.items():
            if form in text and (spans := _spans(form, text)):
                for table, number, column in places:
                    here.setdefault((table, number), []).append((column, form, spans))
        for row, hits in here.items():
            for column, form, spans in hits:
                # A value seen only inside a longer value of the same row is part of
                # that value, not one more shown: "User" in the login "Sample User".
                outer = [span for _, other, other_spans in hits if len(other) > len(form) for span in other_spans]
                if outer and all(any(o_start <= start and end <= o_end for o_start, o_end in outer) for start, end in spans):
                    continue
                shown.setdefault(row, []).append((index, column, form, distinctive(form, row[0], column)))

    def inside(element: Tag, home: Tag) -> bool:
        return element is home or any(parent is home for parent in element.parents)

    # The smallest elements that show at least two values of a row, one distinctive.
    homes: dict[int, tuple[Tag, list[tuple[str, int]]]] = {}
    for row, hits in shown.items():
        if len({column for _, column, _, _ in hits}) < 2 or not any(flag for *_, flag in hits):
            continue
        # Per element: the columns shown, the places they are shown in (a piece of the
        # page and the text found there), and whether one of them is distinctive. Two
        # values must be two places: one value several columns hold is still one value.
        seen: dict[int, tuple[Tag, set[str], set[tuple[int, str]], list[bool]]] = {}
        for index, column, form, flag in hits:
            element = pieces[index][0]
            for ancestor in chain([element], element.parents):
                entry = seen.setdefault(id(ancestor), (ancestor, set(), set(), [False]))
                entry[1].add(column)
                entry[2].add((index, form))
                entry[3][0] = entry[3][0] or flag
                if ancestor is body:
                    break
        qualifying = {
            key: entry[0] for key, entry in seen.items() if len(entry[1]) >= 2 and len(entry[2]) >= 2 and entry[3][0]
        }
        enclosing = {id(parent) for element in qualifying.values() for parent in element.parents}
        for key, element in qualifying.items():
            if key not in enclosing:
                homes.setdefault(key, (element, []))[1].append(row)

    # A row of another table whose smallest element sits inside a record's, and alone
    # there, is part of that record: a picture and its owner in one list item. An
    # element holding several such rows, as a whole list does, is not merged.
    for key, (home, rows) in list(homes.items()):
        if key not in homes:
            continue  # already merged into the record around it
        within = [other for other, (element, _) in homes.items() if other != key and inside(element, home)]
        if len(within) == 1 and not {table for table, _ in homes[within[0]][1]} & {table for table, _ in rows}:
            rows.extend(homes.pop(within[0])[1])

    def widened(home: Tag) -> Tag:
        # The largest element around a record that holds no other record: a picture's
        # whole list item, not only the image inside it.
        others = [element for element, _ in homes.values() if element is not home]
        region = home
        while region is not body and region.parent is not None:
            if any(inside(other, region.parent) for other in others):
                break
            region = region.parent
        return region

    found: list[tuple[int, str, dict[str, str]]] = []  # (first piece, place, record)
    for home, rows in homes.values():
        # The place is where the smallest element sits, which is the same for every
        # record of a list; the values are collected from the widened one.
        place = "/".join(_name(tag) for tag in [*reversed(list(home.parents)), home] if tag.name != "[document]")
        element = widened(home)
        per_table = Counter(table for table, _ in rows)
        groups = [[row] for row in rows] if max(per_table.values()) > 1 else [rows]
        for group in groups:
            # One value per column of each row, where it first appears; one piece of
            # the page counts once, even when it holds the values of several columns.
            first: dict[tuple[tuple[str, int], str], tuple[int, str]] = {}
            for row in group:
                for index, column, form, _ in shown[row]:
                    if inside(pieces[index][0], element):
                        current = first.get((row, column))
                        if current is None or (index, -len(form)) < (current[0], -len(current[1])):
                            first[(row, column)] = (index, form)
            entries = sorted(set(first.values()))
            record: dict[str, str] = {}
            for index, form in entries:
                label, count = pieces[index][1], 1
                while (f"{label} ({count})" if count > 1 else label) in record:
                    count += 1
                record[f"{label} ({count})" if count > 1 else label] = form
            found.append((entries[0][0], place, record))

    found.sort(key=lambda item: item[0])
    places: dict[str, list[dict[str, str]]] = {}
    for _, place, record in found:
        places.setdefault(place, []).append(record)
    return [ExtractedRecords(place, tuple(records)) for place, records in places.items()]

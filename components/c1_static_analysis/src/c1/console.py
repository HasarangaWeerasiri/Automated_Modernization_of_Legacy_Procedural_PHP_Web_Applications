"""Friendly, colourful console output for `python -m c1`.

Only the terminal output changes: every file the pipeline writes stays byte-identical.
Colour is turned off when the output is not a terminal (pipes, CI logs, tests),
when NO_COLOR is set (https://no-color.org), or with --plain.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from typing import Any, Callable

# Banner text drawn with pyfiglet (fonts "ANSI Shadow" and "Slant"), stored here so there is no dependency.
# One-row banners, biggest first; the widest one that fits the window is used.
WIDE = [
    '██████╗  █████╗  ██████╗██╗  ██╗███████╗███╗   ██╗██████╗        ██╗        █████╗ ██╗   ██╗████████╗██╗  ██╗    ███╗   ███╗██╗ ██████╗ ██████╗  █████╗ ████████╗██╗ ██████╗ ███╗   ██╗',
    '██╔══██╗██╔══██╗██╔════╝██║ ██╔╝██╔════╝████╗  ██║██╔══██╗       ██║       ██╔══██╗██║   ██║╚══██╔══╝██║  ██║    ████╗ ████║██║██╔════╝ ██╔══██╗██╔══██╗╚══██╔══╝██║██╔═══██╗████╗  ██║',
    '██████╔╝███████║██║     █████╔╝ █████╗  ██╔██╗ ██║██║  ██║    ████████╗    ███████║██║   ██║   ██║   ███████║    ██╔████╔██║██║██║  ███╗██████╔╝███████║   ██║   ██║██║   ██║██╔██╗ ██║',
    '██╔══██╗██╔══██║██║     ██╔═██╗ ██╔══╝  ██║╚██╗██║██║  ██║    ██╔═██╔═╝    ██╔══██║██║   ██║   ██║   ██╔══██║    ██║╚██╔╝██║██║██║   ██║██╔══██╗██╔══██║   ██║   ██║██║   ██║██║╚██╗██║',
    '██████╔╝██║  ██║╚██████╗██║  ██╗███████╗██║ ╚████║██████╔╝    ██████║      ██║  ██║╚██████╔╝   ██║   ██║  ██║    ██║ ╚═╝ ██║██║╚██████╔╝██║  ██║██║  ██║   ██║   ██║╚██████╔╝██║ ╚████║',
    '╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝╚═════╝     ╚═════╝      ╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝    ╚═╝     ╚═╝╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═══╝',
]
MEDIUM = [
    '░█▀▄░█▀█░█▀▀░█░█░█▀▀░█▀█░█▀▄░░░▄▀░░░░█▀█░█░█░▀█▀░█░█░░░█▄█░▀█▀░█▀▀░█▀▄░█▀█░▀█▀░▀█▀░█▀█░█▀█',
    '░█▀▄░█▀█░█░░░█▀▄░█▀▀░█░█░█░█░░░▄█▀░░░█▀█░█░█░░█░░█▀█░░░█░█░░█░░█░█░█▀▄░█▀█░░█░░░█░░█░█░█░█',
    '░▀▀░░▀░▀░▀▀▀░▀░▀░▀▀▀░▀░▀░▀▀░░░░░▀▀░░░▀░▀░▀▀▀░░▀░░▀░▀░░░▀░▀░▀▀▀░▀▀▀░▀░▀░▀░▀░░▀░░▀▀▀░▀▀▀░▀░▀',
]
NARROW = [
    '╔╗ ╔═╗╔═╗╦╔═╔═╗╔╗╔╔╦╗   ┬   ╔═╗╦ ╦╔╦╗╦ ╦  ╔╦╗╦╔═╗╦═╗╔═╗╔╦╗╦╔═╗╔╗╔',
    '╠╩╗╠═╣║  ╠╩╗║╣ ║║║ ║║  ┌┼─  ╠═╣║ ║ ║ ╠═╣  ║║║║║ ╦╠╦╝╠═╣ ║ ║║ ║║║║',
    '╚═╝╩ ╩╚═╝╩ ╩╚═╝╝╚╝═╩╝  └┘   ╩ ╩╚═╝ ╩ ╩ ╩  ╩ ╩╩╚═╝╩╚═╩ ╩ ╩ ╩╚═╝╝╚╝',
]
LARGE = [
    '▗▄▄▖   ▄    ▄▄ ▗▖ ▄▖▗▄▄▄▖▗▄ ▗▖▗▄▄        ▗▄▖        ▄  ▗▖ ▗▖▗▄▄▄▖▗▖ ▗▖     ▗▄ ▄▖ ▄▄▄   ▄▄ ▗▄▄▖   ▄  ▗▄▄▄▖ ▄▄▄  ▗▄▖ ▗▄ ▗▖',
    '▐▛▀▜▌ ▐█▌  █▀▀▌▐▌▐▛ ▐▛▀▀▘▐█ ▐▌▐▛▀█       █▀▜       ▐█▌ ▐▌ ▐▌▝▀█▀▘▐▌ ▐▌     ▐█ █▌ ▀█▀  █▀▀▌▐▛▀▜▌ ▐█▌ ▝▀█▀▘ ▀█▀  █▀█ ▐█ ▐▌',
    '▐▌ ▐▌ ▐█▌ ▐▛   ▐▙█  ▐▌   ▐▛▌▐▌▐▌ ▐▌      ▜▖        ▐█▌ ▐▌ ▐▌  █  ▐▌ ▐▌     ▐███▌  █  ▐▌   ▐▌ ▐▌ ▐█▌   █    █  ▐▌ ▐▌▐▛▌▐▌',
    '▐███  █ █ ▐▌   ▐██  ▐███ ▐▌█▐▌▐▌ ▐▌      ██▖▄      █ █ ▐▌ ▐▌  █  ▐███▌     ▐▌█▐▌  █  ▐▌▗▄▖▐███  █ █   █    █  ▐▌ ▐▌▐▌█▐▌',
    '▐▌ ▐▌ ███ ▐▙   ▐▌▐▙ ▐▌   ▐▌▐▟▌▐▌ ▐▌     ▐▌▝▙█      ███ ▐▌ ▐▌  █  ▐▌ ▐▌     ▐▌▀▐▌  █  ▐▌▝▜▌▐▌▝█▖ ███   █    █  ▐▌ ▐▌▐▌▐▟▌',
    '▐▙▄▟▌▗█ █▖ █▄▄▌▐▌ █▖▐▙▄▄▖▐▌ █▌▐▙▄█      ▝█▄█▌     ▗█ █▖▝█▄█▘  █  ▐▌ ▐▌     ▐▌ ▐▌ ▄█▄  █▄▟▌▐▌ ▐▌▗█ █▖  █   ▄█▄  █▄█ ▐▌ █▌',
    '▝▀▀▀ ▝▘ ▝▘  ▀▀ ▝▘ ▝▘▝▀▀▀▘▝▘ ▀▘▝▀▀        ▝▀▀▀     ▝▘ ▝▘ ▝▀▘   ▀  ▝▘ ▝▘     ▝▘ ▝▘ ▀▀▀   ▀▀ ▝▘ ▝▀▝▘ ▝▘  ▀   ▀▀▀  ▝▀▘ ▝▘ ▀▘',
]
WIDE_TWO_ROWS = [
    '██████╗  █████╗  ██████╗██╗  ██╗███████╗███╗   ██╗██████╗        ██╗        █████╗ ██╗   ██╗████████╗██╗  ██╗',
    '██╔══██╗██╔══██╗██╔════╝██║ ██╔╝██╔════╝████╗  ██║██╔══██╗       ██║       ██╔══██╗██║   ██║╚══██╔══╝██║  ██║',
    '██████╔╝███████║██║     █████╔╝ █████╗  ██╔██╗ ██║██║  ██║    ████████╗    ███████║██║   ██║   ██║   ███████║',
    '██╔══██╗██╔══██║██║     ██╔═██╗ ██╔══╝  ██║╚██╗██║██║  ██║    ██╔═██╔═╝    ██╔══██║██║   ██║   ██║   ██╔══██║',
    '██████╔╝██║  ██║╚██████╗██║  ██╗███████╗██║ ╚████║██████╔╝    ██████║      ██║  ██║╚██████╔╝   ██║   ██║  ██║',
    '╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝╚═════╝     ╚═════╝      ╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝',
    '',
    '                   ███╗   ███╗██╗ ██████╗ ██████╗  █████╗ ████████╗██╗ ██████╗ ███╗   ██╗',
    '                   ████╗ ████║██║██╔════╝ ██╔══██╗██╔══██╗╚══██╔══╝██║██╔═══██╗████╗  ██║',
    '                   ██╔████╔██║██║██║  ███╗██████╔╝███████║   ██║   ██║██║   ██║██╔██╗ ██║',
    '                   ██║╚██╔╝██║██║██║   ██║██╔══██╗██╔══██║   ██║   ██║██║   ██║██║╚██╗██║',
    '                   ██║ ╚═╝ ██║██║╚██████╔╝██║  ██║██║  ██║   ██║   ██║╚██████╔╝██║ ╚████║',
    '                   ╚═╝     ╚═╝╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═══╝',
]
BANNERS = [WIDE, LARGE, MEDIUM, NARROW]

# Left-to-right colour gradient across the banner: cyan -> blue -> violet -> pink (256-colour codes).
GRADIENT = [51, 45, 39, 33, 27, 63, 99, 135, 141, 177, 171, 165, 201]


def _enable_windows_ansi() -> bool:
    """Turn on ANSI colour codes in the Windows console (cmd / PowerShell, Windows 10+)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


class Console:
    def __init__(self, plain: bool = False, stream=None):
        self.out = stream or sys.stdout
        try:  # box characters must not crash an old code page; replace what cannot be shown
            self.out.reconfigure(errors="replace")
        except Exception:
            pass
        tty = hasattr(self.out, "isatty") and self.out.isatty()
        self.color = not plain and tty and "NO_COLOR" not in os.environ and _enable_windows_ansi()
        self.title_width = 28  # step titles are padded to this width so the results line up
        self.expected: tuple = ()  # errors with a clear message: no traceback
        self.width = shutil.get_terminal_size((100, 24)).columns if tty else 100

    # ---- low level -------------------------------------------------------------------------------
    def c(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return "\033[" + ";".join(codes) + "m" + text + "\033[0m"

    def fg(self, n: int) -> str:
        return f"38;5;{n}"

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    # ---- pieces ----------------------------------------------------------------------------------
    def banner(self, big: bool = False) -> None:
        # big=True (the default from the command line) always uses the largest banner; maximise the
        # window, it is 183 characters wide. big=False picks the largest that fits (--fit).
        fits = lambda b: self.width >= max(map(len, b)) + 3  # noqa: E731
        if big:  # the big title: one row when it fits, else the same letters in two rows
            order = [WIDE, WIDE_TWO_ROWS] + BANNERS[1:]
            art = next((b for b in order if fits(b)), ["BACKEND & AUTH MIGRATION"])
        else:
            art = next((b for b in BANNERS if fits(b)), ["BACKEND & AUTH MIGRATION"])
        w = max(map(len, art))
        self.say()
        for line in art:
            self.say("  " + self._gradient(line, w))
        self.say("  " + self.c("─" * min(w, self.width - 4), self.fg(39)))
        self.say()

    def _gradient(self, line: str, width: int) -> str:
        if not self.color:
            return line
        out, last = [], None
        for x, ch in enumerate(line):
            code = GRADIENT[min(len(GRADIENT) - 1, x * len(GRADIENT) // max(1, width))]
            if code != last:
                out.append(f"\033[1;38;5;{code}m")
                last = code
            out.append(ch)
        return "".join(out) + "\033[0m"

    def info(self, rows: list[tuple[str, str]]) -> None:
        for k, v in rows:
            self.say("  " + self.c(f"{k:<8}", self.fg(245)) + " " + self.c(v, "97"))
        self.say()

    def section(self, title: str) -> None:
        self.say()
        self.say("  " + self.c("► " + title, "1", self.fg(45)))

    def step(self, i: int, n: int, title: str, fn: Callable[[], Any], describe: Callable[[Any], tuple[str, str]]) -> Any:
        dots = "." * max(2, self.title_width + 2 - len(title))
        head = "  " + self.c(f"[{i:>2}/{n}]", self.fg(245)) + " " + self.c(title, "1") + " " + self.c(dots, "2") + " "
        print(head, end="", file=self.out, flush=True)
        t0 = time.perf_counter()
        try:
            result = fn()
        except Exception as e:
            self.say(self.c(" FAIL ", "1", "97", "101") + "  " + self.c(f"{type(e).__name__}: {e}", "91"))
            if not isinstance(e, self.expected):
                self.say(self.c("".join(traceback.format_exc()), "2"))
            raise StepFailed(title) from e
        dt = time.perf_counter() - t0
        text, level = describe(result)
        badge = {"ok": self.c(" OK ", "1", "30", "102"), "warn": self.c(" OK ", "1", "30", "103")}[level]
        colour = {"ok": "97", "warn": "93"}[level]
        self.say(badge + "  " + self.c(text, colour) + "  " + self.c(f"{dt:.1f}s", "2"))
        return result

    def summary(self, rows: list[tuple[str, str, str]]) -> None:
        """rows: (label, value, level) with level ok | warn | bad."""
        inner = max(len(l) for l, _, _ in rows) + 4 + max(len(v) for _, v, _ in rows)
        inner = max(inner, 40)
        top = "╔═ RESULTS " + "═" * (inner - 8) + "╗"
        self.say()
        self.say("  " + self.c(top, self.fg(39)))
        for label, value, level in rows:
            pad = inner - len(label) - len(value)
            colour = {"ok": "92", "warn": "93", "bad": "91"}[level]
            self.say("  " + self.c("║ ", self.fg(39)) + label + " " * pad + self.c(value, "1", colour) + self.c(" ║", self.fg(39)))
        self.say("  " + self.c("╚" + "═" * (inner + 2) + "╝", self.fg(39)))

    def next_steps(self, rows: list[tuple[str, list[str]]]) -> None:
        self.section("Next steps")
        w = max(len(a) for a, _ in rows)
        for what, cmds in rows:
            for j, cmd in enumerate(cmds):
                left = what if j == 0 else ""
                self.say("    " + self.c(f"{left:<{w}}", "97") + "   " + self.c(cmd, self.fg(51)))
        self.say()

    def done(self, seconds: float, fingerprint: str | None = None) -> None:
        self.say("  " + self.c(" DONE ", "1", "30", "106") + "  " + self.c(f"Pipeline finished in {seconds:.1f}s.", "2"))
        if fingerprint:
            self.say("  " + self.c("Output fingerprint ", self.fg(245)) + self.c(fingerprint, "1", self.fg(51))
                     + self.c("   run again: the same input always gives the same fingerprint", "2"))
        self.say()

    def failed(self, title: str) -> None:
        self.say()
        self.say("  " + self.c(" STOPPED ", "1", "97", "41") + "  " + self.c(f"The '{title}' step failed. Nothing after it was run.", "91"))
        self.say("  " + self.c("Copy the red text above when you report the problem.", "2"))
        self.say()


class StepFailed(Exception):
    pass


# ---- plain-English one-liners for each stage's totals --------------------------------------------
def _n(x: Any) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x)


def _get(d: Any, key: str, default: Any = 0) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def _totals(r: Any) -> dict:
    t = _get(r, "totals", None)
    return t if isinstance(t, dict) else (r if isinstance(r, dict) else {})


def describe_extract(r):
    t = _totals(r)
    bad = _get(t, "parseFailures")
    return (f"{_n(_get(t, 'files'))} PHP files  ·  {_n(_get(t, 'nodes'))} AST nodes  ·  {_n(bad)} parse errors",
            "warn" if bad else "ok")


def describe_cfg(r):
    t = _totals(r)
    bad = _get(t, "flags")
    return (f"{_n(_get(t, 'units'))} code units  ·  {_n(_get(t, 'blocks'))} blocks  ·  {_n(bad)} problems", "warn" if bad else "ok")


def describe_includes(r):
    t = _totals(r)
    sites, ok, ex = _get(t, "sites"), _get(t, "resolved"), _get(t, "excluded")
    open_ = sites - ok - ex
    text = f"{_n(ok)}/{_n(sites)} includes resolved" + (f"  ·  {_n(ex)} into excluded code" if ex else "")
    return (text + (f"  ·  {_n(open_)} dynamic, flagged" if open_ > 0 else ""), "warn" if open_ > 0 else "ok")


def describe_dfg(r):
    t = _totals(r)
    return (f"{_n(_get(t, 'defs'))} definitions  ·  {_n(_get(t, 'uses'))} uses  ·  {_n(_get(t, 'queries'))} SQL queries traced", "ok")


def describe_concerns(r):
    t = _totals(r)
    und = _get(t, "undecided") + _get(t, "mixed")
    return (f"{_n(_get(t, 'presentation'))} view  ·  {_n(_get(t, 'business_logic'))} logic  ·  "
            f"{_n(_get(t, 'data_access'))} data  ·  {_n(und)} unsure", "warn" if und else "ok")


def describe_auth(r):
    t = _totals(r)
    g, u = _get(t, "pages:guarded"), _get(t, "pages:unguarded")
    broken, ear = _get(t, "effect:broken-guard"), _get(t, "redirectWithoutExit")
    parts = [f"{_n(g)} guarded pages", f"{_n(u)} unguarded"]
    if broken or ear:
        parts.append(f"{_n(broken + ear)} redirects without exit")
    return ("  ·  ".join(parts), "warn" if (u or broken or ear) else "ok")


def describe_endpoints(r):
    t = _totals(r)
    methods = [f"{_n(_get(t, 'method:' + m))} {m}" for m in ("GET", "POST", "PUT", "PATCH", "DELETE") if _get(t, "method:" + m)]
    return (f"{_n(_get(t, 'endpoints'))} REST endpoints" + (f"  ({' · '.join(methods)})" if methods else ""), "ok")


def describe_contract(r):
    t = _totals(r)
    f = _get(t, "flags")
    return (f"OpenAPI 3.1  ·  {_n(_get(t, 'operations'))} routes  ·  {_n(_get(t, 'schemas'))} models  ·  "
            f"{_n(f)} to review", "warn" if f else "ok")


def describe_timeline(r):
    t = _totals(r)
    return (f"{_n(_get(t, 'entries'))} entries in {_n(_get(t, 'files'))} files  ·  "
            f"{_n(_get(t, 'db_row_field'))} reads traced to the DB", "ok")


def describe_metrics(r):
    t = _totals(r)
    return (f"{_n(_get(t, 'unguardedDataPages'))} unguarded pages  ·  {_n(_get(t, 'sqlFromInputBefore'))} SQL from input  ·  "
            f"{_get(t, 'tangledFilesPercent')}% tangled files (before)", "ok")


def describe_architecture(r):
    t = _totals(r)
    if not _get(t, "score"):
        return ("not enough evidence to suggest a split", "warn")
    name = {"layered-monolith": "Layered monolith", "modular-monolith": "Modular monolith",
            "microservices": "Microservices"}.get(_get(t, "recommended", ""), "?")
    return (f"{name} ({_get(t, 'score')}/100)  ·  {_n(_get(t, 'areas'))} business areas", "ok")


def describe_report(r):
    return ("report.html written", "ok")

"""The friendly console must never leak colour codes into pipes/logs, and must not change any output file."""

import io

from c1 import console as ui


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_plain_output_has_no_escape_codes():
    out = io.StringIO()
    con = ui.Console(stream=out)  # not a terminal -> plain
    con.banner()
    con.step(1, 2, "Trace data flow", lambda: {"totals": {"defs": 3, "uses": 4, "queries": 1}}, ui.describe_dfg)
    con.summary([("Endpoints", "19", "ok")])
    text = out.getvalue()
    assert "\033[" not in text
    assert any(b[0] in text for b in ui.BANNERS) or "BACKEND & AUTH MIGRATION" in text
    assert all(len(b) <= 6 for b in ui.BANNERS)  # every banner is one row of letters
    assert "3 definitions" in text and "1 SQL queries traced" in text


def test_no_color_env_and_plain_flag(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert not ui.Console(stream=FakeTTY()).color
    monkeypatch.delenv("NO_COLOR")
    assert not ui.Console(plain=True, stream=FakeTTY()).color


def test_failed_step_is_reported_and_stops():
    out = io.StringIO()
    con = ui.Console(stream=out)

    def boom():
        raise ValueError("bad input")

    try:
        con.step(1, 1, "Parse PHP into ASTs", boom, ui.describe_extract)
    except ui.StepFailed as e:
        assert str(e) == "Parse PHP into ASTs"
    else:
        raise AssertionError("expected StepFailed")
    assert "FAIL" in out.getvalue() and "ValueError: bad input" in out.getvalue()


def test_descriptions_flag_problems_as_warnings():
    assert ui.describe_auth({"totals": {"pages:guarded": 0, "pages:unguarded": 13}})[1] == "warn"
    assert ui.describe_auth({"totals": {"pages:guarded": 5, "pages:unguarded": 0}})[1] == "ok"
    assert ui.describe_endpoints({"totals": {"endpoints": 2, "method:GET": 1, "method:POST": 1}})[0] == \
        "2 REST endpoints  (1 GET · 1 POST)"

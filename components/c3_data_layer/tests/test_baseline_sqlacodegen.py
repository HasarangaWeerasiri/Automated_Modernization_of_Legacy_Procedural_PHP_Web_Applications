"""sqlacodegen baseline comparison (scripts/baseline_sqlacodegen.py). Small fixture models; no MySQL, no sqlacodegen."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import baseline_sqlacodegen as baseline  # noqa: E402

# C3 style: declarative classes, generic types.
C3_MODELS = '''
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"
    id = mapped_column(Integer, primary_key=True)
    name = mapped_column(String(40), nullable=False)


class Person(Base):
    __tablename__ = "people"
    id = mapped_column(Integer, primary_key=True)
    team_id = mapped_column(Integer, ForeignKey("teams.id"), nullable=True)
    note = mapped_column(Text, nullable=True)
'''

# sqlacodegen style: MySQL dialect types with display widths and collations, Mapped annotations,
# relationships, and a Table() for a table without a primary key.
SQLACODEGEN_MODELS = '''
from typing import Optional

from sqlalchemy import Column, ForeignKey, Table
from sqlalchemy.dialects.mysql import INTEGER, TEXT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Teams(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(40, collation="utf8mb4_general_ci"), nullable=False)

    people: Mapped[list["People"]] = relationship("People", back_populates="team")


class People(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"))
    note: Mapped[Optional[str]] = mapped_column(TEXT)

    team: Mapped[Optional["Teams"]] = relationship("Teams", back_populates="people")
'''

AUDIT_TABLE = '''

t_audit = Table(
    "audit", Base.metadata,
    Column("msg", VARCHAR(20)),
)
'''


def write(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def counts(result, dimension):
    c = result["counts"][dimension]
    return c["compared"], c["matches"], c["differences"]


def test_same_schema_in_two_styles_matches(tmp_path):
    c3 = baseline.load_models(write(tmp_path, "c3.py", C3_MODELS))
    sqla = baseline.load_models(write(tmp_path, "sqla.py", SQLACODEGEN_MODELS))
    result = baseline.compare_models(c3, sqla)
    assert result["verdict"] == "MATCH"
    assert result["differences"] == []
    assert counts(result, "tables") == (2, 2, 0)
    assert counts(result, "columns") == (5, 5, 0)
    assert counts(result, "types") == (5, 5, 0)  # INT(11) = Integer, VARCHAR(40) COLLATE x = String(40)
    assert counts(result, "nullability") == (5, 5, 0)
    assert counts(result, "primary_keys") == (2, 2, 0)
    assert counts(result, "foreign_keys") == (1, 1, 0)


def test_each_difference_is_counted_and_listed_with_both_values(tmp_path):
    changed = (SQLACODEGEN_MODELS
               .replace('VARCHAR(40, collation="utf8mb4_general_ci"), nullable=False',
                        'VARCHAR(50), nullable=True')           # type and nullability
               .replace('team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"))',
                        'team_id: Mapped[Optional[int]] = mapped_column(INTEGER(11))')  # foreign key gone
               .replace('note: Mapped[Optional[str]] = mapped_column(TEXT)',
                        'note: Mapped[Optional[str]] = mapped_column(TEXT)\n'
                        '    extra: Mapped[Optional[str]] = mapped_column(TEXT)'))  # extra column
    c3 = baseline.load_models(write(tmp_path, "c3.py", C3_MODELS))
    sqla = baseline.load_models(write(tmp_path, "sqla.py", changed))
    result = baseline.compare_models(c3, sqla)

    assert counts(result, "tables") == (2, 2, 0)
    assert counts(result, "columns") == (6, 5, 1)
    assert counts(result, "types") == (5, 4, 1)
    assert counts(result, "nullability") == (5, 4, 1)
    assert counts(result, "primary_keys") == (2, 2, 0)
    assert counts(result, "foreign_keys") == (1, 0, 1)
    assert result["verdict"] == "4 difference(s)"
    by_dimension = {d["dimension"]: d for d in result["differences"]}
    assert by_dimension["types"] == {"dimension": "types", "table": "teams", "column": "name",
                                    "c3": "VARCHAR(40)", "sqlacodegen": "VARCHAR(50)",
                                    "c3_rendered": "VARCHAR(40)", "sqlacodegen_rendered": "VARCHAR(50)"}
    assert by_dimension["nullability"]["c3"] is False and by_dimension["nullability"]["sqlacodegen"] is True
    assert by_dimension["columns"] == {"dimension": "columns", "table": "people", "column": "extra", "only_in": "sqlacodegen"}
    assert by_dimension["foreign_keys"]["only_in"] == "c3"
    assert by_dimension["foreign_keys"]["foreign_key"] == {"columns": ["team_id"], "referred_table": "teams",
                                                           "referred_columns": ["id"]}


def test_primary_key_difference(tmp_path):
    no_pk = SQLACODEGEN_MODELS + AUDIT_TABLE
    c3_with_pk = C3_MODELS + '''

class Audit(Base):
    __tablename__ = "audit"
    msg = mapped_column(String(20), primary_key=True)
'''
    result = baseline.compare_models(baseline.load_models(write(tmp_path, "c3.py", c3_with_pk)),
                                     baseline.load_models(write(tmp_path, "sqla.py", no_pk)))
    assert counts(result, "primary_keys") == (3, 2, 1)
    pk = next(d for d in result["differences"] if d["dimension"] == "primary_keys")
    assert pk == {"dimension": "primary_keys", "table": "audit", "c3": ["msg"], "sqlacodegen": []}


def test_missing_table_is_a_table_difference_and_its_columns_are_not_compared(tmp_path):
    c3 = baseline.load_models(write(tmp_path, "c3.py", C3_MODELS))
    sqla = baseline.load_models(write(tmp_path, "sqla.py", SQLACODEGEN_MODELS + AUDIT_TABLE))
    result = baseline.compare_models(c3, sqla)
    assert counts(result, "tables") == (3, 2, 1)
    assert counts(result, "columns") == (5, 5, 0)
    assert result["differences"] == [{"dimension": "tables", "table": "audit", "only_in": "sqlacodegen",
                                      "columns_not_compared": 1}]
    reverse = baseline.compare_models(sqla, c3)
    assert reverse["differences"][0]["only_in"] == "c3"


def test_annotations_explain_views_and_myisam_foreign_keys(tmp_path):
    no_fk = SQLACODEGEN_MODELS.replace('mapped_column(ForeignKey("teams.id"))', "mapped_column(INTEGER(11))")
    result = baseline.compare_models(baseline.load_models(write(tmp_path, "c3.py", C3_MODELS)),
                                     baseline.load_models(write(tmp_path, "sqla.py", no_fk + AUDIT_TABLE)))
    notes = baseline.annotate(result, {"People": {"type": "BASE TABLE", "engine": "MyISAM"},
                                       "teams": {"type": "BASE TABLE", "engine": "InnoDB"},
                                       "audit": {"type": "VIEW", "engine": None}})
    by_dimension = {d["dimension"]: d for d in result["differences"]}
    assert "MyISAM" in by_dimension["foreign_keys"]["note"]
    assert "VIEW" in by_dimension["tables"]["note"]
    assert len(notes) == 2
    # Differences are explained, never removed.
    assert counts(result, "foreign_keys") == (1, 0, 1)


def test_models_file_with_two_metadata_objects_is_rejected(tmp_path):
    source = "from sqlalchemy import MetaData\na = MetaData()\nb = MetaData()\n"
    with pytest.raises(ValueError, match="expected one SQLAlchemy MetaData"):
        baseline.load_models(write(tmp_path, "two.py", source))


def test_the_same_file_loads_twice(tmp_path):
    path = write(tmp_path, "sqla.py", SQLACODEGEN_MODELS)
    assert baseline.load_models(path) == baseline.load_models(path)


def test_sanitise_removes_password_and_url_credentials(monkeypatch):
    monkeypatch.setenv("C3_DB_PASSWORD", "fixture-not-a-real-secret")
    text = baseline.sanitise("failed: mysql+pymysql://c3_tester:fixture-not-a-real-secret@127.0.0.1/db "
                             "and fixture-not-a-real-secret again")
    assert "fixture-not-a-real-secret" not in text
    assert "c3_tester:" not in text


def test_sqlacodegen_query_fields_are_not_applicable_never_zero():
    fields = baseline.sqlacodegen_not_applicable()
    assert set(fields) == {"live_query_sites", "recovered_sites", "data_access_functions_generated",
                           "manual_review_count", "abstentions"}
    assert all(isinstance(v, str) and v.startswith("not applicable") for v in fields.values())


def test_c3_counts_copy_run_outputs(tmp_path):
    (tmp_path / "summary.json").write_text(
        '{"total_queries": 7, "recovered_queries": 6, "converted_queries": 5, "converted_by_general_rules": 4, '
        '"converted_by_query_specific_template": 1, "unresolved_total": 3}', encoding="utf-8")
    review = {"label": None, "live_sites": 6, "live_sites_source": "fixture", "not_converted_live": 2, "value": 33.3,
              "sites": [{"query_id": "Q1", "gt_id": "G1", "site": "a.php:1", "stage": "recovery", "reason_code": "R_A"},
                        {"query_id": "Q2", "gt_id": "G2", "site": "a.php:2", "stage": "conversion", "reason_code": "R_B"}],
              "excluded_non_live": [{"query_id": "Q3", "gt_id": "G3", "site": "b.php:9", "stage": "recovery",
                                     "reason_code": "R_A"}]}
    c = baseline.c3_counts(tmp_path, review)
    assert (c["live_query_sites"], c["detected_sites"], c["recovered_sites"]) == (6, 7, 6)
    assert c["data_access_functions_generated"] == 5
    assert c["manual_review_count"] == 2
    assert c["abstentions"]["total"] == 3
    assert (c["abstentions"]["live"], c["abstentions"]["non_live"]) == (2, 1)
    assert c["abstentions"]["by_reason_code"] == {"R_A": 2, "R_B": 1}
    assert c["abstentions"]["by_stage"] == {"recovery": 2, "conversion": 1}
    assert all("reason" not in s or s.get("reason") is None for s in c["abstentions"]["sites"])


def test_refuses_without_password_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.delenv("C3_DB_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="C3_DB_PASSWORD is not set"):
        baseline.main(["--out", str(tmp_path / "new")])
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        baseline.main(["--out", str(tmp_path)])


def test_main_writes_every_report_and_records_a_failure(tmp_path, monkeypatch):
    """The whole run with the database step and the C3 run replaced by fixtures: one app compared, one failed."""

    import json

    import summarize_evidence

    runs = tmp_path / "runs"
    for app in ("simple_crud", "mixed_crud"):
        folder = runs / app
        folder.mkdir(parents=True)
        write(folder, "models.py", C3_MODELS)
        for name in ("data_access.py", "queries.json", "unresolved_queries.json"):
            write(folder, name, "[]")
        write(folder, "summary.json", json.dumps({
            "total_queries": 2, "recovered_queries": 2, "converted_queries": 1, "converted_by_general_rules": 1,
            "converted_by_query_specific_template": 0, "unresolved_total": 1}))
    review = {app: {"label": "pipeline counts, not verified", "live_sites": 2, "live_sites_source": "fixture",
                    "not_converted_live": 1, "value": 50.0, "excluded_non_live": [],
                    "sites": [{"query_id": "Q1", "gt_id": None, "site": "x.php:3", "stage": "conversion",
                               "reason_code": "R_FIXTURE"}]} for app in ("simple_crud", "mixed_crud")}

    def fake_generate(python, database, outfile, infofile):
        if database == "c3_e3_mixed_crud":
            return "OperationalError: fixture failure text"
        outfile.write_text(SQLACODEGEN_MODELS, encoding="utf-8")
        infofile.write_text(json.dumps({"database": database, "tables": {
            "teams": {"type": "BASE TABLE", "engine": "InnoDB"}, "people": {"type": "BASE TABLE", "engine": "InnoDB"}}}),
            encoding="utf-8")
        return None

    monkeypatch.setenv("C3_DB_PASSWORD", "fixture-not-a-real-secret")
    monkeypatch.setattr(baseline, "run_generate", fake_generate)
    monkeypatch.setattr(baseline, "tool_versions", lambda python: {"python": "x", "sqlacodegen": "0.0-fixture",
                                                                   "SQLAlchemy": "x", "PyMySQL": "x"})
    monkeypatch.setattr(summarize_evidence, "git", lambda *args: "fixturehead")
    monkeypatch.setattr(summarize_evidence, "pipeline_unchanged_since", lambda head: None)
    monkeypatch.setattr(summarize_evidence, "compute_manual_review", lambda base: review)
    out = tmp_path / "out"

    code = baseline.main(["--out", str(out), "--apps", "simple_crud,mixed_crud", "--c3-runs", str(runs),
                          "--sqlacodegen-python", sys.executable])

    assert code == 1  # a failure is reported through the exit code, not hidden
    report = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert report["apps"]["simple_crud"]["status"] == "compared"
    assert report["apps"]["simple_crud"]["comparison"]["verdict"] == "MATCH"
    assert report["apps"]["mixed_crud"]["status"] == "not compared"
    assert report["failures"] == {"mixed_crud": "OperationalError: fixture failure text"}
    assert (out / "mixed_crud" / "sqlacodegen_error.txt").exists()
    assert (out / "simple_crud" / "sqlacodegen_models.py").read_text(encoding="utf-8") == SQLACODEGEN_MODELS
    assert report["apps"]["simple_crud"]["sqlacodegen_only"] == baseline.sqlacodegen_not_applicable()
    assert not list(out.glob("*_database.json"))
    text = (out / "summary.txt").read_text(encoding="utf-8")
    assert "mixed_crud: OperationalError: fixture failure text" in text
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "not used by the C3 pipeline" in readme
    assert "by design of the two tools, not a win for C3" in readme
    assert "sqlacodegen **0.0-fixture**" in readme
    for path in out.rglob("*"):
        if path.is_file():
            assert "fixture-not-a-real-secret" not in path.read_text(encoding="utf-8")

from pathlib import Path

import pytest

from c4 import containers
from c4.config import load_config


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    return load_config("demo", environ={}, root=tmp_path)


def _readiness(**overrides):
    readiness = {
        "c4-gateway": True,
        "c4-legacy-db": True,
        "c4-legacy-web": True,
        "c4-migrated-db": True,
        "c4-migrated-api": True,
    }
    readiness.update(overrides)
    return readiness


def test_returns_as_soon_as_every_container_is_ready(config, monkeypatch):
    monkeypatch.setattr(containers, "probe", lambda _: _readiness())

    assert containers.wait_until_ready(config, timeout=0) == _readiness()


def test_keeps_polling_until_the_last_container_is_ready(config, monkeypatch):
    answers = iter([
        _readiness(**{"c4-migrated-api": False}),
        _readiness(**{"c4-migrated-api": False}),
        _readiness(),
    ])
    monkeypatch.setattr(containers, "probe", lambda _: next(answers))

    assert containers.wait_until_ready(config, timeout=30, interval=0) == _readiness()


def test_timeout_names_the_containers_that_are_not_ready(config, monkeypatch):
    stuck = _readiness(**{"c4-legacy-web": False, "c4-migrated-db": False})
    monkeypatch.setattr(containers, "probe", lambda _: stuck)

    with pytest.raises(containers.NotReadyError, match="c4-legacy-web, c4-migrated-db"):
        containers.wait_until_ready(config, timeout=0, interval=0)


def test_probe_covers_the_web_and_database_container_of_both_systems(config, monkeypatch):
    class FakeClient:
        def close(self):
            pass

    monkeypatch.setattr(containers, "docker_client", lambda: FakeClient())
    monkeypatch.setattr(containers, "_state", lambda client, name: {"Status": "running"})
    monkeypatch.setattr(containers, "_db_ready", lambda client, name: True)
    monkeypatch.setattr(containers, "_web_ready", lambda client, name, url: url.endswith(("/", "/openapi.json")))

    assert containers.probe(config) == _readiness()


# ------------------------------------------------------------- seed preparation

from c4.containers import prepare_seed, split_seed  # noqa: E402

DUMP = (
    b"-- MySQL dump\r\n"
    b"CREATE DATABASE IF NOT EXISTS `app`;\r\n"
    b"CREATE USER 'app'@'%' IDENTIFIED BY 'secret';\r\n"
    b"GRANT SELECT, INSERT\r\n"
    b"  ON `app`.* TO 'app'@'%';\r\n"
    b"USE `app`;\r\n"
    b"CREATE TABLE `notes` (`body` text);\r\n"
    b"INSERT INTO `notes` VALUES ('GRANT is a word in this note'),('Ren\xe9e');\r\n"
    b"FLUSH PRIVILEGES;\r\n"
)


def test_account_statements_are_split_from_the_data():
    data, accounts = split_seed(DUMP)

    assert accounts == (
        b"CREATE USER 'app'@'%' IDENTIFIED BY 'secret';\r\n"
        b"GRANT SELECT, INSERT\r\n"
        b"  ON `app`.* TO 'app'@'%';\r\n"
        b"FLUSH PRIVILEGES;\r\n"
    )
    assert b"CREATE TABLE `notes`" in data and b"USE `app`;" in data


def test_the_data_keeps_every_byte_and_line_ending_it_had():
    data, accounts = split_seed(DUMP)

    assert len(data) + len(accounts) == len(DUMP)
    assert b"('GRANT is a word in this note'),('Ren\xe9e');\r\n" in data


def test_a_dump_without_account_statements_is_kept_exactly():
    plain = b"CREATE TABLE t (a int);\nINSERT INTO t VALUES (1);\n"

    assert split_seed(plain) == (plain, b"")


def test_prepared_copies_are_written_where_compose_reads_them(tmp_path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "dump.sql").write_bytes(DUMP)
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./dump.sql\nC4_LEGACY_DB_NAME=app\n"
    )
    config = load_config("demo", environ={}, root=tmp_path)

    prepare_seed(config)

    folder = tmp_path / "benchmarks" / "demo" / "prepared"
    assert (folder / "legacy_accounts.sql").read_bytes().startswith(b"CREATE USER")
    assert b"CREATE USER" not in (folder / "legacy_data.sql").read_bytes()
    assert (folder / "migrated_data.sql").read_bytes() == (folder / "legacy_data.sql").read_bytes()
    assert config.compose_env["C4_LEGACY_DATA"] == "./benchmarks/demo/prepared/legacy_data.sql"

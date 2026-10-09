from pathlib import Path

import pytest

from c4.config import ConfigError, load_config

DESCRIPTOR = (
    "C4_LEGACY_DOCROOT=./benchmarks/demo/app\n"
    "C4_LEGACY_SEED=./benchmarks/demo/app/seed.sql\n"
    "C4_LEGACY_DB_NAME=demodb\n"
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(DESCRIPTOR)
    return tmp_path


def test_documented_topology_is_the_default(root):
    config = load_config("demo", environ={}, root=root)

    assert config.legacy.base_url == "http://127.0.0.1:8080"
    assert config.migrated.base_url == "http://127.0.0.1:8000"
    assert config.legacy.db_url == "mysql+pymysql://root@127.0.0.1:3307/demodb"
    assert config.migrated.db_url == "mysql+pymysql://root:c4migrated@127.0.0.1:3308/demodb"


def test_descriptor_supplies_everything_about_the_application(root):
    config = load_config("demo", environ={}, root=root)

    assert config.benchmark == "demo"
    assert config.legacy.db_name == "demodb"
    assert config.legacy.seed_file == (root / "benchmarks/demo/app/seed.sql").resolve()
    assert config.oracle_dir == root.resolve() / "oracles" / "demo"
    assert config.report_dir == root.resolve() / "reports" / "demo"


def test_both_sides_share_the_seed_and_database_name_by_default(root):
    config = load_config("demo", environ={}, root=root)

    assert config.migrated.seed_file == config.legacy.seed_file
    assert config.migrated.db_name == config.legacy.db_name


def test_local_env_file_overrides_defaults(root):
    (root / ".env").write_text("C4_LEGACY_WEB_PORT=18080\n")

    config = load_config("demo", environ={}, root=root)

    assert config.legacy.base_url == "http://127.0.0.1:18080"


def test_process_environment_overrides_files(root):
    (root / ".env").write_text("C4_LEGACY_WEB_PORT=18080\n")

    config = load_config("demo", environ={"C4_LEGACY_WEB_PORT": "28080"}, root=root)

    assert config.legacy.base_url == "http://127.0.0.1:28080"


def test_benchmark_is_selected_by_environment_then_local_file(root):
    (root / "benchmarks" / "other.env").write_text(DESCRIPTOR.replace("demodb", "otherdb"))
    (root / ".env").write_text("C4_BENCHMARK=demo\n")

    assert load_config(environ={}, root=root).benchmark == "demo"
    assert load_config(environ={"C4_BENCHMARK": "other"}, root=root).benchmark == "other"


def test_migrated_database_url_can_point_at_another_engine(root):
    url = "postgresql+psycopg://c4:c4@127.0.0.1:5432/demodb"

    config = load_config("demo", environ={"C4_MIGRATED_DB_URL": url}, root=root)

    assert config.migrated.db_url == url


def test_compose_receives_every_resolved_value(root):
    (root / ".env").write_text("C4_LEGACY_DB_PORT=13307\n")

    env = load_config("demo", environ={}, root=root).compose_env

    assert env["C4_LEGACY_DB_PORT"] == "13307"
    assert env["C4_LEGACY_DB_NAME"] == "demodb"
    assert env["C4_MIGRATED_API_PORT"] == "8000"


def test_no_benchmark_selected_is_an_error(root):
    with pytest.raises(ConfigError, match="No benchmark selected"):
        load_config(environ={}, root=root)


def test_unknown_benchmark_lists_the_available_ones(root):
    with pytest.raises(ConfigError, match="Available: demo"):
        load_config("missing", environ={}, root=root)


def test_incomplete_descriptor_names_the_missing_setting(root):
    (root / "benchmarks" / "broken.env").write_text("C4_LEGACY_DOCROOT=./x\n")

    with pytest.raises(ConfigError, match="C4_LEGACY_SEED, C4_LEGACY_DB_NAME"):
        load_config("broken", environ={}, root=root)

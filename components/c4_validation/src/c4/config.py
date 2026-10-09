"""All environment detail for the validation engine.

No other module hardcodes a URL, DSN, port, path or container name (NFR5). Values
are resolved in the order Docker Compose itself uses, so the engine and the
containers always agree:

    process environment  >  benchmarks/<app>.env  >  .env  >  DEFAULTS

Nothing here is specific to one application. Which application is under validation
is itself a setting (C4_BENCHMARK), and everything known about that application
comes from its descriptor file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[2]

# Must match the fallbacks written in docker-compose.yml.
DEFAULTS = {
    "C4_LEGACY_WEB_PORT": "8080",
    "C4_LEGACY_DB_PORT": "3307",
    "C4_MIGRATED_API_PORT": "8000",
    "C4_MIGRATED_DB_PORT": "3308",
    "C4_MIGRATED_DB_PASSWORD": "c4migrated",
    "C4_MIGRATED_APP": "./stub_api",
    "C4_LEGACY_READY_PATH": "/",
    # Every FastAPI application serves its own schema here, so readiness needs no
    # endpoint added to the application under validation.
    "C4_MIGRATED_READY_PATH": "/openapi.json",
    "C4_READY_TIMEOUT": "120",
    "C4_OUTPUT_DIR": ".",
}

REQUIRED_IN_DESCRIPTOR = ("C4_LEGACY_DOCROOT", "C4_LEGACY_SEED", "C4_LEGACY_DB_NAME")

# Fixed by docker-compose.yml.
COMPOSE_FILE = "docker-compose.yml"
LEGACY_WEB_CONTAINER = "c4-legacy-web"
LEGACY_DB_CONTAINER = "c4-legacy-db"
MIGRATED_API_CONTAINER = "c4-migrated-api"
MIGRATED_DB_CONTAINER = "c4-migrated-db"

# Every published port is bound to loopback. The address is used rather than the
# name "localhost", which can resolve to IPv6 and reach a different listener.
HOST = "127.0.0.1"


class ConfigError(Exception):
    """The environment is not described well enough to run."""


@dataclass(frozen=True)
class System:
    """One side of the comparison: a web application and its database."""

    name: str
    base_url: str
    ready_path: str
    web_container: str
    db_container: str
    db_url: str
    db_name: str
    seed_file: Path


@dataclass(frozen=True)
class Config:
    benchmark: str
    root: Path
    compose_file: Path
    compose_env: Mapping[str, str]
    legacy: System
    migrated: System
    oracle_dir: Path
    report_dir: Path
    ready_timeout: float

    @property
    def systems(self) -> tuple[System, System]:
        return (self.legacy, self.migrated)


def _read_env_file(path: Path) -> dict[str, str]:
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def _mysql_url(port: str, database: str, password: str | None) -> str:
    url = URL.create(
        "mysql+pymysql",
        username="root",
        password=password,
        host=HOST,
        port=int(port),
        database=database,
    )
    return url.render_as_string(hide_password=False)


def load_config(
    benchmark: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    root: Path = ROOT,
) -> Config:
    """Resolve the configuration for one benchmark.

    The benchmark is taken from the argument, then C4_BENCHMARK in the process
    environment, then C4_BENCHMARK in .env. There is no built-in default: the
    engine does not prefer any application.
    """
    environ = os.environ if environ is None else environ
    local_file = root / ".env"
    local = _read_env_file(local_file) if local_file.is_file() else {}

    benchmark = benchmark or environ.get("C4_BENCHMARK") or local.get("C4_BENCHMARK")
    if not benchmark:
        raise ConfigError(
            "No benchmark selected. Set C4_BENCHMARK in the environment or in .env."
        )

    descriptor_file = root / "benchmarks" / f"{benchmark}.env"
    if not descriptor_file.is_file():
        available = sorted(path.stem for path in (root / "benchmarks").glob("*.env"))
        raise ConfigError(
            f"No descriptor for benchmark '{benchmark}' at {descriptor_file}. "
            f"Available: {', '.join(available) or 'none'}."
        )
    descriptor = _read_env_file(descriptor_file)

    missing = [key for key in REQUIRED_IN_DESCRIPTOR if not descriptor.get(key)]
    if missing:
        raise ConfigError(f"{descriptor_file} does not set: {', '.join(missing)}.")

    overrides = {key: value for key, value in environ.items() if key.startswith("C4_")}
    values = {**DEFAULTS, **local, **descriptor, **overrides, "C4_BENCHMARK": benchmark}

    # Both sides start from the same dump and database name unless a benchmark
    # says otherwise (FR2).
    values.setdefault("C4_MIGRATED_DB_NAME", values["C4_LEGACY_DB_NAME"])
    values.setdefault("C4_MIGRATED_SEED", values["C4_LEGACY_SEED"])

    def path(key: str) -> Path:
        return (root / values[key]).resolve()

    legacy = System(
        name="legacy",
        base_url=f"http://{HOST}:{values['C4_LEGACY_WEB_PORT']}",
        ready_path=values["C4_LEGACY_READY_PATH"],
        web_container=LEGACY_WEB_CONTAINER,
        db_container=LEGACY_DB_CONTAINER,
        # Benchmarks connect as root with an empty password; see docker-compose.yml.
        db_url=_mysql_url(values["C4_LEGACY_DB_PORT"], values["C4_LEGACY_DB_NAME"], None),
        db_name=values["C4_LEGACY_DB_NAME"],
        seed_file=path("C4_LEGACY_SEED"),
    )
    migrated = System(
        name="migrated",
        base_url=f"http://{HOST}:{values['C4_MIGRATED_API_PORT']}",
        ready_path=values["C4_MIGRATED_READY_PATH"],
        web_container=MIGRATED_API_CONTAINER,
        db_container=MIGRATED_DB_CONTAINER,
        # A full URL can be supplied to point at another engine, e.g. PostgreSQL (NFR4).
        db_url=values.get("C4_MIGRATED_DB_URL")
        or _mysql_url(
            values["C4_MIGRATED_DB_PORT"],
            values["C4_MIGRATED_DB_NAME"],
            values["C4_MIGRATED_DB_PASSWORD"],
        ),
        db_name=values["C4_MIGRATED_DB_NAME"],
        seed_file=path("C4_MIGRATED_SEED"),
    )

    output = path("C4_OUTPUT_DIR")
    return Config(
        benchmark=benchmark,
        root=root,
        compose_file=root / COMPOSE_FILE,
        compose_env=values,
        legacy=legacy,
        migrated=migrated,
        oracle_dir=output / "oracles" / benchmark,
        report_dir=output / "reports" / benchmark,
        ready_timeout=float(values["C4_READY_TIMEOUT"]),
    )

"""Environment smoke tests.

Nothing downstream is trustworthy until these pass: both systems reachable, both
databases seeded identically, every test starting from the baseline, and neither
system able to reach the outside network.

They name no table, page or port of any particular benchmark.
"""

import pytest
import requests
from sqlalchemy import MetaData, create_engine, func, select

from c4 import containers, dbstate

pytestmark = pytest.mark.environment


@pytest.fixture(params=["legacy", "migrated"])
def system(request, at_baseline):
    return getattr(at_baseline, request.param)


@pytest.fixture
def docker(environment):
    client = containers.docker_client()
    yield client
    client.close()


def _validated_containers(config):
    return [name for system in config.systems for name in (system.web_container, system.db_container)]


def _empty_every_table(system):
    engine = create_engine(system.db_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    with engine.begin() as connection:
        for table in reversed(metadata.sorted_tables):
            connection.execute(table.delete())
    engine.dispose()


# ------------------------------------------------------------------- reachability


def test_every_container_is_ready(environment):
    assert all(containers.probe(environment).values())


def test_system_answers_over_http(system):
    session = requests.Session()
    session.trust_env = False
    response = session.get(system.base_url + system.ready_path, timeout=10)

    assert response.status_code < 400


# ------------------------------------------------------------- identical baselines


def test_database_is_seeded(system, baselines):
    assert sum(len(table.rows) for table in baselines[system.name].tables.values()) > 0


def test_both_databases_hold_the_same_tables(baselines):
    assert set(baselines["legacy"].tables) == set(baselines["migrated"].tables)


def test_both_databases_hold_the_same_data(baselines):
    assert baselines["legacy"].data_fingerprint == baselines["migrated"].data_fingerprint


# ---------------------------------------------------------------- test isolation


def test_pair_first_empties_both_databases(at_baseline, baselines):
    for system in at_baseline.systems:
        _empty_every_table(system)
        assert dbstate.snapshot(system).data_fingerprint != baselines[system.name].data_fingerprint


def test_pair_second_starts_from_the_baseline_again(at_baseline, baselines):
    # Runs after the test above in file order, and must not see what it did.
    for system in at_baseline.systems:
        assert dbstate.snapshot(system).state_fingerprint == baselines[system.name].state_fingerprint


def test_a_reset_reaches_connections_opened_before_it(system, baselines):
    # A running application keeps its database connections open across a reset.
    baseline = baselines[system.name]
    name = next(name for name, table in baseline.tables.items() if table.rows)
    engine = create_engine(system.db_url)
    metadata = MetaData()
    metadata.reflect(bind=engine, only=[name])
    count = select(func.count()).select_from(metadata.tables[name])
    held = engine.connect()
    try:
        assert held.execute(count).scalar() == len(baseline.tables[name].rows)
        held.commit()
        _empty_every_table(system)
        assert held.execute(count).scalar() == 0
        held.commit()

        dbstate.reset(system)

        assert held.execute(count).scalar() == len(baseline.tables[name].rows)
    finally:
        held.close()
        engine.dispose()


# -------------------------------------------------------------- network isolation


def test_no_validated_container_has_a_route_out(environment):
    assert containers.isolated(environment) == {
        name: True for name in _validated_containers(environment)
    }


def test_outbound_connection_attempts_fail(environment, docker):
    # 192.0.2.1 is reserved for documentation, so no real host is ever contacted.
    attempt = "timeout 5 bash -c 'echo > /dev/tcp/192.0.2.1/80' 2>&1"
    for name in _validated_containers(environment):
        container = docker.containers.get(name)
        if container.exec_run(["sh", "-c", "command -v bash"]).exit_code != 0:
            continue  # no shell to attempt from; the route check above still covers it
        result = container.exec_run(["bash", "-c", attempt])

        assert result.exit_code != 0, name
        assert b"Network is unreachable" in result.output, name


def test_only_the_gateway_publishes_ports_and_only_on_loopback(environment, docker):
    def bindings(name):
        ports = docker.containers.get(name).attrs["NetworkSettings"]["Ports"] or {}
        return [binding for published in ports.values() for binding in published or []]

    for name in _validated_containers(environment):
        assert bindings(name) == [], name

    gateway = bindings(environment.gateway_container)
    assert gateway
    assert {binding["HostIp"] for binding in gateway} == {"127.0.0.1"}


# -------------------------------------------------------------- benchmark safety


def test_nothing_is_mounted_writable_from_this_machine(environment, docker):
    # The benchmark source, the seed dumps and the migrated application are all
    # read-only inside the containers: a run cannot alter its own inputs.
    for name in [*_validated_containers(environment), environment.gateway_container]:
        for mount in docker.containers.get(name).attrs["Mounts"]:
            if mount["Type"] == "bind":
                assert mount["RW"] is False, f"{name}: {mount['Destination']}"

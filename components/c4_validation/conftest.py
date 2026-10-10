"""Reset fixtures.

Shared by the engine's own tests and, later, by the generated equivalence harness.
A test that asks for `at_baseline` is guaranteed to start with both databases at
the verified seed baseline, whatever ran before it and in whatever order.
"""

import pytest

from c4 import containers, dbstate
from c4.config import Config, load_config


@pytest.fixture(scope="session")
def config() -> Config:
    return load_config()


@pytest.fixture(scope="session")
def environment(config: Config) -> Config:
    """Both systems, confirmed reachable before anything is validated (FR1)."""
    try:
        readiness = containers.probe(config)
    except containers.DockerUnavailableError as error:
        pytest.fail(str(error), pytrace=False)
    waiting = sorted(name for name, ready in readiness.items() if not ready)
    if waiting:
        pytest.fail(
            f"The environment is not up ({', '.join(waiting)} not ready). "
            "Start it with: python -m c4.containers up",
            pytrace=False,
        )
    return config


@pytest.fixture(scope="session")
def baselines(environment: Config):
    """Reload both databases once and record the state every scenario starts from.

    The baseline is whatever a reload produces, never whatever a container happens
    to hold when the session starts.
    """
    recorded = {system.name: dbstate.reset(system) for system in environment.systems}
    yield recorded
    for system in environment.systems:
        dbstate.ensure_baseline(system, recorded[system.name])


@pytest.fixture
def at_baseline(environment: Config, baselines) -> Config:
    """Both databases verified at the baseline; reloaded only if left dirty."""
    for system in environment.systems:
        dbstate.ensure_baseline(system, baselines[system.name])
    return environment

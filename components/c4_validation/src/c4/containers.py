"""Stage 0 — container lifecycle and readiness.

docker-compose.yml declares the topology. This module brings it up with the values
from config and confirms that both systems are reachable before anything is
validated (FR1).

Compose is invoked through its CLI because the Docker SDK has no Compose API. The
SDK is used for everything that inspects a running container.

    python -m c4.containers up | down | status
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import docker
import requests
from docker.errors import DockerException, NotFound

from c4.config import Config, ConfigError, load_config


class ComposeError(RuntimeError):
    """Docker Compose could not carry out a lifecycle command."""


class NotReadyError(RuntimeError):
    """One or more containers did not become ready in time."""


class DockerUnavailableError(RuntimeError):
    """The Docker daemon cannot be reached."""


def docker_client() -> docker.DockerClient:
    """Connect to the Docker daemon, or say plainly that it is not running."""
    try:
        return docker.from_env()
    except DockerException as error:
        raise DockerUnavailableError("Cannot reach Docker. Is Docker running?") from error


def _compose(config: Config, *args: str) -> None:
    command = [
        "docker", "compose",
        "--project-directory", str(config.root),
        "-f", str(config.compose_file),
        *args,
    ]
    result = subprocess.run(
        command,
        env={**os.environ, **config.compose_env},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ComposeError(f"`{' '.join(command)}` failed:\n{result.stderr.strip()}")


def up(config: Config, *, build: bool = True) -> dict[str, bool]:
    """Start both systems and return once every container is ready."""
    _compose(config, "up", "-d", *(["--build"] if build else []))
    return wait_until_ready(config)


def down(config: Config, *, remove_volumes: bool = False) -> None:
    """Stop and remove the containers. Volumes hold only the shared database socket."""
    _compose(config, "down", *(["-v"] if remove_volumes else []))


def _state(client: docker.DockerClient, name: str) -> dict:
    try:
        return client.containers.get(name).attrs["State"]
    except NotFound:
        return {}


def _db_ready(client: docker.DockerClient, name: str) -> bool:
    # The health check passes only after the seed has loaded; see docker-compose.yml.
    return _state(client, name).get("Health", {}).get("Status") == "healthy"


def _web_ready(client: docker.DockerClient, name: str, url: str) -> bool:
    # The container must itself be running: an answer on the port alone could come
    # from an unrelated process that holds the same port on this machine.
    if _state(client, name).get("Status") != "running":
        return False
    session = requests.Session()
    session.trust_env = False  # never route a local request through a proxy
    try:
        response = session.get(url, timeout=3, allow_redirects=False)
    except requests.RequestException:
        return False
    finally:
        session.close()
    return response.status_code < 500


def probe(config: Config) -> dict[str, bool]:
    """Report, for each container, whether it is ready right now."""
    client = docker_client()
    try:
        readiness = {}
        for system in config.systems:
            readiness[system.db_container] = _db_ready(client, system.db_container)
            readiness[system.web_container] = _web_ready(
                client, system.web_container, system.base_url + system.ready_path
            )
        return readiness
    finally:
        client.close()


def wait_until_ready(
    config: Config, *, timeout: float | None = None, interval: float = 1.0
) -> dict[str, bool]:
    """Poll until every container is ready, or raise NotReadyError."""
    timeout = config.ready_timeout if timeout is None else timeout
    deadline = time.monotonic() + timeout
    while True:
        readiness = probe(config)
        if all(readiness.values()):
            return readiness
        if time.monotonic() >= deadline:
            waiting = sorted(name for name, ready in readiness.items() if not ready)
            raise NotReadyError(f"Not ready after {timeout:g}s: {', '.join(waiting)}.")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m c4.containers", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("up", "down", "status"))
    parser.add_argument("--benchmark", help="benchmark name; defaults to C4_BENCHMARK")
    parser.add_argument("--no-build", action="store_true", help="up: do not rebuild images")
    parser.add_argument("--volumes", action="store_true", help="down: also remove volumes")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.benchmark)
        if args.command == "down":
            down(config, remove_volumes=args.volumes)
            print(f"{config.benchmark}: stopped")
            return 0
        readiness = up(config, build=not args.no_build) if args.command == "up" else probe(config)
    except (ConfigError, ComposeError, DockerUnavailableError, NotReadyError) as error:
        print(error, file=sys.stderr)
        return 1

    urls = {system.web_container: system.base_url for system in config.systems}
    print(f"benchmark: {config.benchmark}")
    for name, ready in readiness.items():
        print(f"  {name:<18} {'ready' if ready else 'NOT READY':<10} {urls.get(name, '')}".rstrip())
    return 0 if all(readiness.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

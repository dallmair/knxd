"""Run the integration suite under pytest.

Automake runs this file as a single test, so `make test` reports one result for the whole suite.

Tests marked `multicast` use fixed groups and ports and may reach the LAN, so they only run
in a network namespace, or on the host when KNXD_MULTICAST=host. See test/README.md.
"""

import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path

NO_TESTS_COLLECTED = 5
HERE = Path(__file__).resolve().parent
NETNS_RUN = str(HERE / "netns-run.sh")


def _pytest_cmd(*args: str) -> list[str]:
    return [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(HERE / "integration"), *args]


def _result(status: int) -> int:
    return 0 if status == NO_TESTS_COLLECTED else status


def _status(cmd: list[str]) -> int:
    return _result(subprocess.call(cmd))


def _multicast_count() -> int:
    collect = subprocess.run(
        _pytest_cmd("-m", "multicast", "--collect-only", "-q"), capture_output=True, text=True, check=False
    )
    if collect.returncode not in {0, NO_TESTS_COLLECTED}:
        sys.stderr.write(collect.stdout + collect.stderr)
        raise SystemExit(collect.returncode)
    return sum("::" in line for line in collect.stdout.splitlines())


def _multicast_parallel_cmd(mode: str, workers: int) -> list[str]:
    # Each xdist worker starts through netns-run.sh, so it gets a network namespace of its own.
    worker = shlex.join(["sh", NETNS_RUN, mode, sys.executable])
    return _pytest_cmd("-m", "multicast", "--dist", "load", "--tx", f"{workers}*popen//python={worker}")


if __name__ == "__main__":
    mode = os.environ.get("KNXD_MULTICAST", "auto")
    xdist = importlib.util.find_spec("xdist") is not None
    others = _pytest_cmd(*(["-n", "auto"] if xdist else []), "-m", "not multicast")
    if mode == "host" or not xdist:
        # Without namespaces per worker, multicast tests run after the others, one at a time.
        status = _status(others)
        raise SystemExit(status or _status(["sh", NETNS_RUN, mode, *_pytest_cmd("-m", "multicast")]))
    workers = min(_multicast_count(), os.cpu_count() or 1)
    if workers == 0:
        raise SystemExit(_status(others))
    # Fails early if netns mode has no namespaces, and lets sudo ask for the password only once.
    probe = subprocess.call(["sh", NETNS_RUN, mode, "true"])
    if probe:
        raise SystemExit(probe)
    # The groups run one after the other so their output does not interleave in the log.
    status = _status(others)
    multicast_status = _status(_multicast_parallel_cmd(mode, workers))
    raise SystemExit(status or multicast_status)

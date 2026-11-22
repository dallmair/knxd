"""Run the integration suite under pytest.

Automake runs this file as a single test, so `make test` reports one result for the whole suite.

Tests marked `multicast` use fixed groups and ports and may reach the LAN, so they only run
in a network namespace, or on the host when KNXD_MULTICAST=host. See test/README.md.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

NO_TESTS_COLLECTED = 5
HERE = Path(__file__).resolve().parent


def _pytest_cmd(*args: str) -> list[str]:
    return [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(HERE / "integration"), *args]


def _status(cmd: list[str]) -> int:
    status = subprocess.call(cmd)
    return 0 if status == NO_TESTS_COLLECTED else status


def _multicast_pass() -> int:
    mode = os.environ.get("KNXD_MULTICAST", "auto")
    return _status(["sh", str(HERE / "netns-run.sh"), mode, *_pytest_cmd("-m", "multicast")])


if __name__ == "__main__":
    args = ["-m", "not multicast"]
    if importlib.util.find_spec("xdist") is not None:
        args = ["-n", "auto", *args]
    parallel = _status(_pytest_cmd(*args))
    # Multicast tests share fixed groups and ports, so they run after the parallel part, one at a time.
    raise SystemExit(parallel or _multicast_pass())

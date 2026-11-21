"""Run the integration suite under pytest.

Automake runs this file as a single test, so `make test` reports one result for the whole suite.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

NO_TESTS_COLLECTED = 5


def _pytest(*args: str) -> int:
    suite = Path(__file__).resolve().parent / "integration"
    status = subprocess.call([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(suite), *args])
    return 0 if status == NO_TESTS_COLLECTED else status


if __name__ == "__main__":
    if importlib.util.find_spec("xdist") is None:
        raise SystemExit(_pytest())
    # Multicast tests share fixed groups and ports, so they run after the parallel part, one at a time.
    parallel = _pytest("-n", "auto", "-m", "not multicast")
    serial = _pytest("-m", "multicast")
    raise SystemExit(parallel or serial)

"""Run the integration suite under pytest.

Automake runs this file as a single test, so `make test` reports one result for the whole suite.

Tests marked `multicast` use fixed groups and ports and may reach the LAN, so they only run
in a network namespace, or on the host network in `host` mode. KNXD_MULTICAST lists the
modes to try (scripts in netns/); the first whose `--test` passes runs the multicast pass.
See test/README.md.
"""

import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path

NO_TESTS_COLLECTED = 5
HERE = Path(__file__).resolve().parent
NETNS_DIR = HERE / "netns"


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


def _multicast_parallel_cmd(script: str, workers: int) -> list[str]:
    # Each xdist worker starts through the mode script, so it gets a network namespace of its own.
    worker = shlex.join(["sh", script, sys.executable])
    return _pytest_cmd("-m", "multicast", "--dist", "load", "--tx", f"{workers}*popen//python={worker}")


def _modes() -> dict[str, Path]:
    """Map mode names to scripts: `10-netns.sh` is `netns`. Sorted, so `auto` tries low numbers first."""
    modes = {}
    for path in sorted(NETNS_DIR.glob("*.sh")):
        prefix, _, name = path.stem.partition("-")
        modes[name if prefix.isdigit() and name else path.stem] = path
    return modes


def _candidates(spec: str, modes: dict[str, Path]) -> list[str]:
    """Expand a spec like `auto,!sudo` or `netns,docker`; `auto` means every numbered script."""
    auto = [name for name, path in modes.items() if path.stem[0].isdigit()]
    wanted: list[str] = []
    excluded: set[str] = set()
    for token in filter(None, (t.strip() for t in spec.split(","))):
        name = token.removeprefix("!")
        if name != "auto" and name not in modes:
            raise SystemExit(f"error: KNXD_MULTICAST={spec}: unknown mode {name!r}; known: auto, {', '.join(modes)}")
        names = auto if name == "auto" else [name]
        if token.startswith("!"):
            excluded.update(names)
        else:
            wanted.extend(names)
    return [name for name in dict.fromkeys(wanted) if name not in excluded]


def _select(spec: str) -> tuple[str, str, bool]:
    """Return the first usable mode, its script, and whether its `--test` printed `parallel`."""
    modes = _modes()
    for name in _candidates(spec, modes):
        probe = subprocess.run(["sh", str(modes[name]), "--test"], stdout=subprocess.PIPE, text=True, check=False)
        if probe.returncode == 0:
            parallel = probe.stdout.strip() == "parallel"
            print(f"multicast tests: mode {name}, {'parallel' if parallel else 'serial'}", file=sys.stderr)
            return name, str(modes[name]), parallel
    raise SystemExit(f"error: KNXD_MULTICAST={spec}: none of these modes is usable")


if __name__ == "__main__":
    mode, script, parallel = _select(os.environ.get("KNXD_MULTICAST", "auto"))
    os.environ["KNXD_MULTICAST"] = mode
    # The tests trust this variable, so never inherit it.
    os.environ.pop("KNXD_NETNS_ACTIVE", None)
    xdist = importlib.util.find_spec("xdist") is not None
    others = _pytest_cmd(*(["-n", "auto"] if xdist else []), "-m", "not multicast")
    if not parallel or not xdist:
        # Without namespaces per worker, multicast tests run after the others, one at a time.
        status = _status(others)
        raise SystemExit(status or _status(["sh", script, *_pytest_cmd("-m", "multicast")]))
    workers = min(_multicast_count(), os.cpu_count() or 1)
    if workers == 0:
        raise SystemExit(_status(others))
    # The groups run one after the other so their output does not interleave in the log.
    status = _status(others)
    multicast_status = _status(_multicast_parallel_cmd(script, workers))
    raise SystemExit(status or multicast_status)

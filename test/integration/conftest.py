"""Fixtures shared by the knxd integration tests.

knxd and knxtool run from the build tree. Automake sets KNXD_BUILD_ROOT for
`make test`; running pytest by hand falls back to the source tree.

All tests are async and run on anyio's asyncio backend (`anyio_mode = auto` in
test/pyproject.toml). Never block the event loop in a test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import anyio
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Generator, Sequence

    from anyio.abc import Process
    from xdist.workermanage import WorkerController

DEFAULT_TIMEOUT = 10.0
STOP_GRACE = 5.0
POLL_INTERVAL = 0.05
# knxd logs this at error level 6 (info) once a listener with port = 0 got its port from the kernel.
LISTEN_LINE = re.compile(rb"Server '(\S+)' is listening for [^\n]+? on (?:UDP|TCP) port (\d+)\n")
# SO_ACCEPTCON bit in the Flags column of /proc/net/unix (Linux).
SO_ACCEPTCON = 0x10000


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "multicast: uses fixed multicast groups or ports; runs in parallel only with a network namespace per worker",
    )


def _netns() -> str:
    return str(Path("/proc/self/ns/net").readlink())


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: WorkerController) -> None:
    node.workerinput["knxd_controller_netns"] = _netns()


def pytest_report_header(config: pytest.Config) -> str | None:
    if os.environ.get("KNXD_MULTICAST") == "host":
        return "WARNING: KNXD_MULTICAST=host, multicast tests use the host network and may reach the LAN"
    return None


def _multicast_isolated() -> bool:
    return os.environ.get("KNXD_NETNS_ACTIVE") == "1" or os.environ.get("KNXD_MULTICAST") == "host"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    workerinput = getattr(config, "workerinput", None)
    own_netns = workerinput is not None and workerinput.get("knxd_controller_netns") != _netns()
    shared = not own_netns and int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1")) > 1
    no_netns = pytest.mark.skip(
        reason="multicast tests need a network namespace: run via run_integration.py with "
        "KNXD_MULTICAST=netns or sudo, or KNXD_MULTICAST=host to use the host network",
    )
    no_parallel = pytest.mark.skip(
        reason="parallel multicast tests need a network namespace per worker; use run_integration.py or drop -n",
    )
    for item in items:
        if not item.get_closest_marker("multicast"):
            continue
        if not _multicast_isolated():
            item.add_marker(no_netns)
        elif shared:
            item.add_marker(no_parallel)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def knxd_env() -> dict[str, str]:
    root = Path(os.environ.get("KNXD_BUILD_ROOT", Path(__file__).resolve().parents[2]))
    if not (root / "src" / "server").is_dir():
        pytest.fail(f"knxd build tree not found at {root}; run make first")
    tool_dirs = [
        root / "src/tools/.libs",
        root / "src/tools",
        root / "src/server/.libs",
        root / "src/server",
    ]
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([*map(str, tool_dirs), os.environ.get("PATH", "")])
    lib_dirs = [str(root / "src/client/c/.libs"), os.environ.get("LD_LIBRARY_PATH", "")]
    env["LD_LIBRARY_PATH"] = os.pathsep.join(d for d in lib_dirs if d)
    return env


@pytest.fixture(scope="session")
def tool(knxd_env: dict[str, str]) -> Callable[[str], str]:
    def find(name: str) -> str:
        path = shutil.which(name, path=knxd_env["PATH"])
        if path is None:
            pytest.fail(f"{name} not found in the knxd build tree; run make first")
        return path

    return find


def _bound_ports(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return {m[1].decode(): int(m[2]) for m in LISTEN_LINE.finditer(path.read_bytes())}


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return path.read_bytes().count(b"\n")


def _listening(path: Path) -> bool:
    # A probe connection would take a client address from knxd, so ask the kernel instead.
    try:
        rows = Path("/proc/net/unix").read_text().splitlines()[1:]
    except OSError:
        return False
    for row in rows:
        fields = row.split(maxsplit=7)
        if len(fields) == 8 and fields[7] == str(path) and int(fields[3], 16) & SO_ACCEPTCON:
            return True
    return False


async def _wait_until(check: Callable[[], bool], what: str) -> None:
    with anyio.move_on_after(DEFAULT_TIMEOUT):
        # Polls files and /proc, which offer no event to wait on.
        while not check():  # noqa: ASYNC110
            await anyio.sleep(POLL_INTERVAL)
        return
    pytest.fail(f"timed out after {DEFAULT_TIMEOUT}s waiting for {what}")


def _tail(path: Path, lines: int = 20) -> str:
    if not path.exists():
        return "(no output)"
    text = path.read_bytes().decode("utf-8", "replace").splitlines()
    return "\n".join(text[-lines:]) or "(empty)"


def _open_output(out_path: Path, err_path: Path, append: bool = False) -> tuple[IO[bytes], IO[bytes]]:
    return out_path.open("ab" if append else "wb"), err_path.open("wb")


async def _stop(proc: Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    with anyio.move_on_after(STOP_GRACE):
        await proc.wait()
        return
    proc.kill()
    await proc.wait()


class Processes:
    """Starts knxd and knxtool processes for one test and stops them afterwards."""

    def __init__(self, workdir: Path, env: dict[str, str]) -> None:
        self.workdir = workdir
        self.env = env
        self._running: dict[str, Process] = {}
        self._names: list[str] = []

    def _paths(self, name: str) -> tuple[Path, Path]:
        return self.workdir / f"{name}.out", self.workdir / f"{name}.err"

    async def spawn(self, name: str, argv: Sequence[str], *, stdout: Path | None = None) -> None:
        out_path, err_path = self._paths(name)
        self._names.append(name)
        out, err = _open_output(stdout or out_path, err_path)
        with out, err:
            self._running[name] = await anyio.open_process(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=self.workdir,
                env=self.env,
            )

    async def run(
        self,
        name: str,
        argv: Sequence[str],
        *,
        stdout: Path | None = None,
        append: bool = False,
    ) -> int:
        """Run a command to completion and return its exit status."""
        out_path, err_path = self._paths(name)
        self._names.append(name)
        out, err = _open_output(stdout or out_path, err_path, append)
        with out, err, anyio.move_on_after(DEFAULT_TIMEOUT):
            result = await anyio.run_process(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=self.workdir,
                env=self.env,
                check=False,
            )
            return result.returncode
        pytest.fail(f"{name} did not finish within {DEFAULT_TIMEOUT}s")

    def stderr_tail(self, name: str) -> str:
        return _tail(self._paths(name)[1])

    async def stop(self, name: str) -> None:
        proc = self._running.pop(name, None)
        if proc is not None:
            await _stop(proc)

    async def stop_all(self) -> None:
        while self._running:
            await _stop(self._running.popitem()[1])

    async def wait_for_lines(self, path: Path, count: int) -> None:
        await _wait_until(lambda: _count_lines(path) >= count, f"{path.name} to have {count} line(s)")

    async def wait_for_socket(self, path: Path) -> None:
        await _wait_until(lambda: _listening(path), f"{path.name} to listen")

    async def wait_for_ports(self, name: str, sections: Sequence[str]) -> dict[str, int]:
        """Return the ports that process `name` bound for the given config sections, by section name."""
        err = self._paths(name)[1]
        await _wait_until(
            lambda: set(sections) <= _bound_ports(err).keys(), f"{name} to report ports of {', '.join(sections)}"
        )
        return _bound_ports(err)

    def report_output(self) -> None:
        for name in self._names:
            print(f"--- {name} stderr ---", file=sys.stderr)
            print(self.stderr_tail(name), file=sys.stderr)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, Any, None]:
    outcome = yield
    setattr(item, f"rep_{call.when}", outcome.get_result())


@pytest.fixture
async def procs(request: pytest.FixtureRequest, tmp_path: Path, knxd_env: dict[str, str]) -> AsyncIterator[Processes]:
    processes = Processes(tmp_path, knxd_env)
    try:
        yield processes
    finally:
        await processes.stop_all()
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        processes.report_output()

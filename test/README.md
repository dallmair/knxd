# knxd tests

This suite contains **integration tests only**. Each scenario starts a real
`knxd` and checks its observable behaviour from outside, over its sockets.

Tests marked `@pytest.mark.deviation` pin behaviour that differs from the KNX
specification or is a known bug (for example the address leak after a refused
UDP connect). When such a bug is fixed, update the test and drop the marker.
Run only those tests with `-m deviation`, or leave them out with
`-m "not deviation"`.

## Layout

| Path | Contents |
|------|----------|
| `run_integration.py` | Entry point that `make test` runs. It starts pytest on `integration/`. |
| `integration/conftest.py` | Shared fixtures: build-tree lookup, process lifecycle, readiness checks, ports that knxd bound for `port = 0`. |
| `integration/tunnelling/` | KNXnet/IP tunnelling and management connections over UDP, TCP and Unix sockets, driven by a small Python KNXnet/IP client. |

## Coverage

Status of the test suite:

| Area | Scenario | Status |
|------|----------|--------|
| CLI validation (invalid and valid option combinations) | | Planned |
| Group cache, bus monitor, group listener over multicast and tunnel | | Planned |
| TP-UART driver: reset/state handshake, receive, transmit and confirm | | Planned |
| NCN5120 driver | | Planned |
| FT1.2 driver | | Planned |
| Protocol codecs and boundaries | | Planned |
| Backend protocol parsing and state transitions | | Planned |
| Address allocation and filtering | | Planned |
| USB ID/protocol handling (no enumeration) | | Planned |
| NAT translation | | Planned |
| Queue ordering, backpressure, pacing | | Planned |
| Group-cache expiry | | Planned |
| KNXnet/IP routing | | Planned |
| KNXnet/IP tunnelling over UDP (`ets_router`), TCP (`tcptunsrv`) and Unix sockets (`unixtunsrv`) | `integration/tunnelling/` | Covered |
| knxd client wire protocol (C client library, `knxtool`) | | Planned |
| CGI read/write invocation and responses | | Planned |
| Process lifecycle | | Planned |
| Hardware-in-the-loop serial/USB checks (opt-in) | | Planned |

Likely gaps that the planned scenarios should address:

- Malformed protocol and frame handling.
- Address allocation, reconnect, and error recovery.
- Client lifecycle and resource cleanup.
- Feature-disabled build configurations (scenarios must skip, not pass).

## KNXnet/IP tunnelling tests

`integration/tunnelling/` talks to knxd only through its sockets. One knxd per
test serves the UDP (`ets_router`), TCP (`tcptunsrv`) and Unix socket
(`unixtunsrv`) servers from one pool of client addresses, with a `dummy:`
backend. `knxnet_wire.py` and `knxnet_transport.py` are an independent
KNXnet/IP client, written from the KNX spec (03_08_02, 03_08_04), so they
share no code with knxd. `tunnel_session.py` hides the one real transport
difference: UDP has ACKs and sequence counters, TCP and Unix do not.

| File | Covers |
|------|--------|
| `test_connect.py`, `test_connect_busmon_mgmt.py`, `test_connect_errors.py` | CONNECT_REQUEST: addresses, data endpoint, refusals, busmonitor and management channels |
| `test_lifecycle.py` | CONNECTIONSTATE, DISCONNECT, heartbeat timeout, address release |
| `test_data_exchange.py` | telegrams between tunnels, L_Data.con, busmonitor |
| `test_management.py` | M_PropRead on management connections |
| `test_discovery.py` | SEARCH and DESCRIPTION; tests with discovery enabled are marked `multicast` |
| `test_udp_sequencing.py`, `test_udp_retransmission.py`, `test_udp_refusals.py` | UDP sequence counters, ACKs, retransmission, refusals in ACK status |
| `test_stream_protocol.py`, `test_stream_framing.py` | TCP/Unix: no ACKs, stream framing, malformed headers |
| `test_concurrency.py` | many clients on all three servers sending at once: delivery, order, L_Data.con |

The suite waits for real protocol timeouts (1 s retransmission, 2 s heartbeat).

### Code coverage

    pip install gcovr
    test/coverage.sh              # report on all of src/
    COVERAGE_HTML=1 test/coverage.sh

`coverage.sh` builds an instrumented copy of the tree in `/tmp/knxd-coverage`
(the normal build is left alone, set `COVERAGE_DIR` to change the place),
runs the whole integration suite against it through `run_integration.py`, like
`make test` (so `KNXD_MULTICAST` applies), and prints a per-file line report
for `src/`. Extra arguments go to pytest, for example
`test/coverage.sh -k tunnelling` to run only some tests. Set `COVERAGE_FILES`
to a regex to restrict the report to some files.

The first run configures and builds the copy (a few minutes); later runs sync
the changed sources and rebuild only those. Set `COVERAGE_REBUILD=1` to start
from scratch.

## Prerequisites

- A normal knxd build: a C/C++ toolchain, autotools, and the knxd build
  dependencies (see the top-level `README.md`).
- Python 3 with the `pytest` module (listed in `pyproject.toml`). `configure`
  warns if it is missing. `make test` then fails with a message, so a missing
  test runner is never reported as a pass.
- Linux. The readiness check reads `/proc/net/unix`.
- No KNX hardware. The default suite uses loopback and the `dummy:` backend.

Set up a virtual environment once (Debian/Ubuntu needs `python3-venv` for this;
Alpine ships it with `python3`):

    python3 -m venv .venv
    . .venv/bin/activate
    pip install 'test/[xdist]'

The `xdist` extra (pytest-xdist) is optional; omit it to run tests serially.

To work on the test code, also install the optional `dev` extra (ruff, ty) for
linting and type checks:

    pip install 'test/[xdist,dev]'
    ruff check test/
    ruff format --check test/
    ty check --python .venv test/

Activate the venv before running `./configure`, because `configure` picks the
first `python3` on `PATH`. To use a specific interpreter without activating,
run `PYTHON=$PWD/.venv/bin/python ./configure` instead. `.venv/` is
git-ignored.

## Running

Run the default suite (what CI runs):

    ./configure
    make
    make test

`make test` runs `make -C test check`, which runs `run_integration.py`. That
starts pytest, so each test is listed separately in its output, and the
Automake result reflects the whole suite.

Run selected tests from the top of the build tree. Set `KNXD_BUILD_ROOT`, or
run from the source tree, so the tests find the built `knxd` and `knxtool`:

    KNXD_BUILD_ROOT=$PWD python3 -m pytest test/integration/tunnelling

Use `-k` to select one test, or `-n auto` (pytest-xdist) to run in parallel.
When pytest-xdist is installed, `make test` runs the suite in parallel too.
Knxd listeners use `port = 0`, so parallel tests never share a port.

Tests marked `@pytest.mark.multicast` use fixed multicast groups and ports, so
they are skipped in plain `-n` runs. `make test` runs them in a separate pass
after the other tests, so the output of the two passes does not interleave.
In that pass every xdist worker has its own network namespace, so the multicast
tests run in parallel with each other and cannot send telegrams onto a real
KNX/IP network. In `host` mode, or without pytest-xdist, they run one at a
time.

### Multicast tests

`KNXD_MULTICAST` selects where that pass runs. Set it for `make test`, or for
`python3 test/run_integration.py`:

    make test KNXD_MULTICAST=sudo

Each mode is a script in `test/netns/`. `KNXD_MULTICAST` is a comma-separated
list of modes. At the start of the run, `run_integration.py` calls each listed
script with `--test`, in order, and uses the first that succeeds. If none does,
the run fails. `auto` stands for every script with a number prefix, lowest
number first. `!mode` removes a mode from the list. Examples:

| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Tries `netns`, then `sudo`, then `host`, then `true`. |
| `auto,!sudo` | As `auto`, but never runs sudo. For CI where sudo aborts the run. |
| `auto,!host` | As `auto`, but multicast never goes onto the host network. |
| `netns` | Fails unless unprivileged namespaces work. For CI that must support them. |

| Mode | Script | Behaviour |
|------|--------|-----------|
| `netns` | `10-netns.sh` | Unprivileged namespace (`unshare --user --map-root-user --net`). |
| `sudo` | `50-sudo.sh` | Creates the namespace with `sudo unshare --net`, sets up `lo`, then drops back to your user for pytest. Only the setup runs as root, and the namespace vanishes with the process. sudo runs with `-n`, so it never prompts: it works as root, with a `NOPASSWD` rule, or after `sudo -v` has cached your credentials. |
| `host` | `90-host.sh` | No isolation: telegrams may reach a KNX/IP router on the LAN. `--test` checks that the multicast route uses an interface with the `MULTICAST` flag. |
| `true` | `99-true.sh` | Last resort: the multicast tests do not run. |

A new mode (for example Docker, or a jail on *BSD) is a new script: with
`--test` it exits 0 if it can work here, and prints `parallel` if every call
gets an isolated network of its own (so each xdist worker can use one), or
`serial` otherwise. Without `--test` it runs its arguments as a command in an
isolated network with multicast routed over `lo`, and sets
`KNXD_NETNS_ACTIVE=1`. Nothing may write to stdout before the command starts.

Unprivileged namespaces are blocked by AppArmor on Ubuntu 24.04+
(`kernel.apparmor_restrict_unprivileged_userns`), by `kernel.unprivileged_userns_clone=0`
on older Debian, by `user.max_user_namespaces=0` on RHEL, and by Docker's default
seccomp profile. Use `sudo` or `host` there. `sudo` needs `unshare`, `setpriv` and `ip`.

The scripts can be used by hand, for example
`test/netns/50-sudo.sh python3 -m pytest -m multicast -k group`.

Running `pytest -m multicast` directly skips these tests. Only the launcher or
`KNXD_MULTICAST=host` enables them.

A test is reported as skipped when the build lacks a feature it needs, such as
the group cache.

Opt-in hardware tests (serial/USB) will run through a separate target that is
not part of `make test` or CI. That target is planned but not yet present.

## Maintenance rules

- Put new tests under the product area they exercise, as `test_*.py` files.
  Test files are picked up automatically; no Makefile entry is needed.
- Use the fixtures in `integration/conftest.py` for process start/stop,
  readiness, ports, and output. Do not add fixed sleeps without a comment that
  says why no observable signal exists.
- A test must skip (`pytest.skip`) when a required feature is disabled. It must
  not pass silently.

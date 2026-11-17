# Agent instructions for knxd

## Rules

- C++: follow [CodingStyle.md](CodingStyle.md).
- Commits: only when asked. Follow [SubmittingPatches.md](SubmittingPatches.md) and add `Signed-off-by`.
- Keep changes surgical. Never edit generated files (`configure`, `Makefile.in`, `config.h`, ...).

## Build and test

- Build: `sh bootstrap.sh && ./configure && make -j$(nproc)`
- Integration tests: `make test`. The venv must be on `PATH` when running `configure`; see [test/README.md](test/README.md).

## Python (`test/` only)

- Use the project venv `.venv/`. Install deps (pytest, ruff, ty) with `.venv/bin/pip install test/` (use `'test/[xdist]'` for parallel runs).
- Every Python file you touch under `test/` must pass all checks (ruff config: [test/pyproject.toml](test/pyproject.toml)):
  - `.venv/bin/ruff check <files>`
  - `.venv/bin/ruff format --check <files>`
  - `.venv/bin/ty check --python .venv <files>`
- Release resources in fixtures with `try:`/`finally:`.
- Tests are `async def` on anyio (asyncio backend, auto mode via [test/pyproject.toml](test/pyproject.toml)); never block the event loop (no `time.sleep`, `subprocess.run`, `select` or blocking sockets).
- Do not apply these rules to Python outside `test/` (`src/client/python/`, `src/examples/python/`).

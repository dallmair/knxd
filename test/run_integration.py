"""Run the integration suite under pytest.

Automake runs this file as a single test, so `make test` reports one result for the whole suite.
"""

from pathlib import Path

import pytest

if __name__ == "__main__":
    suite = Path(__file__).resolve().parent / "integration"
    raise SystemExit(pytest.main(["-p", "no:cacheprovider", str(suite)]))

# Integration tests for knxd, written as pytest tests under integration/.
# run_integration.py runs them as one Automake test.

TEST_EXTENSIONS = .py
PY_LOG_COMPILER = $(PYTHON)

if HAVE_INTEGRATION_TESTS

TESTS_ENVIRONMENT = KNXD_BUILD_ROOT='$(abs_top_builddir)' PYTHONDONTWRITEBYTECODE=1

TESTS = run_integration.py

else

check-local:
	@echo "error: 'make test' needs python3 with the pytest module (see test/README.md)" >&2
	@exit 1

endif

EXTRA_DIST = \
	README.md \
	pyproject.toml \
	run_integration.py \
	netns-run.sh \
	coverage.sh \
	integration

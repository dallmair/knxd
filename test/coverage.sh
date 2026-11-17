#!/bin/sh
# Measure how much of knxd the black-box tests exercise.
#
# Usage: test/coverage.sh [pytest arguments]
#
# Builds an instrumented copy of the source tree in $COVERAGE_DIR (default
# /tmp/knxd-coverage), so the normal build tree is left alone, runs the
# integration tests against it, and prints a gcovr report. The first run configures
# and builds everything; later runs copy over the changed sources and rebuild only those.
# Set COVERAGE_REBUILD=1 to start from scratch.
#
# Needs the normal build prerequisites plus gcovr (pip install gcovr).
# Set COVERAGE_FILES to a regex to restrict the report to some source files.
# Set COVERAGE_HTML=1 to also write $COVERAGE_DIR/report/index.html.

set -eu

src=$(cd "$(dirname "$0")/.." && pwd)
dir=${COVERAGE_DIR:-/tmp/knxd-coverage}
files=${COVERAGE_FILES:-'src/.*'}
python=${PYTHON:-python3}

if ! "$python" -m gcovr --version >/dev/null 2>&1; then
    echo "error: gcovr not found for $python (pip install gcovr)" >&2
    exit 1
fi

mkdir -p "$dir"
if [ ! -f "$dir/config.status" ] || [ -n "${COVERAGE_REBUILD:-}" ]; then
    # First run: copy everything that is needed to configure (generated files such as
    # configure included), but no build outputs, virtualenv or git data.
    rm -rf "$dir"
    mkdir -p "$dir"
    (cd "$src" && tar --exclude=./.venv --exclude=./.git --exclude='*.o' --exclude='*.lo' \
        --exclude='*.la' --exclude='*.a' --exclude=.libs --exclude=.deps --exclude=autom4te.cache \
        --exclude=__pycache__ --exclude='*.gcda' --exclude='*.gcno' \
        --exclude=./src/server/knxd --exclude=./src/server/knxd_args --exclude=./src/tools/knxtool \
        -cf - .) | (cd "$dir" && tar -xf -)
    (cd "$dir" && ./configure CFLAGS='-O0 -g --coverage' \
        CXXFLAGS='-O0 -g --coverage -std=c++0x -Wno-subobject-linkage' LDFLAGS=--coverage >configure.log 2>&1)
else
    # Later runs: bring over the current sources only, so build outputs in $dir stay
    # (and are not replaced by the uninstrumented ones from $src).
    (cd "$src" && git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf -) |
        (cd "$dir" && tar -xf -)
fi
# The copy has no .git, which configure.ac needs for the version, so never re-run autotools in it.
(cd "$dir" && make AUTOMAKE=: ACLOCAL=: AUTOCONF=: AUTOHEADER=: -j"$(nproc)" >build.log 2>&1) || { echo "error: build failed, see $dir/build.log" >&2; exit 1; }
cd "$dir"

# Counters from earlier runs would be added to this one.
find . -name '*.gcda' -delete

# --rootdir keeps test/integration/conftest.py (shared fixtures) in scope wherever this is run from.
KNXD_BUILD_ROOT="$dir" "$python" -m pytest -p no:cacheprovider --rootdir="$src/test" \
    "$src/test/integration" "$@"

"$python" -m gcovr -r . --filter "$files" --txt
if [ -n "${COVERAGE_HTML:-}" ]; then
    mkdir -p report
    "$python" -m gcovr -r . --filter "$files" --html-details report/index.html
    echo "HTML report: $dir/report/index.html"
fi

#!/bin/sh
# Multicast mode "true": last resort of "auto", the multicast tests do not run.
# Usage: 99-true.sh --test | COMMAND...
[ "${1-}" = --test ] && { echo serial; exit 0; }
echo "multicast tests: not run, no isolated network is available" >&2

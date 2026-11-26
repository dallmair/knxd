#!/bin/sh
# Multicast mode "netns": unprivileged network namespace.
# Usage: 10-netns.sh --test | COMMAND...
set -eu

if [ "${1-}" = --test ]; then
    command -v unshare >/dev/null 2>&1 && command -v ip >/dev/null 2>&1 &&
        unshare --user --map-root-user --net -- true 2>/dev/null && { echo parallel; exit 0; }
    echo "netns: unprivileged network namespaces are not available" >&2
    exit 2
fi

# knxd reads the preferred source address (RTA_PREFSRC) from the multicast route.
# stdout stays clean: xdist workers talk to the controller over it.
setup='{ ip link set lo up && ip link set lo multicast on && ip route add 224.0.0.0/4 dev lo src 127.0.0.1; } >&2'
export KNXD_NETNS_ACTIVE=1
exec unshare --user --map-root-user --net -- sh -c "$setup && exec \"\$@\"" sh "$@"

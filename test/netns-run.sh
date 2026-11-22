#!/bin/sh
# Run a command in an environment where multicast tests may run.
# Usage: netns-run.sh MODE COMMAND...
# MODE is auto, netns, sudo or host; see test/README.md.
set -eu

[ $# -ge 2 ] || { echo "usage: $0 auto|netns|sudo|host COMMAND..." >&2; exit 2; }
mode=$1
shift

# The tests trust this variable, so never inherit it.
unset KNXD_NETNS_ACTIVE
export KNXD_MULTICAST=$mode

# knxd reads the preferred source address (RTA_PREFSRC) from the multicast route.
# stdout stays clean: xdist workers talk to the controller over it.
setup='{ ip link set lo up && ip link set lo multicast on && ip route add 224.0.0.0/4 dev lo src 127.0.0.1; } >&2'

need() {
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 || { echo "error: KNXD_MULTICAST=$mode needs $tool" >&2; exit 2; }
    done
}

case $mode in
host)
    echo "warning: KNXD_MULTICAST=host: multicast tests run on the host network and may reach the LAN" >&2
    exec "$@"
    ;;

auto | netns)
    if command -v unshare >/dev/null 2>&1 && command -v ip >/dev/null 2>&1 &&
        unshare --user --map-root-user --net -- true 2>/dev/null; then
        echo "multicast tests: unprivileged network namespace" >&2
        export KNXD_NETNS_ACTIVE=1
        exec unshare --user --map-root-user --net -- sh -c "$setup && exec \"\$@\"" sh "$@"
    fi
    if [ "$mode" = netns ]; then
        echo "error: KNXD_MULTICAST=netns, but unprivileged network namespaces are not available" >&2
        exit 2
    fi
    echo "multicast tests: no network namespace available, they will be skipped" >&2
    exec "$@"
    ;;

sudo)
    need unshare setpriv ip
    echo "multicast tests: sudo network namespace" >&2
    export KNXD_NETNS_ACTIVE=1
    if [ "$(id -u)" -eq 0 ]; then
        exec unshare --net -- sh -c "$setup && exec \"\$@\"" sh "$@"
    fi
    need sudo
    # Root only creates the namespace; the command runs as the invoking user again.
    exec sudo -E unshare --net -- sh -c \
        "$setup && uid=\$1 && gid=\$2 && shift 2 && exec setpriv --reuid=\"\$uid\" --regid=\"\$gid\" --init-groups -- \"\$@\"" \
        sh "$(id -u)" "$(id -g)" "$@"
    ;;

*)
    echo "error: KNXD_MULTICAST='$mode'; use one of auto, netns, sudo, host" >&2
    exit 2
    ;;
esac

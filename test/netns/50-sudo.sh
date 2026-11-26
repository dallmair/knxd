#!/bin/sh
# Multicast mode "sudo": root creates the network namespace, the command runs as the invoking user.
# Usage: 50-sudo.sh --test | COMMAND...
set -eu

[ "$(id -u)" -eq 0 ] && sudo= || sudo='sudo -n -E'

if [ "${1-}" = --test ]; then
    for tool in unshare setpriv ip ${sudo:+sudo}; do
        command -v "$tool" >/dev/null 2>&1 || { echo "sudo: $tool not found" >&2; exit 2; }
    done
    # -n fails instead of prompting; 'sudo -v' beforehand caches the credentials.
    $sudo unshare --net -- true 2>/dev/null && { echo parallel; exit 0; }
    echo "sudo: cannot create a network namespace without a password prompt; run 'sudo -v' first" >&2
    exit 2
fi

# knxd reads the preferred source address (RTA_PREFSRC) from the multicast route.
# stdout stays clean: xdist workers talk to the controller over it.
setup='{ ip link set lo up && ip link set lo multicast on && ip route add 224.0.0.0/4 dev lo src 127.0.0.1; } >&2'
export KNXD_NETNS_ACTIVE=1
exec $sudo unshare --net -- sh -c \
    "$setup && uid=\$1 && gid=\$2 && shift 2 && exec setpriv --reuid=\"\$uid\" --regid=\"\$gid\" --init-groups -- \"\$@\"" \
    sh "$(id -u)" "$(id -g)" "$@"

#!/bin/sh
# Multicast mode "host": no isolation, tests may reach the LAN.
# Usage: 90-host.sh --test | COMMAND...
set -eu

if [ "${1-}" = --test ]; then
    # A default route also covers multicast, so ask the kernel instead of looking for 224.0.0.0/4.
    dev=$(ip -4 route get 224.0.23.12 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')
    if [ -z "$dev" ]; then
        echo "host: no IPv4 route for multicast (224.0.23.12)" >&2
        exit 2
    fi
    ip link show "$dev" | grep -q '<[^>]*MULTICAST' && { echo serial; exit 0; }
    echo "host: interface $dev, used for multicast, has no MULTICAST flag" >&2
    exit 2
fi

unset KNXD_NETNS_ACTIVE
export KNXD_MULTICAST=host
exec "$@"

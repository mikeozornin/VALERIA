#!/usr/bin/env bash
set -euo pipefail

# Production processes must belong to the service cgroup. A detached copy would
# bypass cleanup on a web-server crash.
action="${1:-start}"
case "$action" in
  start|stop|restart|status) ;;
  *) echo "usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac

for unit in valeria-web.service; do
  if [[ "$(systemctl show "$unit" --property=LoadState --value)" != loaded ]]; then
    echo "Install the Valeria systemd units first: deploy/install-systemd.sh" >&2
    exit 1
  fi
done

exec systemctl "$action" valeria-web.service

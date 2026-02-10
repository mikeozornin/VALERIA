#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$EUID" != 0 || "$PWD" != /opt/valeria ]]; then
  echo "Run as root from the deployment at /opt/valeria" >&2
  exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
  echo "systemd must be running as PID 1" >&2
  exit 1
fi

# Installation must not silently create a parallel copy of the legacy server.
python3 deploy/check-legacy-processes.py web
systemd-analyze verify deploy/systemd/valeria-web.service
backup_dir="$(mktemp -d "$PWD/data/systemd-backup-XXXXXXXX")"
for unit in valeria-web.service; do
  if [[ -e "/etc/systemd/system/$unit" || -L "/etc/systemd/system/$unit" ]]; then
    cp -a "/etc/systemd/system/$unit" "$backup_dir/"
  fi
  install -m 0644 "deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
echo "Units installed; backup: $backup_dir"
echo "Start: ./run-local.sh start"
echo "Enable after reboot: systemctl enable valeria-web.service"

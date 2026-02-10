#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-}" in
  web) ;;
  *) echo "usage: $0 web" >&2; exit 2 ;;
esac

# Preserve the existing shell-based .env expansion and CA configuration.
python3 deploy/check-legacy-processes.py "$1"
set -a
# shellcheck disable=SC1091
. ./.env
set +a

export SSL_CERT_FILE="${SSL_CERT_FILE:-$PWD/.secrets/ca/ca-certificates.crt}"
export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-$SSL_CERT_FILE}"
export NODE_EXTRA_CA_CERTS="${NODE_EXTRA_CA_CERTS:-$SSL_CERT_FILE}"
export GIT_SSL_CAINFO="${GIT_SSL_CAINFO:-$SSL_CERT_FILE}"
mkdir -p data/mcp/memory

exec .venv/bin/opencode-web >> data/opencode-web.log 2>&1

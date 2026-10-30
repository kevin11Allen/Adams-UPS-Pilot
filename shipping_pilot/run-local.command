#!/bin/sh
set -eu
cd "$(dirname "$0")"
export UPS_CONFIG_PATH="${UPS_CONFIG_PATH:-../../../private/ups/config.json}"
if [ -z "${UPS_CA_BUNDLE:-}" ] && [ -f /etc/ssl/cert.pem ]; then
  export UPS_CA_BUNDLE=/etc/ssl/cert.pem
fi
exec python3 server.py --port 8765

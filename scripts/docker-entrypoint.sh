#!/bin/sh
set -eu

mode="${AGENT_DECK_HARDWARE_MODE:-fake}"
port="${AGENT_DECK_PORT:-8765}"
host="${AGENT_DECK_HOST:-0.0.0.0}"
config="${AGENT_DECK_CONFIG:-/app/agent-deck.toml}"

mkdir -p /data/home /data/state /data/logs

set -- agent-deckd \
  --host "$host" \
  --port "$port" \
  --config "$config" \
  --log-file /data/logs/agent-deckd.log

case "$mode" in
  fake|disabled)
    set -- "$@" --disable-hardware-renderer
    ;;
  streamdock)
    ;;
  *)
    echo "Unsupported AGENT_DECK_HARDWARE_MODE: $mode (use fake or streamdock)" >&2
    exit 2
    ;;
esac

exec "$@"

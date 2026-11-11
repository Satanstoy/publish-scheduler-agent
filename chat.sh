#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -z "${OPENCODE_CLI_PATH:-}" ]; then
  for candidate in "$HOME/Library/Application Support/ai.opencode.desktop/cli"/*/opencode-cli; do
    if [ -x "$candidate" ]; then
      OPENCODE_CLI_PATH="$candidate"
    fi
  done
  export OPENCODE_CLI_PATH
fi

STATE_DIR="${STATE_DIR:-.runs/chat-demo}"
args=(python3 run.py chat --data-dir data --state-dir "$STATE_DIR")
if [ -n "${NOW:-}" ]; then
  args+=(--now "$NOW")
fi

exec "${args[@]}" "$@"

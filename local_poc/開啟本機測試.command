#!/bin/zsh
cd "${0:A:h}/.." || exit 1
PY="$HOME/codex/dunni/duanni-codex-dev/runtime/hermes/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo '找不到現有 Hermes 本機 Python 環境，請參照 local_poc/README.md。'
  exit 1
fi
"$PY" local_poc/server.py &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null' EXIT INT TERM
sleep 1
open http://127.0.0.1:9135/
wait "$server_pid"

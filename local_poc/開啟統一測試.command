#!/bin/zsh
set -e
cd "${0:A:h}/.." || exit 1
HERMES_PY="$HOME/codex/dunni/duanni-codex-dev/runtime/hermes/venv/bin/python"
PREVIEW_PY="/Users/weiting/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ ! -x "$HERMES_PY" || ! -x "$PREVIEW_PY" ]]; then
  echo '缺少本機 Hermes 或預覽 Python 環境。請看 local_poc/README.md。'
  exit 1
fi
export YUING_LOCAL_PORT=9141
export PORT=9142
export POC_LOCAL_BRIDGE_URL=http://127.0.0.1:9141
export POC_PREVIEW_USER=local-owner
export POC_PREVIEW_PASSWORD="$("$PREVIEW_PY" -c 'import secrets; print(secrets.token_urlsafe(18))')"
"$HERMES_PY" local_poc/server.py &
bridge_pid=$!
"$PREVIEW_PY" deploy_pwa/server.py &
preview_pid=$!
trap 'kill "$bridge_pid" "$preview_pid" 2>/dev/null' EXIT INT TERM
sleep 1
echo "預覽網址：http://127.0.0.1:9142/"
echo "帳號：$POC_PREVIEW_USER"
echo "本次密碼：$POC_PREVIEW_PASSWORD"
echo '此預覽只在 Mac 本機；關閉此視窗會停止服務。'
open http://127.0.0.1:9142/
wait "$preview_pid"

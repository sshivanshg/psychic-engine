#!/usr/bin/env bash
# TradeOS — start the WHOLE stack with one command.
#   • FastAPI backend  → http://127.0.0.1:8000   (uv run tradeos serve)
#   • SvelteKit dev    → http://127.0.0.1:5173   (npm run dev, with HMR)  ← open this one
# Ctrl-C stops both. Ports are freed first, so a stale server never blocks startup.
set -uo pipefail
cd "$(dirname "$0")"

free_port() {                       # kill whatever is listening on a port (quietly, if anything)
  local pids; pids=$(lsof -ti:"$1" 2>/dev/null || true)
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
}

pids=()
cleanup() {
  trap - EXIT INT TERM
  echo; echo "▸ stopping…"
  kill "${pids[@]}" 2>/dev/null || true
  free_port 8000; free_port 5173    # ensure vite/uvicorn (and any children) are gone
}
trap cleanup EXIT INT TERM

echo "▸ freeing ports 8000 (API) and 5173 (web)…"
free_port 8000; free_port 5173

if [ ! -d web/node_modules ]; then
  echo "▸ installing web deps (first run)…"
  (cd web && npm install)
fi

echo "▸ starting API  → http://127.0.0.1:8000"
uv run tradeos serve &
pids+=($!)

echo "▸ starting web  → http://127.0.0.1:5173   ← open this"
(cd web && npm run dev) &
pids+=($!)

echo "▸ both up. Ctrl-C to stop everything."
wait

#!/usr/bin/env bash
# Sarjy runner.
#
# Runs the service in the background and watches the git HEAD. When HEAD moves
# -- because you ran `git pull`, switched branch, or committed -- it reinstalls
# dependencies if requirements.txt changed and restarts the service.
#
#   ./run.sh              start (or restart) in the background
#   ./run.sh stop         stop the service and the watcher
#   ./run.sh status       what is running, on what commit
#   ./run.sh logs         follow the log
#   ./run.sh foreground   run without the watcher, output to the terminal
#
# It does not run `git pull` for you. Pulling is a decision; restarting after
# one is not.
set -uo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
POLL_SECONDS="${POLL_SECONDS:-5}"
LOG_DIR="logs"
LOG="$LOG_DIR/sarjy.log"
APP_PID="$LOG_DIR/app.pid"
WATCH_PID="$LOG_DIR/watch.pid"
MAX_LOG_BYTES=$((5 * 1024 * 1024))

mkdir -p "$LOG_DIR"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

rotate_if_large() {
  [ -f "$LOG" ] || return 0
  local size
  size=$(wc -c < "$LOG")
  if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
    mv "$LOG" "$LOG.1"
    log "log rotated at ${size} bytes"
  fi
}

head_sha()  { git rev-parse HEAD 2>/dev/null || echo none; }
reqs_hash() { md5sum requirements.txt 2>/dev/null | cut -d' ' -f1; }

running() {
  local f=$1
  [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null
}

start_app() {
  rotate_if_large
  # Port may still be held by a dying process; wait rather than race it.
  for _ in 1 2 3 4 5; do
    if command -v fuser >/dev/null && fuser "$PORT"/tcp >/dev/null 2>&1; then
      sleep 1
    else
      break
    fi
  done
  log "starting uvicorn on :$PORT  (commit $(head_sha | cut -c1-8))"
  nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
    >> "$LOG" 2>&1 &
  echo $! > "$APP_PID"

  # Confirm it actually came up, rather than reporting success on a crash.
  for _ in $(seq 1 30); do
    sleep 0.5
    if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
      log "healthy  http://localhost:$PORT"
      return 0
    fi
    running "$APP_PID" || { log "FAILED to start -- see $LOG"; return 1; }
  done
  log "started but /health did not answer in 15s -- see $LOG"
}

stop_app() {
  if running "$APP_PID"; then
    local pid; pid=$(cat "$APP_PID")
    log "stopping uvicorn (pid $pid)"
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -0 "$pid" 2>/dev/null && { log "force killing $pid"; kill -9 "$pid" 2>/dev/null; }
  fi
  rm -f "$APP_PID"
}

watch_loop() {
  local last_sha last_reqs sha
  last_sha=$(head_sha)
  last_reqs=$(reqs_hash)
  log "watching HEAD every ${POLL_SECONDS}s"

  while true; do
    sleep "$POLL_SECONDS"
    sha=$(head_sha)

    if [ "$sha" != "$last_sha" ]; then
      log "HEAD ${last_sha:0:8} -> ${sha:0:8}, restarting"
      if [ "$(reqs_hash)" != "$last_reqs" ]; then
        log "requirements.txt changed, installing"
        python3 -m pip install -q --user -r requirements.txt >> "$LOG" 2>&1 \
          && log "dependencies installed" \
          || log "dependency install FAILED -- starting anyway, see $LOG"
        last_reqs=$(reqs_hash)
      fi
      stop_app
      start_app
      last_sha=$sha
    fi

    # The service can die on its own; the watcher is also a supervisor.
    if ! running "$APP_PID"; then
      log "service is not running, restarting"
      start_app
    fi
  done
}

case "${1:-start}" in
  start|restart)
    running "$WATCH_PID" && { log "stopping previous watcher"; kill "$(cat "$WATCH_PID")" 2>/dev/null; rm -f "$WATCH_PID"; }
    stop_app
    start_app || exit 1
    nohup bash "$0" _watch >> /dev/null 2>&1 &
    echo $! > "$WATCH_PID"
    log "watcher started (pid $(cat "$WATCH_PID"))"
    echo
    echo "  http://localhost:$PORT"
    echo "  ./run.sh logs      follow the log"
    echo "  ./run.sh stop      stop everything"
    ;;
  _watch)   watch_loop ;;
  stop)
    running "$WATCH_PID" && { log "stopping watcher"; kill "$(cat "$WATCH_PID")" 2>/dev/null; }
    rm -f "$WATCH_PID"
    stop_app
    log "stopped"
    ;;
  status)
    running "$APP_PID"   && echo "  service  running (pid $(cat "$APP_PID")) on :$PORT" \
                         || echo "  service  not running"
    running "$WATCH_PID" && echo "  watcher  running (pid $(cat "$WATCH_PID"))" \
                         || echo "  watcher  not running"
    echo "  commit   $(head_sha | cut -c1-8)  $(git log -1 --format=%s 2>/dev/null)"
    curl -sf "http://localhost:$PORT/health" 2>/dev/null | python3 -m json.tool 2>/dev/null | sed 's/^/  /'
    ;;
  logs)     tail -f "$LOG" ;;
  foreground)
    exec python3 -m uvicorn app.main:app --reload --port "$PORT"
    ;;
  *)
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac

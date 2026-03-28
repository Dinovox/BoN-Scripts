#!/bin/bash
# Challenge 5 — Auto scheduler
# Round 1: 16:00–16:30 UTC
# Break:   16:30–17:00 UTC
# Round 2: 17:00–17:30 UTC

ADMIN="erd1cjjuju9sns6rrgrmndwcjmvpqkpruuqdvl00z7ruv8r3mwcu0v6qzjrm2q"
TARGET="erd1s0hzpn90yje34n4vth6vl0vlzd4uz7xz5y49mlak3dwacnkcfkwsl5nnhq"
DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS=10

ORCHESTRATOR_PID=""

now_utc() { date -u "+%H:%M:%S UTC"; }

seconds_until() {
  local target_hhmm="$1"  # e.g. "16:30"
  local today=$(date -u "+%Y-%m-%d")
  local target_epoch=$(date -u -j -f "%Y-%m-%d %H:%M:%S" "${today} ${target_hhmm}:00" "+%s" 2>/dev/null)
  local now_epoch=$(date -u "+%s")
  echo $((target_epoch - now_epoch))
}

start_agents() {
  echo "🟢 [$(now_utc)] Starting 10 agents..."
  cd "$DIR"
  npm run challenge5:multi -- --agents $AGENTS --admin "$ADMIN" --target "$TARGET" &
  ORCHESTRATOR_PID=$!
  echo "   PID: $ORCHESTRATOR_PID"
}

stop_agents() {
  if [ -n "$ORCHESTRATOR_PID" ] && kill -0 "$ORCHESTRATOR_PID" 2>/dev/null; then
    echo "🔴 [$(now_utc)] Stopping agents (PID $ORCHESTRATOR_PID)..."
    kill -INT "$ORCHESTRATOR_PID"
    sleep 3
    kill -TERM "$ORCHESTRATOR_PID" 2>/dev/null
    ORCHESTRATOR_PID=""
    echo "   Stopped."
  fi
}

sleep_until() {
  local target="$1"
  local secs=$(seconds_until "$target")
  if [ "$secs" -le 0 ]; then
    echo "⚠️  $target UTC already passed, skipping wait"
    return
  fi
  echo "⏳ [$(now_utc)] Waiting until $target UTC ($secs seconds)..."
  sleep "$secs"
}

trap 'echo ""; echo "🛑 Interrupted — stopping agents..."; stop_agents; exit 0' INT TERM

echo "══════════════════════════════════════════"
echo "  Challenge 5 — Auto Scheduler"
echo "══════════════════════════════════════════"
echo "  Round 1:  16:00 → 16:30 UTC"
echo "  Break:    16:30 → 17:00 UTC"
echo "  Round 2:  17:00 → 17:30 UTC"
echo "══════════════════════════════════════════"

# ── Round 1 ──────────────────────────────────
sleep_until "16:00"
start_agents

sleep_until "16:30"
stop_agents
echo "☕ [$(now_utc)] Break — Round 2 starts at 17:00 UTC"

# ── Round 2 ──────────────────────────────────
sleep_until "17:00"
start_agents

sleep_until "17:30"
stop_agents

echo ""
echo "✅ [$(now_utc)] Challenge complete! Check scores:"
echo "   https://bon.multiversx.com/guild-wars"

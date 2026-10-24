#!/bin/bash
# Kick the GitHub Actions runner when a workflow run sits queued too long
# while the runner looks online/idle (stale listener session). Idempotent.
# Usage: runner_watchdog.sh [--dry-run] [--simulate-queued]
set -u
REPO=${WATCHDOG_REPO:-RAZermenoSaracho/job_scraper}
RUNNER_DIR=${RUNNER_DIR:-/Users/razs/actions-runners/job_scraper}
MIN_AGE=${WATCHDOG_MIN_AGE_MINUTES:-10}
COOLDOWN=${WATCHDOG_COOLDOWN_MINUTES:-30}
STATE=${WATCHDOG_STATE:-$HOME/.local/state/runner_watchdog/$(basename "$REPO").last}
LOG=${WATCHDOG_LOG:-$HOME/Library/Logs/runner_watchdog.log}
DRY=0; SIM=0
for a in "$@"; do case $a in --dry-run) DRY=1;; --simulate-queued) SIM=1;; esac; done

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

if [ $SIM = 1 ]; then
  runs="[{\"databaseId\":0,\"workflowName\":\"simulated\",\"createdAt\":\"$(python3 -c 'import datetime as d;print((d.datetime.now(d.timezone.utc)-d.timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%SZ"))')\"}]"
else
  runs=$(gh run list -R "$REPO" --status queued --json databaseId,createdAt,workflowName) || { log "gh run list failed"; exit 1; }
fi

old=$(MIN_AGE=$MIN_AGE python3 -c '
import sys,json,os,datetime as d
n=d.datetime.now(d.timezone.utc);m=int(os.environ["MIN_AGE"])
for r in json.load(sys.stdin):
    a=(n-d.datetime.fromisoformat(r["createdAt"].replace("Z","+00:00"))).total_seconds()/60
    if a>m: print(r["databaseId"],int(a))' <<<"$runs")
[ -z "$old" ] && exit 0

# Resolve launchd label: env, else .service plist basename.
LABEL=${RUNNER_LABEL:-}
if [ -z "$LABEL" ] && [ -f "$RUNNER_DIR/.service" ]; then
  LABEL=$(basename "$(cat "$RUNNER_DIR/.service")" .plist)
fi
[ -z "$LABEL" ] && { log "no runner label (set RUNNER_LABEL)"; exit 1; }
RUNNER_NAME=${RUNNER_NAME:-${LABEL##*.}}

log "queued >${MIN_AGE}m: $(echo $old | tr '\n' ';') runner=$RUNNER_NAME"
status=$(gh api "repos/$REPO/actions/runners" | RN=$RUNNER_NAME python3 -c '
import sys,json,os
for r in json.load(sys.stdin)["runners"]:
    if r["name"]==os.environ["RN"]: print(r["status"], "busy" if r["busy"] else "idle"); break
else: print("missing")') || { log "gh api runners failed"; exit 1; }

case $status in
  *busy) log "runner busy, leaving it alone"; exit 0;;
  missing) log "runner $RUNNER_NAME not registered, no action"; exit 1;;
esac

if [ -f "$STATE" ]; then
  last=$(cat "$STATE"); now=$(date +%s)
  if [ $((now - last)) -lt $((COOLDOWN * 60)) ]; then
    log "cooldown active (last kick $(( (now - last) / 60 ))m ago), skipping"; exit 0
  fi
fi

if [ $DRY = 1 ]; then
  log "DRY-RUN: runner $status; would run: launchctl kickstart -k gui/$(id -u)/$LABEL and write $STATE"
  exit 0
fi
log "runner $status; kickstarting $LABEL"
if launchctl kickstart -k "gui/$(id -u)/$LABEL"; then
  mkdir -p "$(dirname "$STATE")"; date +%s > "$STATE"
else
  log "kickstart failed"; exit 1
fi

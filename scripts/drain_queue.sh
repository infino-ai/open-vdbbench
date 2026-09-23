#!/usr/bin/env bash
# Keep a run at its concurrency limit until a queue of engines is exhausted.
#
#   drain_queue.sh <stamp> [max_concurrent]
#
# Reads the engines still to launch from runs/<stamp>/queue.txt, one per line,
# and pops from the front. Every loop it collects and retires any leg that has
# finished, then launches as many queued engines as there are free slots.
#
# Slot counting asks Azure how many VMs are actually running rather than reading
# the status table: a leg can sit in the table as unreachable while its VM is
# deallocated and holding no quota at all, and counting that as a slot would
# stall the queue for hours.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

STAMP=${1:?usage: drain_queue.sh <stamp> [max_concurrent]}
MAX=${2:-10}
RG=${AZURE_RG:?set AZURE_RG to the resource group the VMs go in}
# A 10M leg spends three to four hours loading before it searches, so it needs a
# deadman well past the 240 minute default. Exported rather than left to the
# caller, because a leg launched from here with the default would be deallocated
# mid-load and produce nothing.
export DEADMAN_MIN=${DEADMAN_MIN:-600}
Q="runs/$STAMP/queue.txt"

say(){ echo "$(date -u +%H:%M:%S) $*"; }

running(){
  az vm list -d -g "$RG" --query \
    "length([?starts_with(name,'vdbb-') && powerState=='VM running'])" -o tsv 2>/dev/null \
    | tr -d '\r' | grep -E '^[0-9]+$' || echo "$MAX"
}

while true; do
  status=$(python scripts/azure_fleet.py status "$STAMP" 2>/dev/null)

  # A leg that reached done gets collected and its VM handed back.
  done_legs=$(echo "$status" | awk '/done rc=/{print $1}')
  for e in $done_legs; do
    say "collecting $e"
    python scripts/azure_fleet.py collect "$STAMP" "$e" >/dev/null 2>&1
    # A leg that measured nothing is the one whose logs matter, and retire
    # deletes the machine. Deallocating instead hands the cores back to the
    # quota and keeps the OS disk, so the reason can still be read off it.
    pts=$(python scripts/leg_measured.py "$STAMP" "$e" 2>/dev/null | tr -d "
")
    if [ "${pts:-0}" -gt 0 ] 2>/dev/null; then
      python scripts/azure_fleet.py retire "$STAMP" "$e" >/dev/null 2>&1 \
        && say "retired $e, $pts points measured, slot free" \
        || say "retire $e failed"
    else
      vm="vdbb-$e-$STAMP"
      az vm deallocate -g "$RG" -n "$vm" --no-wait -o none 2>/dev/null \
        && say "$e measured nothing: deallocated $vm, disk kept to diagnose" \
        || say "$e measured nothing, could not deallocate $vm"
    fi
  done

  live=$(running)
  free=$(( MAX - live ))
  [ "$free" -lt 0 ] && free=0

  while [ "$free" -gt 0 ] && [ -s "$Q" ]; do
    next=$(head -n 1 "$Q")
    tail -n +2 "$Q" > "$Q.tmp" && mv "$Q.tmp" "$Q"
    [ -z "$next" ] && continue
    say "launching $next ($live running, $free free)"
    if python scripts/azure_fleet.py up "$next" --into "$STAMP" >/dev/null 2>&1; then
      say "launched $next (deadman ${DEADMAN_MIN}m)"
    else
      say "launch $next FAILED, putting it back"
      printf '%s\n' "$next" | cat - "$Q" > "$Q.tmp" && mv "$Q.tmp" "$Q"
      break
    fi
    free=$(( free - 1 ))
  done

  if [ ! -s "$Q" ] && [ "$(echo "$status" | grep -cE 'running|unreachable')" -eq 0 ]; then
    say "queue empty and nothing running: 10M pass complete"
    break
  fi
  sleep 300
done

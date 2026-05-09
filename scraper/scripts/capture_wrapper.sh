#!/usr/bin/env bash
# Wrapper for the capture daemon. Maps exit codes to back-off delays per the
# scraper spec, then exits so systemd respawns us cleanly with fresh state.
#
# Exit codes (see src/capture/daemon.py):
#   0  clean shutdown
#  10  session dead, recovery exhausted    -> short cooldown
#  11  no virtustec iframe                 -> longer cooldown + alert
#  12  too many recoveries this hour       -> long back-off
#   1  uncaught                            -> medium cooldown

set -euo pipefail

cd "$(dirname "$0")/.."

uv run scraper capture --headless
code=$?

case "$code" in
  0)  delay=0 ;;
  10) delay=30 ;;
  11) delay=300 ;;
  12) delay=1800 ;;
  *)  delay=60 ;;
esac

if [ "$delay" -gt 0 ]; then
  echo "capture_wrapper: exited $code, sleeping ${delay}s before exit so systemd restarts" >&2
  sleep "$delay"
fi

exit "$code"

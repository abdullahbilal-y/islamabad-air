#!/usr/bin/env bash
# One scheduled run: fetch from PMD, republish the static data, push it.
# macOS/Linux equivalent of daily-poll.ps1. Install with cron, e.g.:
#
#   crontab -e
#   0 10,19 * * * /path/to/islamabad-air/scripts/daily-poll.sh >> /tmp/hawa.log 2>&1
#
# Note: cron does not catch up after the machine was asleep. On a laptop, prefer
# anacron or a systemd timer with Persistent=true -- a missed day cannot be
# recovered later, because PMD keeps no archive.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"

python="$repo/.venv/bin/python"
[ -x "$python" ] || { echo "No virtualenv at $python"; exit 1; }

echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting poll"

# `hawa poll` exits 0 even when PMD is down, so a transient outage is not a
# failed cron job every time their server hiccups.
"$python" -m hawa.cli poll

if [ -z "$(git status --porcelain -- docs)" ]; then
  echo "no data changes; nothing to publish"
  exit 0
fi

git add docs
git commit -q -m "data: pollen readings as of $(date '+%Y-%m-%d')"

# A failed push keeps the commit locally; the next run sends both.
if ! git push -q origin main; then
  echo "push failed; the commit is safe locally and will go out next run"
  exit 0
fi

echo "published and pushed"

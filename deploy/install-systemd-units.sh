#!/usr/bin/env bash
# Separate, operator-authorized installation; never stops the bot.
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1
[[ "$(id -u)" == 0 ]] || exit 1
[[ "$#" == 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || { echo 'Usage: sudo bash deploy/install-systemd-units.sh <FULL_REVIEWED_SHA>'; exit 2; }
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
LOCK="${SHPAKDND_LOCK:-/run/lock/shpakdnd-deploy.lock}"
"${SHPAKDND_TOOLING_PYTHON:-python3}" "$SOURCE/deploy/release_state.py" --source "$SOURCE" --sha "$1" --lock "$LOCK" --project "${SHPAKDND_PROJECT:-/opt/shpakdnd-bot}"
[[ ! -L "$LOCK" ]] || exit 1
exec 9>>"$LOCK"
flock -n 9 || { echo 'Deployment already running'; exit 1; }
exec "${SHPAKDND_TOOLING_PYTHON:-python3}" "$SOURCE/deploy/systemd_state.py" install --source "$SOURCE" --sha "$1"

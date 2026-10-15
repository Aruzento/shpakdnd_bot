#!/usr/bin/env bash
# Read-only observer. Never install code, initialize SQLite, or control services.
set -euo pipefail
export GIT_OPTIONAL_LOCKS=0
PROJECT="${SHPAKDND_PROJECT:-/opt/shpakdnd-bot}"
[[ -d "$PROJECT" && ! -L "$PROJECT" ]] || { echo 'Observer: project unavailable'; exit 1; }
head="$(git --no-optional-locks -c core.fsmonitor=false -C "$PROJECT" rev-parse --verify HEAD)" || exit 1
[[ "$head" =~ ^[0-9a-f]{40}$ ]] || exit 1
changes="$(git --no-optional-locks -c core.fsmonitor=false -c core.untrackedCache=false -C "$PROJECT" status --porcelain --untracked-files=normal)" || exit 1
if [[ -n "$changes" ]]; then dirty=yes; else dirty=no; fi
printf 'Observer: project change; HEAD=%s dirty=%s. Controlled deployment requires operator review.\n' "$head" "$dirty"

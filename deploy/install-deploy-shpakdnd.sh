#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run installer through sudo.'; exit 1; }
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# Frozen pvp_ops.py and the other eight runtime files are verified together.
# Explicit reviewed SHA is mandatory. The Python installer owns the same flock
# as deploy; it never imports application code or reads application settings.
exec /usr/bin/python3 -I -B "$SOURCE/install_bundle.py" "$@"

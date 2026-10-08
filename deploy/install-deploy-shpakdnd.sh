#!/usr/bin/env bash
# Bootstrap only the release tooling. No watched code, DB or systemd unit writes.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Запусти установку через sudo.'; exit 1; }
[[ "$#" == 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || { echo 'Usage: sudo bash deploy/install-deploy-shpakdnd.sh <FULL_REVIEWED_SHA>'; exit 2; }
export PYTHONDONTWRITEBYTECODE=1
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEST="${SHPAKDND_TOOLING_DEST:-/usr/local/lib/shpakdnd-deploy}"
BIN="${SHPAKDND_TOOLING_BIN:-/usr/local/bin}"
BOOTSTRAP_PYTHON="${SHPAKDND_TOOLING_PYTHON:-python3}"
LOCK="${SHPAKDND_LOCK:-/run/lock/shpakdnd-deploy.lock}"
STAGING="" LINK=""
"$BOOTSTRAP_PYTHON" "$SOURCE/release_state.py" --source "$SOURCE/.." --sha "$1" --dest "$DEST" --bin "$BIN" --lock "$LOCK" --project "${SHPAKDND_PROJECT:-/opt/shpakdnd-bot}"
[[ ! -L "$LOCK" ]] || exit 1
exec 9>>"$LOCK"
flock -n 9 || { echo 'Deployment уже выполняется.'; exit 1; }
"$BOOTSTRAP_PYTHON" "$SOURCE/release_state.py" --source "$SOURCE/.." --sha "$1" --dest "$DEST" --bin "$BIN" --lock "$LOCK" --project "${SHPAKDND_PROJECT:-/opt/shpakdnd-bot}"
[[ "$(readlink -m "$DEST")" != "$(readlink -f "$SOURCE/..")"/* ]] || exit 1
[[ "$DEST" == /* && "$BIN" == /* && ! -L "$DEST" && ! -L "$BIN" ]] || exit 1
[[ ! -L "$DEST/versions" && ( ! -e "$DEST/versions" || -d "$DEST/versions" ) ]] || exit 1
install -d -o root -g root -m 755 "$DEST" "$DEST/versions" "$BIN"
DEST="$(cd "$DEST" && pwd -P)"; BIN="$(cd "$BIN" && pwd -P)"
[[ "$(cd "$DEST/versions" && pwd -P)" == "$DEST/versions" ]] || exit 1
cleanup() {
    if [[ -n "$STAGING" && "$STAGING" == "$DEST"/versions/.install.* && ! -L "$STAGING" ]]; then rm -rf -- "$STAGING"; fi
    [[ -z "$LINK" ]] || rm -f -- "$LINK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
STAGING="$(mktemp -d "$DEST/versions/.install.XXXXXXXX")"
for helper in deploy_helpers.py telegram-deploy-notice.py sqlite-deploy.py release_preflight.py preflight_data.py release_state.py systemd_state.py release_lkg.py legacy_lkg.py; do
    install -o root -g root -m 644 "$SOURCE/$helper" "$STAGING/$helper"
done
install -o root -g root -m 755 "$SOURCE/deploy-shpakdnd.sh" "$STAGING/deploy-shpakdnd.sh"
install -d -o root -g root -m 755 "$STAGING/shared"
for shared in release_checks.py release_guard.py test_inventory.py; do
    install -o root -g root -m 644 "$SOURCE/../scripts/$shared" "$STAGING/shared/$shared"
done
bash -n "$STAGING/deploy-shpakdnd.sh"
"$BOOTSTRAP_PYTHON" -m py_compile "$STAGING"/*.py "$STAGING/shared"/*.py
# These are only caches made in this mktemp staging; never ship mutable bytecode.
rm -rf -- "$STAGING/__pycache__" "$STAGING/shared/__pycache__"
HASH="$("$BOOTSTRAP_PYTHON" -c 'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); [(h.update(f.relative_to(p).as_posix().encode()+b"\0"+f.read_bytes())) for f in sorted(p.rglob("*")) if f.is_file() and "__pycache__" not in f.parts]; print(h.hexdigest())' "$STAGING")"
VERSION="$DEST/versions/tooling_$HASH"
if [[ -e "$VERSION" ]]; then
    "$BOOTSTRAP_PYTHON" -c 'import pathlib,sys;sys.path.insert(0,sys.argv[1]);import release_preflight as p,release_state as s;s.trusted_path(sys.argv[2],directory=True);assert p.tooling_hash(sys.argv[1])==p.tooling_hash(sys.argv[2]),"Existing version differs from verified staging"' "$STAGING" "$VERSION"
else chmod 755 "$STAGING"; mv -- "$STAGING" "$VERSION"; STAGING=""; fi
LINK="$BIN/.deploy-shpakdnd.$$.new"
ln -s -- "$VERSION/deploy-shpakdnd.sh" "$LINK"
mv -Tf -- "$LINK" "$BIN/deploy-shpakdnd"; LINK=""
"$BOOTSTRAP_PYTHON" -c 'import os,sys;from pathlib import Path;[ (lambda f:(os.fsync(f),os.close(f)))(os.open(p,os.O_RDONLY|os.O_DIRECTORY)) for p in sys.argv[1:]] if os.name=="posix" else None' "$DEST/versions" "$BIN"
echo "Tooling bundle $HASH установлен атомарно."
echo 'Проверка: sudo deploy-shpakdnd --dry-run; затем sudo deploy-shpakdnd --preflight'

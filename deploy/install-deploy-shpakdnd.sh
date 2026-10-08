#!/usr/bin/env bash
# Bootstrap only the release tooling. No watched code, DB or systemd unit writes.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Запусти установку через sudo.'; exit 1; }
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEST="${SHPAKDND_TOOLING_DEST:-/usr/local/lib/shpakdnd-deploy}"
BIN="${SHPAKDND_TOOLING_BIN:-/usr/local/bin}"
BOOTSTRAP_PYTHON="${SHPAKDND_TOOLING_PYTHON:-python3}"
LOCK="${SHPAKDND_LOCK:-/run/lock/shpakdnd-deploy.lock}"
STAGING="" LINK=""
[[ ! -L "$LOCK" ]] || exit 1
exec 9>>"$LOCK"
flock -n 9 || { echo 'Deployment уже выполняется.'; exit 1; }
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
for helper in deploy_helpers.py telegram-deploy-notice.py sqlite-deploy.py release_preflight.py preflight_data.py; do
    install -o root -g root -m 644 "$SOURCE/$helper" "$STAGING/$helper"
done
install -o root -g root -m 755 "$SOURCE/deploy-shpakdnd.sh" "$STAGING/deploy-shpakdnd.sh"
bash -n "$STAGING/deploy-shpakdnd.sh"
"$BOOTSTRAP_PYTHON" -m py_compile "$STAGING"/*.py
HASH="$("$BOOTSTRAP_PYTHON" -c 'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); [(h.update(f.name.encode()+b"\0"+f.read_bytes())) for f in sorted(p.iterdir()) if f.is_file()]; print(h.hexdigest())' "$STAGING")"
VERSION="$DEST/versions/tooling_$HASH"
if [[ ! -e "$VERSION" ]]; then chmod 755 "$STAGING"; mv -- "$STAGING" "$VERSION"; STAGING=""; fi
LINK="$BIN/.deploy-shpakdnd.$$.new"
ln -s -- "$VERSION/deploy-shpakdnd.sh" "$LINK"
mv -Tf -- "$LINK" "$BIN/deploy-shpakdnd"; LINK=""
echo "Tooling bundle $HASH установлен атомарно."
echo 'Проверка: sudo deploy-shpakdnd --dry-run; затем sudo deploy-shpakdnd --preflight'

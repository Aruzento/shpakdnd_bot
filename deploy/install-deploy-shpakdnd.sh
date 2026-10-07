#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Запусти установку через sudo.'; exit 1; }
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="/usr/local/lib/shpakdnd-deploy"
exec 9>/run/lock/shpakdnd-deploy.lock
flock -n 9 || { echo 'Deployment уже выполняется.'; exit 1; }
install -d -o root -g root -m 755 "$DEST"
for helper in deploy_helpers.py telegram-deploy-notice.py sqlite-deploy.py; do
    install -o root -g root -m 644 "$SOURCE/$helper" "$DEST/$helper"
done
install -o root -g root -m 755 "$SOURCE/deploy-shpakdnd.sh" "$DEST/deploy-shpakdnd.sh"
ln -sfn "$DEST/deploy-shpakdnd.sh" /usr/local/bin/deploy-shpakdnd
echo 'Установлено. Проверка: sudo deploy-shpakdnd --dry-run'

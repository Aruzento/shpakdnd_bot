#!/usr/bin/env bash
# ONLY an ephemeral CI container: no host services/token/database are used.
set -Eeuo pipefail
[[ "$#" == 1 && "$1" =~ ^[0-9a-f]{40}$ ]]
export PYTHONDONTWRITEBYTECODE=1
for attempt in $(seq 1 30); do
    status="$(systemctl is-system-running || true)"
    [[ "$status" == running || "$status" == degraded ]] && break
    sleep 1
done
cp -r /source /opt/reviewed
chmod -R go-w /opt/reviewed
[[ "$(git -C /opt/reviewed rev-parse HEAD)" == "$1" ]]
git -C /opt/reviewed remote set-url origin https://github.com/Aruzento/shpakdnd_bot.git
useradd --system --home-dir /nonexistent shpaktest
mkdir -p /opt/stage-bot/app/mini/content
cat > /opt/stage-bot/bot.py <<'PY'
import time
print('ISOLATED STAND: no Telegram client or polling',flush=True)
while True:time.sleep(1)
PY
printf '.env\n*.db*\n__pycache__/\n' > /opt/stage-bot/.gitignore
printf 'BOT_TOKEN=ci-test-token\n' > /opt/stage-bot/.env
python3 - <<'PY'
import sqlite3
with sqlite3.connect('/opt/stage-bot/shpakdnd.db') as c:
 for table in ('mini_worlds','mini_players','mini_wallet_transactions','mini_event_sessions'):
  c.execute('CREATE TABLE '+table+'(id INTEGER PRIMARY KEY)');c.execute('INSERT INTO '+table+' VALUES(1)')
PY
chown -R shpaktest:shpaktest /opt/stage-bot
chmod 600 /opt/stage-bot/.env /opt/stage-bot/shpakdnd.db
runuser -u shpaktest -- git -C /opt/stage-bot init -q
runuser -u shpaktest -- git -C /opt/stage-bot config user.name Stand
runuser -u shpaktest -- git -C /opt/stage-bot config user.email stand@example.invalid
runuser -u shpaktest -- git -C /opt/stage-bot remote add origin https://github.com/Aruzento/shpakdnd_bot.git
runuser -u shpaktest -- git -C /opt/stage-bot add .
runuser -u shpaktest -- git -C /opt/stage-bot commit -qm 'Isolated dummy process'
cat > /etc/systemd/system/shpakdnd-bot.service <<'UNIT'
[Unit]
Description=Isolated dummy process without network/Telegram
[Service]
User=shpaktest
WorkingDirectory=/opt/stage-bot
ExecStart=/usr/bin/python3 /opt/stage-bot/bot.py
Restart=no
[Install]
WantedBy=multi-user.target
UNIT
cat > /usr/local/bin/update-shpakdnd-bot.sh <<'LEGACY'
#!/bin/bash
# Unsafe legacy fixture is archived; never executed.
exit 99
LEGACY
chmod 755 /usr/local/bin/update-shpakdnd-bot.sh
cat > /etc/systemd/system/shpakdnd-bot-update.service <<'UNIT'
[Service]
Type=oneshot
ExecStart=/usr/local/bin/update-shpakdnd-bot.sh
UNIT
cat > /etc/systemd/system/shpakdnd-bot-watch.path <<'UNIT'
[Path]
PathModified=/opt/stage-bot/bot.py
Unit=shpakdnd-bot-update.service
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl start shpakdnd-bot.service
export SHPAKDND_PROJECT=/opt/stage-bot SHPAKDND_BOT_USER=shpaktest SHPAKDND_PYTHON=/usr/bin/python3
PID="$(systemctl show -p MainPID --value shpakdnd-bot.service)"
DB_HASH="$(sha256sum /opt/stage-bot/shpakdnd.db)"
STARTED="$(date +%s%N)"
bash /opt/reviewed/deploy/install-deploy-shpakdnd.sh "$1"
bash /opt/reviewed/deploy/install-systemd-units.sh "$1"
ENDED="$(date +%s%N)"
# Reinstallation performs no replacement or reload.
bash /opt/reviewed/deploy/install-systemd-units.sh "$1"
python3 /opt/reviewed/deploy/systemd_state.py verify --project /opt/stage-bot
for file in bot.py app/extra.py app/mini/content/fixture.json; do
    printf '\n# deliberately dirty stand event\n' >> "/opt/stage-bot/$file"
    sleep 1
    [[ "$(systemctl show -p MainPID --value shpakdnd-bot.service)" == "$PID" ]]
    [[ "$(systemctl show -p NRestarts --value shpakdnd-bot.service)" == 0 ]]
done
[[ "$(sha256sum /opt/stage-bot/shpakdnd.db)" == "$DB_HASH" ]]
journalctl --no-pager -u shpakdnd-bot-update.service | tee /tmp/observer-journal
# grep is part of the container baseline; no files are selected/deleted here.
grep -qi 'controlled deployment' /tmp/observer-journal
python3 /opt/reviewed/deploy/systemd_state.py verify --project /opt/stage-bot
mkdir -p /etc/systemd/system/shpakdnd-bot-update.service.d
printf '[Service]\nExecStartPost=/bin/true\n' > /etc/systemd/system/shpakdnd-bot-update.service.d/unapproved.conf
systemctl daemon-reload
if python3 /opt/reviewed/deploy/systemd_state.py verify --project /opt/stage-bot; then exit 1; fi
# Failed repair refuses unapproved overrides and leaves watcher quarantined.
if bash /opt/reviewed/deploy/install-systemd-units.sh "$1"; then exit 1; fi
[[ "$(systemctl is-active shpakdnd-bot-watch.path || true)" == inactive ]]
[[ "$(systemctl show -p MainPID --value shpakdnd-bot.service)" == "$PID" ]]
[[ -f /etc/systemd/system/shpakdnd-bot-update.service.d/shpakdnd-quarantine.conf ]]
[[ -f /var/lib/shpakdnd-release/observer-quarantined ]]
# Simulate reboot losing runtime masks: persistent condition blocks unsafe job.
systemctl unmask --runtime shpakdnd-bot-update.service
systemctl daemon-reload
systemctl start shpakdnd-bot-update.service
[[ "$(systemctl show -p ConditionResult --value shpakdnd-bot-update.service)" == no ]]
[[ "$(systemctl show -p MainPID --value shpakdnd-bot.service)" == "$PID" ]]
python3 - "$1" "$STARTED" "$ENDED" "$PID" <<'PY'
import json,sys
print('SYSTEMD_STAND_RESULT='+json.dumps(dict(status='PASS',source_sha=sys.argv[1],install_seconds=(int(sys.argv[3])-int(sys.argv[2]))/1e9,bot_pid=sys.argv[4],bot_restarts=0,sqlite_unchanged=True,observer_events=3,unit_drift_detected=True,persistent_quarantine=True)))
PY

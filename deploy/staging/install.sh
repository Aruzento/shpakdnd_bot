#!/usr/bin/env bash
# Tooling only; run manually from the reviewed, merged main commit on TEST.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == VM-338333 ]] || { echo 'Requires root on VM-338333'; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -d -o root -g root -m 755 /usr/local/lib/shpakdnd-staging /opt/shpakdnd-staging /opt/shpakdnd-staging/releases
install -d -o root -g root -m 755 /var/lib/shpakdnd-staging
install -d -o root -g root -m 700 /var/lib/shpakdnd-staging/reports
install -d -o root -g shpakbot -m 750 /etc/shpakdnd-staging
install -d -o shpakbot -g shpakbot -m 700 /var/lib/shpakdnd-staging/data
exec 9>/var/lib/shpakdnd-staging/deploy.lock
flock -n 9 || { echo 'Staging deployment already running'; exit 1; }
for name in controller.py gate.py inventory.py preflight.py startup.py ssh_entry.py; do
    install -o root -g root -m 644 "$source_dir/$name" "/usr/local/lib/shpakdnd-staging/$name"
done
install -o root -g root -m 644 "$source_dir/../pvp_ops.py" /usr/local/lib/shpakdnd-staging/pvp_ops.py
echo 'Frozen tooling installed. No service, SSH configuration, database or current release changed.'
echo 'Complete docs/operations/STAGING-AUTOMATION.md before enabling dispatch.'

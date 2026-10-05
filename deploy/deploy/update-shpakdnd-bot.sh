#!/usr/bin/env bash
set -euo pipefail

PROJECT="/opt/shpakdnd-bot"
PYTHON="$PROJECT/.venv/bin/python"
SERVICE="shpakdnd-bot.service"

echo "Обнаружено изменение проекта."
sleep 2

cd "$PROJECT"

echo "Проверяю синтаксис проекта..."
"$PYTHON" -m compileall -q bot.py app

echo "Синтаксис OK."

# WinSCP может загрузить код от root. Меняем владельца только у файлов кода.
if [ "$(stat -c '%U' "$PROJECT/bot.py")" != "shpakbot" ]; then
    chown shpakbot:shpakbot "$PROJECT/bot.py"
fi

find "$PROJECT/app" ! -user shpakbot -exec chown shpakbot:shpakbot {} +

echo "Перезапускаю бота..."
systemctl restart "$SERVICE"
sleep 2

if systemctl is-active --quiet "$SERVICE"; then
    echo "Бот успешно перезапущен."
else
    echo "ОШИБКА: бот после обновления не запустился."
    systemctl --no-pager status "$SERVICE"
    exit 1
fi

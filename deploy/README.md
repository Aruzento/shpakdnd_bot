# Обновление watcher после перехода на модули

Старый watcher следил только за `bot.py`. Теперь нужно следить также за папкой `app/`.

Скопировать:

```bash
cp deploy/update-shpakdnd-bot.sh /usr/local/bin/update-shpakdnd-bot.sh
chmod +x /usr/local/bin/update-shpakdnd-bot.sh

cp deploy/shpakdnd-bot-update.service /etc/systemd/system/shpakdnd-bot-update.service
cp deploy/shpakdnd-bot-watch.path /etc/systemd/system/shpakdnd-bot-watch.path

systemctl daemon-reload
systemctl enable --now shpakdnd-bot-watch.path
systemctl restart shpakdnd-bot-watch.path
systemctl status shpakdnd-bot-watch.path
```

Ожидаемое состояние:

```text
Active: active (waiting)
```

Проверка логов автообновления:

```bash
journalctl -u shpakdnd-bot-update.service -n 50
```

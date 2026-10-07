# Production release deployment V1.3.3

Интерактивная команда `sudo deploy-shpakdnd`: установка, dry run, backup, проверки и rollback
описаны в [docs/deployment.md](../docs/deployment.md). Она отдельна от filesystem watcher ниже.

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


## V1.2.5: вложенные модули и каталоги

Path unit явно перечисляет каталоги UI, combat/hero abilities, effects,
migrations, events, Boss и JSON-контента. Наблюдение inotify за директорией
не рекурсивно ([inotify(7)](https://www.man7.org/linux/man-pages/man7/inotify.7.html)).
При добавлении новой feature-директории добавь соответствующий PathModified;
`tests/test_architecture.py` проверяет покрытие всех app Python/JSON directories.
DB, .env и __pycache__ не перечисляются в watch paths.

При обновлении V1.2.5 повторно установи path unit и выполни daemon-reload/restart
watcher по инструкции выше. Изменения этих unit-файлов сами по себе не изменяют
установленный service на сервере. Runtime-проверку systemd выполняй на Linux;
локально проверяется структура unit и её покрытие файлов проекта.

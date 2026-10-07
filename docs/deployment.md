# Production deployment D&D Mini

## Установка команды

Production: `/opt/shpakdnd-bot`, пользователь `shpakbot`, Python
`/opt/shpakdnd-bot/.venv/bin/python`, SQLite `/opt/shpakdnd-bot/shpakdnd.db`.
Существующие units: `shpakdnd-bot.service`, `shpakdnd-bot-watch.path`,
`shpakdnd-bot-update.service`. Watcher вызывает прежний маленький
`update-shpakdnd-bot.sh`; release manager его не заменяет.

После получения V1.4 один раз установить tooling:

```bash
cd /opt/shpakdnd-bot
sudo bash deploy/install-deploy-shpakdnd.sh
sudo deploy-shpakdnd --dry-run
```

Installer копирует небольшой bundle в `/usr/local/lib/shpakdnd-deploy` и создаёт
symlink `/usr/local/bin/deploy-shpakdnd`. Команда продолжает существовать даже
после code rollback на релиз без deployment scripts. При обновлении самого
tooling повторить installer; каждое обычное обновление этого не требует.
Installer и deploy используют один lock `/run/lock/shpakdnd-deploy.lock`.
Нужны Bash, Git с `switch --no-overwrite-ignore`, util-linux (`flock`, `runuser`),
systemd, coreutils и уже установленные Python dependencies проекта. `.env`/DB
должны быть доступны `shpakbot`, а project — доступен ему на запись. Service
должен иметь ожидаемые User/WorkingDirectory/ExecStart; скрипт проверяет их.

Начальную установку нового tooling на старом сервере делайте в существующее
maintenance window: остановите watcher, его update service и бот перед
получением первого релиза со скриптом, затем установите команду. С последующих
релизов всю последовательность выполняет команда ниже. Сам installer не
переключает Git, не останавливает и не запускает production.

## Обычный релиз

Разработчик заранее публикует commit в GitHub. На сервере:

```bash
sudo deploy-shpakdnd
```

1. Проверяется clean worktree, paths, пользователь и units; выполняется
   `git fetch origin`, пока бот работает. Печатаются текущий SHA и SHA
   `origin/main`. При одинаковом SHA restart/checks требуют согласия.
2. После согласия во все enabled Mini worlds отправляется
   «🛠 Технический перерыв». При ошибке требуется отдельное согласие продолжить.
3. Останавливаются path watcher, уже запущенный update oneshot, затем бот;
   все три должны стать inactive.
4. SQLite Online Backup API создаёт
   `/opt/shpakdnd-backups/shpakdnd_YYYYMMDD_HHMMSS.db`. Файл создаётся эксклюзивно,
   проходит integrity/FK checks. Показываются Old/Target SHA и backup path.
5. Второе подтверждение разрешает установить именно показанный target SHA.
   `git switch --no-overwrite-ignore -C main <SHA>` не уничтожает локальные
   изменения и не перезаписывает ignored файлы. HEAD сверяется с target.
   Tracked `.env`, production DB и `.venv` в old/target запрещены.
6. Под `shpakbot` последовательно выполняются compileall, runtime DB_PATH check,
   `check_bot.py`, validators, весь unittest suite, SQLite integrity/FK checks.
   Затем проверяются release diff, фактический HEAD и clean worktree.
7. Выводятся результаты и число tests. Только подтверждение «Запустить новую
   версию?» разрешает запуск. Пустой Enter — согласие; закрытый stdin — отказ.
8. Bot service запускается первым. Две проверки с ожиданием по 5 секунд
   проверяют active, стабильность MainPID/NRestarts; свежий journal проверяется
   на startup failures/restart loop. Watcher запускается после первой проверки,
   затем повторно проверяется стабильность бота.
9. Когда bot и watcher active, отдельный helper отправляет окончание перерыва
   с коротким SHA. Ошибка финального notice выводит WARNING; работающий релиз
   остаётся успешным. Итог: HEAD, backup, unit states, deployment log.

Production ничего не пушит в GitHub. Fetch фиксирует target SHA один раз:
изменение origin/main другим процессом между подтверждениями не подменяет его.
Скрипт не выполняет dependency install; релиз с новыми dependencies требует
заранее подготовленного virtualenv в согласованное maintenance window.

## Telegram и миграции

`deploy/telegram-deploy-notice.py --project ... --text ...` работает независимо
от polling-процесса. Он импортирует существующий `app.config` (`.env`, BOT_TOKEN,
DB_PATH), открывает SQLite read-only, читает enabled `mini_worlds` и отправляет
Bot API `sendMessage` с соответствующим `chat_id` и `message_thread_id`.
Thread 0 означает обычный чат; параметр thread тогда не передаётся. Несколько
worlds получают отдельные сообщения; ошибка одной не отменяет попытки для
остальных. Пустой список worlds — явная ошибка. Исключения транспорта/ответы
Telegram не печатаются: они могут содержать токен. Повторные HTTP sends
автоматически не выполняются.

`check_bot.py` вызывает тот же `init_db` / `init_mini_db` / `init_boss_db`, что
production startup; каталог и схема проходят обычные validators. Отдельного
migration engine в shell нет. DB_PATH проверяется до backup и после checkout,
до потенциальной мутации БД. V1.4 добавляет 11 таблиц Village/Duel/Streak/Shadow/Mythic, совместимые поля
Daily/Boss snapshots и ledger идемпотентных круток. Исторические таблицы Events сохраняются.

Pipeline:

```bash
./.venv/bin/python -m compileall -q bot.py app
./.venv/bin/python check_bot.py
./.venv/bin/python -m app.mini.combat.hero_abilities.validate
./.venv/bin/python -m app.mini.boss.boss_abilities.validate
./.venv/bin/python -m app.mini.tower.validate
./.venv/bin/python -m unittest discover -s tests -q
# helper: PRAGMA integrity_check и PRAGMA foreign_key_check
# git diff --check OLD_HEAD TARGET_HEAD; HEAD и clean worktree
```

Другие content/schema validators входят в `check_bot.py`.

## Отказ, ошибка и rollback

До первой остановки отказ не меняет работающий стек. После backup отказ от
установки сохраняет код и предлагает явно запустить прежнюю версию; можно
осознанно оставить bot/watcher остановленными.

Failed check немедленно прекращает pipeline и запрещает запуск нового кода.
Показываются Previous/Target HEAD и backup, затем выбор:

- `[1]` оставить бот остановленным;
- `[2]` вернуть Old Git HEAD и запустить предыдущую версию.

Подтверждённый code rollback снова останавливает весь стек, безопасно
переключает main на Old SHA, выполняет тот же pipeline и проверки startup,
затем возвращает watcher и завершает maintenance notice. Requested target
deployment всё равно возвращает non-zero, даже если recovery успешен.
Если старый код несовместим с текущей БД или проверки не проходят, автоматического
запуска нет. Backup failure также предлагает эти варианты; восстановление
производится только после валидных проверок.

**DB никогда не заменяется автоматически.** Новая версия могла выполнить
additive migration, а после backup могли появиться данные. Восстановление БД —
отдельное ручное действие после оценки совместимости, сохранения текущей БД и
остановки bot/watcher. Изменять DB при работающем боте нельзя.

Failed start показывает status и последние journal logs, останавливает
нездоровый стек и предлагает code rollback. Watcher не включается до первой
успешной проверки бота. При ошибке watcher обе службы возвращаются в остановленное
состояние. Окончание перерыва не публикуется для нездорового стека.

Ctrl+C/SIGTERM выводят текущие unit states, HEAD и backup; скрытого запуска нет.
После отключения stdin на финальном подтверждении код остаётся установленным,
бот/watcher — остановленными, что явно указано в итоговом сообщении.
Двойной deploy сразу завершается: «Deployment уже выполняется».

## Логи и dry run

Логи: `/var/log/shpakdnd-deploy/deploy_YYYYMMDD_HHMMSS_PID.log` (root, mode 600).
Записываются timestamps, SHA, backup, checks, startup и rollback. Поток console/log
проходит redaction значений `.env` и Telegram token pattern; `.env` никогда не
печатается. Git credentials не следует помещать в URL remote.

Hotfix V1.3.3: все приглашения подтверждения и выбора rollback завершаются
newline до `read`, чтобы построчный redactor сразу показал вопрос пользователю.
Defaults и EOF semantics сохранены. После получения hotfix повторите installer
выше: установленный bundle вне checkout автоматически не обновляется.

```bash
sudo deploy-shpakdnd --dry-run
```

Проверяет project/env/Python/DB/unit configuration, clean Git, выполняет fetch,
показывает current/target и states. Не отправляет Telegram, не останавливает
службы, не checkout, не запускает tests/migrations, не меняет DB. Создаются только
lock и временная read-only для shpakbot копия helper bundle (удаляется при exit);
fetch обновляет remote refs.

Пути/user/unit constants имеют готовые production defaults. Переменные
`SHPAKDND_PROJECT`, `SHPAKDND_PYTHON`, `SHPAKDND_BACKUPS`, `SHPAKDND_LOGS`,
`SHPAKDND_LOCK`, `SHPAKDND_RUNTIME_DIR`, `SHPAKDND_START_WAIT` и unit/user overrides
существуют для изолированной тестовой среды; обычный deploy их не требует.
Локальные regression tests выполняют реальный Bash с fake Git/systemd/Telegram
и временными paths. Linux permissions, реальный flock и systemd/Telegram
подтверждаются dry run и первым контролируемым deployment на сервере.

# Production deployment D&D Mini V1.4.1 — этап B

Production defaults: `/opt/shpakdnd-bot`, `shpakbot`, `.venv/bin/python`,
`shpakdnd.db`; units `shpakdnd-bot.service`, `shpakdnd-bot-watch.path`,
`shpakdnd-bot-update.service`. Этот документ описывает release manager.
Обновление legacy watcher/units относится к этапу C.

## Bootstrap нового установленного tooling

Правка repository scripts не обновляет `/usr/local/bin/deploy-shpakdnd`.
Новый installer получает весь bundle из отдельного checkout проверенного
commit **вне production и watch paths**, без копирования `.env` или SQLite.
После независимого одобрения этапа B оператор может выполнить:

```bash
# В отдельной директории, например /var/tmp/shpakdnd-tooling-source:
git clone https://github.com/Aruzento/shpakdnd_bot.git /var/tmp/shpakdnd-tooling-source
cd /var/tmp/shpakdnd-tooling-source
git checkout --detach <FULL_REVIEWED_STAGE_B_SHA>
sudo bash deploy/install-deploy-shpakdnd.sh
sudo deploy-shpakdnd --dry-run
```

Не переключайте работающий `/opt/shpakdnd-bot` ради bootstrap. Installer
использует тот же `/run/lock/shpakdnd-deploy.lock`, копирует helper bundle в
защищённый staging, проверяет Python/shell syntax, затем атомарно переключает
symlink на `/usr/local/lib/shpakdnd-deploy/versions/tooling_<HASH>`.
При ошибке прежняя команда сохраняется; старые bundles остаются для разбора.
Installer не пишет в игровую директорию/DB и не меняет systemd units/services.
Нужны root, Bash, Git, coreutils, util-linux (`flock`, `runuser`), systemd,
production Python с dotenv/pip/venv и доступ к зависимостям requirements.

Deployment по-прежнему получает TARGET из `origin/main`: до одобренного
включения stage B в main preflight намеренно отклонит старый TARGET без
обязательной infrastructure. Bootstrap сам ничего не merge/deploy.

## Preflight при работающем боте

```bash
sudo deploy-shpakdnd --preflight
```

1. Единственный deployment lock защищает preflight, deploy и bootstrap.
   Проверяются project/env/DB access, clean Git и unit configuration.
   `git fetch origin` фиксирует полные OLD SHA и TARGET SHA `origin/main`.
2. Проверяется Git history/integrity, baseline V1.4 и неизменяемый stage A,
   наличие обязательных файлов, отсутствие tracked env/DB/venv. В этапе B
   изменения game code/catalog/schema относительно stage A запрещены.
3. Отдельный clone с полной историей и detached точным TARGET создаётся в
   `/var/tmp/shpakdnd-preflight/shpakdnd-preflight-*`, вне production/watch paths.
   Production HEAD не переключается. Отдельный clone OLD нужен для rollback.
4. В private окружении пользователя бота создаётся новый venv, устанавливаются
   TARGET requirements, выполняется pip check. Отдельно проверяется, что
   установленный production venv удовлетворяет TARGET requirements; он не
   изменяется. Недостающие packages требуют отдельного плана обновления.
5. `deploy_helpers.backup_database` открывает live DB с `mode=ro`, использует
   SQLite Online Backup API (включая committed WAL), проверяет integrity/FK.
   Snapshot должен содержать настоящую инициализированную Mini schema.
   Обычное копирование `.db` и пустая DB вместо migration snapshot запрещены.
6. Перед записью helper проверяет реальные app.config BASE_DIR/DB_PATH и
   check_bot.DB_PATH, SHA checkout, symlinks и physical aliases/hardlinks.
   На snapshot запускается штатный `check_bot.main`: init_db/init_mini_db/
   init_boss_db, каталоги и validators. Production DB_PATH недопустим.
7. Сравниваются все существующие таблицы, определения колонок и multiset
   хешей всех исходных записей: игроки, wallet, Events обеих игр, boss snapshots,
   tower/equipment, village/duels, mythic/shadow и любые другие таблицы.
   Допустимые service fields: worlds name/enabled; heroes name/description/
   image_path/passive_text; items name/description; equipment name; offers
   title/description. metadata_json, price/stock, rarity/attack, ownership и
   любые game state fields сравниваются строго.
   Пользовательские данные должны сохраниться; новые пользовательские строки
   в существующих таблицах также запрещены. Добавленные таблицы/колонки
   записываются в evidence. Второе применение check_bot обязано сохранить
   уже мигрированные данные строго, включая metadata.
8. На отдельную копию мигрированной DB запускается OLD check_bot с прежним
   production interpreter и фиктивной конфигурацией. Проверяются integrity/FK
   и строгая сохранность данных. Это только compatibility probe, не rollback.
9. На TARGET запускается общий `scripts/release_checks.py`: полный unittest,
   discovery preservation, release guard, diff --check, compileall, check_bot
   на другой disposable DB, все content/ability/tower validators и bash -n.
   Migration snapshot отделён от тестовой DB. Count определяется discovery;
   failures/errors/skips/expected failures/unexpected successes недопустимы.
10. Повторно проверяются OLD HEAD, чистота источника и состояния/PID/NRestarts
    production. Cleanup удаляет только собственный mkdtemp directory после
    завершения дочерних процессов, включая SIGTERM/error. PASS публикуется
    только после всех проверок и успешного cleanup.

В окружение TARGET не попадают настоящий `.env`, BOT_TOKEN, proxy credentials
или произвольные environment values. Используется `ci-test-token`; сохраняется
только проверяемый timezone setting. Общий sitecustomize release runner запрещает
внешние socket connections; bot.py/polling не запускается. Package installation
может обращаться к package index до migration/test network guard.

## Evidence и срок действия

JSON, SHA256 sidecar и redacted log хранятся в `/var/lib/shpakdnd-preflight`
(root:root 0700; files 0600). Evidence включает SHA, оба baseline, hash полного
замороженного installed bundle, UTC times, phases, discovery/executed counts,
migration preservation/integrity/FK, schema fingerprints, dependency result,
rollback compatible true/false/unknown и hash защищённого log.

Validation требует owner/private permissions, regular files без symlinks,
checksums report/log, полный PASS, совпадение SHA/tooling и возраст не более
часа. RUNNING, NOT RUN, FAIL, отсутствующий/прерванный report недействительны.
Обычный deploy всегда проводит **новый** preflight; reuse/skip flag отсутствует.
Записи игроков не входят в условие актуальности evidence: перед checkout
проверяется fingerprint schema свежей live DB, а не hash её содержимого.
Изменение OLD/TARGET/tooling требует нового preflight. Новый origin/main не
подменяет уже зафиксированный SHA.

## Короткий deployment

```bash
sudo deploy-shpakdnd
```

До первого `systemctl stop` завершается весь preflight, валидируется evidence,
получается подтверждение пользователя, повторно проверяются source/services,
затем отправляется notice о перерыве. Ошибка notice требует отдельного согласия.

После stop watcher → update oneshot → bot все units должны быть inactive.
Создаётся **свежий** эксклюзивный final SQLite Online Backup с integrity/FK
в `/opt/shpakdnd-backups`, до checkout. Второе подтверждение разрешает установить
только проверенный SHA: `git switch --no-overwrite-ignore -C main <SHA>`.
Ignored env/venv/DB защищены, dirty worktree и tracked production files запрещены.

После checkout выполняются только DB_PATH/SHA checks, init_db/init_mini_db/
init_boss_db и SQLite integrity/FK, clean worktree/evidence schema validation.
Полный check_bot с каталогами, unittest, compileall и validators уже закончены
до downtime; они не запускаются при deployment или rollback после остановки.
После подтверждения запускается bot; active/MainPID/NRestarts и свежий journal
проверяются до watcher и ещё раз после него. Только здоровый bot + watcher
разрешают финальный notice. Ошибка финального notice — warning с сохранением
здорового состояния. EOF никогда не считается подтверждением.

## Ошибки и rollback

До остановки: команда возвращает nonzero, production остаётся в исходном
состоянии, notice/checkout/migrations/restart не выполняются. Защищённый log
объясняет сбой; устраните причину и запустите полный preflight заново.

После остановки: fail-closed, неисправные services останавливаются, watcher
не активируется ради сокращения простоя. Summary содержит OLD/TARGET/current
SHA, backup и states. Автоматический code rollback отсутствует. Пользователь
может оставить всё остановленным либо явно выбрать rollback; последний
разрешён только при актуальном evidence, rollback_compatible=true и точном
совпадении live schema с проверенной мигрированной schema. false/unknown
запрещают переключение и запуск. Повторные долгие checks при rollback отсутствуют;
выполняются только короткие runtime checks. Deployment target остаётся FAIL
даже после успешного возврата OLD. Ошибочное/прерванное завершение инвалидирует
PASS этого запуска. При отказе от TARGET до checkout OLD запускается только
после отдельного подтверждения; проверенный старый HEAD обязан совпадать.

Никогда не восстанавливайте backup поверх live DB автоматически: после запуска
могли появиться новые транзакции. Разбор failed runtime, dependency upgrade,
ручной recovery и согласование downtime требуют оператора. Закреплённый
last-known-good и модернизация legacy watcher — этап C; расширенный итоговый
release report — этап D. Legacy update script пока не использует этот pipeline;
этап B не объявляет его автоматическое обновление безопасным.

## Dry run и проверка разработки

`--dry-run` проверяет setup/fetch/clean paths/units и показывает SHA; он не
выполняет preflight и не разрешает deployment. Override paths/user/units,
`SHPAKDND_PREFLIGHT_EVIDENCE` и `SHPAKDND_PREFLIGHT_WORK` предназначены для
изолированных тестов. Evidence/work должны быть вне watched production paths.
Tests используют только временные Git/SQLite и fake systemd/Telegram, включая
fault injection, parallel lock, WAL, SIGTERM, evidence tampering и bootstrap.
Реальные production операции в разработке и CI не выполняются.

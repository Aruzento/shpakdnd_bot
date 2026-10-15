# Production deployment D&D Mini V1.4.1 — этап C

Production defaults: `/opt/shpakdnd-bot`, `shpakbot`, `.venv/bin/python`,
`shpakdnd.db`; units `shpakdnd-bot.service`, `shpakdnd-bot-watch.path`,
`shpakdnd-bot-update.service`. Этот документ описывает release manager.
Watcher является read-only observer; tooling, units и игровые релизы устанавливаются отдельными контролируемыми операциями. Ни одна команда ниже не выполняется Codex на production.

## Bootstrap из проверенного SHA

Оператор сначала проверяет независимое ревью и Linux CI полного SHA этапа C.
Используется отдельный root-owned чистый checkout вне production/watch paths.
`.env`, `.venv` и SQLite из production туда не копируются. Пример после одобрения:

```bash
# FULL_REVIEWED_SHA — полный SHA, подтверждённый ревью и CI.
sudo git clone https://github.com/Aruzento/shpakdnd_bot.git /opt/shpakdnd-tooling-source
sudo git -C /opt/shpakdnd-tooling-source checkout --detach "$FULL_REVIEWED_SHA"
sudo bash /opt/shpakdnd-tooling-source/deploy/install-deploy-shpakdnd.sh "$FULL_REVIEWED_SHA"
# Отдельное решение оператора: следующая команда меняет watcher configuration.
sudo bash /opt/shpakdnd-tooling-source/deploy/install-systemd-units.sh "$FULL_REVIEWED_SHA"
sudo deploy-shpakdnd --dry-run
```

Первый installer проверяет root ownership/permissions, clean Git, origin, полный
SHA и точные Git blobs. Проверки syntax выполняются в собственном staging.
Полный bundle и копии общего runner/guard/inventory устанавливаются в
`/usr/local/lib/shpakdnd-deploy/versions/tooling_<HASH>`, затем атомарно заменяется
`/usr/local/bin/deploy-shpakdnd`. Existing version проверяется перед reuse.
Данные и directory entries fsync; прерывание до переключения сохраняет старую
команду. Повреждённая ранее установленная версия не переиспользуется.
Рабочий checkout, SQLite и services не изменяются этим installer.

Второй installer отдельно устанавливает observer/units. Обе операции и release
manager используют один FD9 lock `/run/lock/shpakdnd-deploy.lock`; recursive
acquisition отсутствует. Root-only Linux helpers проверяют фактический lock.
Control paths не могут пересекаться с source/live checkout или alias SQLite.
Нужны Bash, Git, Python3, coreutils, util-linux, systemd/systemd-analyze; release
preflight дополнительно требует рабочий Python с dotenv/pip/venv и packages.

Bootstrap не меняет `origin/main`, live HEAD или игровую DB. Пока main не содержит
утверждённую C infrastructure, обычный preflight намеренно отклоняет старый TARGET.
Отдельная процедура legacy LKG ниже поддерживает только pinned V1.4 baseline.

## Безопасная установка watcher units

`install-systemd-units.sh <FULL_REVIEWED_SHA>` не останавливает игровой bot.
Источник и все helper/shared blobs проверяются до изменения services. Существующий
root-owned bot fragment сохраняется и включается в manifest, его конфигурация
не переписывается installer. Project, user и Python должны соответствовать
существующему bot unit. Defaults: `/opt/shpakdnd-bot`, `shpakbot`, `.venv/bin/python`.
Для независимого стенда используются SHPAKDND_PROJECT/BOT_USER/PYTHON внутри
отдельной systemd namespace; реальные production units не переименовываются.

Порядок установки:

1. Зафиксировать source SHA, получить единый lock; сохранить RUNNING report.
2. Durably установить root-owned `shpakdnd-quarantine.conf` с отрицательным
   ConditionPathExists на существующий private quarantine marker. Этот запрет
   сохраняется после перезагрузки, в отличие от runtime mask.
3. Mask/stop update, disable/stop watcher. Проверить quiescence и отсутствие
   посторонних drop-ins. Здоровый bot PID/NRestarts сохраняется.
4. Архивировать прежние units и legacy helper (helper только как 0600 текст).
   В private staging проверить shell syntax и systemd-analyze verify.
5. Установить immutable observer в
   `/usr/local/lib/shpakdnd-observer/versions/<SHA>/update-shpakdnd-bot.sh`;
   атомарно заменить два units и старый `/usr/local/bin/update-shpakdnd-bot.sh`
   безопасной ссылкой через wrapper на observer.
6. Daemon-reload, проверить hashes всех установленых файлов и эффективные
   overrides. Удалить только собственный quarantine drop-in после проверки
   безопасного набора. Unmask update, daemon-reload, проверить loaded properties.
7. Enable/start watcher, однократно вызвать observer, проверить неизменный bot
   PID/NRestarts, journal/loaded properties. Атомарно сохранить CONFIRMED manifest.

Manifest: `/var/lib/shpakdnd-release/systemd-installation.json` (root 0700/0600),
checksum envelope, source SHA, tooling hash, file hashes, project/user/interpreter,
exact helper, unit names, watched paths и normalized loaded config hash.
Installation reports/forensic backups: `.../installations/<UTC_SHA>/`.
Повторная подтверждённая установка ничего не заменяет и не делает reload.

Ошибка оставляет watcher отключённым, update masked, persistent quarantine
сохранённым, healthy bot работающим. RUNNING/FAIL report не разрешает release.
После SIGKILL/power loss частично заменённые units не дают PASS; durable
quarantine блокирует legacy update и после reboot. Повторить installer можно
только из того же или нового отдельно одобренного source SHA. Посторонние
root overrides требуют ручного разбора. Архивированный legacy helper никогда
не восстанавливается и не включается автоматически.

## Observer и проверка фактически загруженной конфигурации

Observer читает только Git HEAD/status: optional index writes и fsmonitor hooks
отключены. Dirty state фиксируется без исправления. Файлы/секреты не выводятся;
не вызываются Git write commands, Python/game imports, SQLite, chown, compileall,
deploy или systemctl. WinSCP, повторный event, checkout и event во время preflight
требуют дальнейшего решения оператора и не управляют ботом.

Update service запускает non-root observer из стабильного root-owned пути:
ProtectSystem=strict, read-only project, inaccessible env/venv/DB/WAL/SHM,
PrivateNetwork/AF_UNIX, empty capabilities, NoNewPrivileges и другие sandbox
restrictions. PathModified явно перечисляет все каталоги app Python/JSON:
оно не рекурсивно. `.env`, DB, caches, venv, временные и control directories
не перечисляются. Observer не создаёт собственных events. Burst limit при
аномальном потоке прекращает watcher, а не перезапускает bot.

Preflight и startup повторно проверяют systemctl show: FragmentPath, effective
ExecStart/extra commands, User/WorkingDirectory, DropInPaths, NeedDaemonReload,
Paths/Unit/Triggers, hardening и states. Root ownership, ancestors, permissions,
symlinks, hashes observer/units/legacy alias/bot fragment также проверяются.
Нормализованный config hash не включает transient PID/время read-only oneshot.
Unit/helper/override drift после preflight инвалидирует gate; наличие безопасных
файлов в Git и зелёный CI не заменяют эту серверную проверку.

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
   Production HEAD не переключается. Отдельный clone OLD сохраняет диагностическую совместимость этапа B; rollback target берётся только из LKG.
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
   и строгая сохранность данных. Это только compatibility probe, не rollback. Отдельно создаются два checkout фактического LKG SHA и копии SOURCE/TARGET DB: initializer LKG должен сохранять схему и все строки строго. OLD compatibility не заменяет эти результаты.
9. На TARGET запускается общий `scripts/release_checks.py`: полный unittest,
   discovery preservation, release guard, diff --check, compileall, check_bot
   на другой disposable DB, все content/ability/tower validators и bash -n.
   Migration snapshot отделён от тестовой DB. Count определяется discovery;
   failures/errors/skips/expected failures/unexpected successes недопустимы.
10. Повторно проверяются OLD HEAD, чистота источника и состояния/PID/NRestarts
    production. Cleanup удаляет только собственный mkdtemp directory после
    завершения дочерних процессов, включая SIGTERM/error. PASS публикуется
    только после всех проверок и успешного cleanup.

Config/import probes выполняются с PYTHONDONTWRITEBYTECODE=1, чтобы не
создавать cache в watched production code или установленном venv.
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
rollback compatible true/false/unknown и hash защищённого log. Дополнительно: installed systemd binding, LKG binding и независимые source/target compatibility именно LKG SHA.

Validation требует owner/private permissions, regular files без symlinks,
checksums report/log, полный PASS, совпадение SHA/tooling и возраст не более
часа. RUNNING, NOT RUN, FAIL, отсутствующий/прерванный report недействительны.
Обычный deploy всегда проводит **новый** preflight; reuse/skip flag отсутствует.
Записи игроков не входят в условие актуальности evidence: перед checkout
проверяется SOURCE schema свежей live DB. После остановки содержимое сравнивается
со свежим final backup: последующие записи уже не считаются нормальным traffic.
Изменение OLD/TARGET/tooling/installed units/LKG требует нового preflight. Новый origin/main не
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
в `/opt/shpakdnd-backups`, до checkout. Проверяются SOURCE schema и полное
логическое совпадение backup/live. Защищённый журнал `<evidence>.deployment.json`
(0600, checksum, fsync файла и каталога) фиксирует SOURCE, SHA, tooling,
evidence hash, пути и hashes backup/логических данных, без пользовательских строк.
Второе подтверждение разрешает установить
только проверенный SHA: `git switch --no-overwrite-ignore -C main <SHA>`.
Ignored env/venv/DB защищены, dirty worktree и tracked production files запрещены.

Backup helper ограничен timeout 60 секунд (+5 на kill);
root runtime controller ограничивает дочернюю миграцию 50 секундами, при ошибке
или сигнале завершает всю группу процессов, с 3 секундами до SIGKILL;
Online Backup дополнительно имеет deadline 30 секунд.
После checkout выполняются только DB_PATH/SHA checks, init_db/init_mini_db/
init_boss_db и SQLite integrity/FK, clean worktree/evidence schema validation.
Перед дочерним initializer журнал **durably** фиксирует MIGRATION_STARTED.
TARGET фиксируется только после exit 0, строго TARGET schema, integrity/FK
и сохранности всех прежних строк/колонок относительно final backup. SOURCE
не принимается вместо отличающейся TARGET. Даже одинаковые schema hashes
не обходят initializer и сравнение данных. Сбой, timeout, SIGTERM или
неподтверждённый результат оставляет UNKNOWN; журнал STARTED после SIGKILL
также запрещает запуск. До запуска bot отдельно фиксируется риск новых записей.
Полный check_bot с каталогами, unittest, compileall и validators уже закончены
до downtime; они не запускаются при deployment или rollback после остановки.
Перед startup проверяется installed configuration. После подтверждения запускается bot; active/MainPID/NRestarts и свежий journal
проверяются до watcher и ещё раз после него. Fresh /proc process identity и весь invocation journal проверяются также до watcher, включая rollback. После bot startup read-only SQLite smoke проверяет integrity/FK, обязательные таблицы, миры и подтверждённую схему до watcher. После watcher выполняются дополнительные stable process/journal/smoke checks. Только здоровый bot + watcher разрешают финальный notice. LKG требует реальный semantic smoke и отдельный ввод полного SHA оператором. Ошибка финального notice — warning с сохранением
здорового состояния. EOF никогда не считается подтверждением.

## Last-Known-Good: формат и подтверждение

`/var/lib/shpakdnd-release/last-known-good.json` — root-only checksum envelope
с payload version=2/status=CONFIRMED (исторические C version=1 сохраняются), full SHA, UTC timestamp, deployment_id,
repository identity, tooling/systemd hashes, всеми результатами release checks,
health/process/operator proof, SQLite schema/integrity/FK/smoke и previous link.
История хранится в `history/<record hash>.json`; проверяются checksum текущей записи и связь с предыдущей записью.
Запись атомарна, fsync файла и каталога. Секреты и пользовательские SQLite
строки не записываются. Checksums обнаруживают повреждение; root и его private
каталоги являются границей доверия. Публичной команды `set-lkg <sha>` нет.

Checkout/runtime-init/is-active сами не меняют LKG. Для продвижения нужны:
актуальный exact-SHA release evidence, успешные TARGET migrations/preservation,
зафиксированный startup attempt, текущий exact clean checkout, свежий процесс
после checkout, совпадающие /proc cwd/cmdline и systemd InvocationID/start time,
стабильные PID/NRestarts, весь journal этой invocation без ошибок, installed
units, два health samples и read-only SQLite smoke. `.health.json` связывает
эти доказательства с evidence и DB operation journal. Confirmation действует
5 минут и повторно проверяет process/config/DB. Последний шаг — полный TARGET
SHA, введённый оператором, после bound real semantic proof и staging gameplay proof. Enter/EOF оставляет прежнюю LKG, даже если deployment
здоров. Повтор уже подтверждённой операции ничего не переписывает.

Failed deployment/rollback никогда не продвигает TARGET. До atomic replace
SIGTERM/SIGKILL сохраняет прежнюю запись; orphan `.new`/history не является LKG.
Повреждённый JSON/checksum/history, отсутствующий commit или другая repository
identity блокируют rollback. Historical LKG может отличаться от current HEAD;
это повод для двух независимых compatibility probes, не для перезаписи LKG.

## Первичная инициализация

Если LKG отсутствует, rollback запрещён. Нельзя объявлять current HEAD здоровым
по одному совпадению файлов/схемы. Для современного C-кода подтверждение происходит
через обычный полный deployment, health, real semantic smoke и operator confirmation.

Для существующей V1.4 есть только pinned baseline procedure:

```bash
# Только после одобрения, установки безопасных units и решения о stop/start.
sudo deploy-shpakdnd --initialize-lkg
```

Команда принимает исключительно full baseline
`f06c0d129fdad0fef4fde0889915faac26b94f4a` как текущий clean OLD/TARGET. Adapter
из установленного C bundle проверяет origin/fsck/exact code/catalog/test blobs
по immutable Git SHA, dependencies, реальный SQLite snapshot, migrations twice,
preservation, validators, shell syntax, compileall и полный discovery/unittest
именно baseline. Старый SHA не обязан содержать runner этапа A: используется
зафиксированный общий C runner/inventory/network guard, а baseline preservation
обоснован точным immutable SHA. Это отдельный явно помеченный evidence kind
PINNED_LEGACY_BASELINE; произвольный legacy HEAD не допускается. Unchanged baseline-тесты используют свою pinned topic configuration; общий network guard запрещает внешние API. Современный runner продолжает использовать test isolation этапа A.

Проверки идут до остановки. Далее действуют все обычные подтверждения, final
backup/SOURCE/TARGET journal и короткий контролируемый stop/start того же SHA.
Без такого startup невозможно доказать, что Python-процесс загрузил именно
эти исходники. После stable process/units/smoke checks оператор вводит полный
baseline SHA. До этого LKG отсутствует/UNCONFIRMED; ошибка или недостаточные
доказательства не дают безопасный rollback target. Codex эту процедуру на
production не запускает.

## Ошибки и rollback только к LKG

До остановки любой FAIL возвращает nonzero; healthy bot/watcher сохраняются,
notice/checkout/live migration отсутствуют. Исправить причину и повторить полный
preflight. После stop неисправный bot останавливается, watcher не активируется.
Оператор выбирает оставить всё остановленным либо явно разрешает возврат.
Сервисная конфигурация и LKG должны соответствовать frozen preflight binding.
Полный Git commit LKG должен существовать в том же repository.

| Состояние DB | Требования к rollback |
|---|---|
| SOURCE | migrations/startup не запускались; SOURCE schema и все данные совпадают с fresh final backup; LKG SOURCE compatibility=true для точного LKG SHA; integrity/FK проходят |
| TARGET | runtime exit 0, TARGET schema и preservation подтверждены; LKG TARGET compatibility=true для точного LKG SHA; текущее логическое содержимое совпадает с sealed TARGET state; integrity/FK проходят |
| MIGRATION_STARTED/UNKNOWN/FAILED | rollback/startup запрещены, даже при совпадении schema hash с SOURCE |

OLD preflight compatibility не разрешает LKG rollback, включая случай LKG≠OLD.
При false/unknown/missing compatibility возврат запрещён. Неизвестные изменения,
частичная миграция и новые live transactions после startup блокируют автоматизированный
возврат: схема одна не доказывает данные. Watcher/update останавливаются перед
checkout. Только проверенный LKG получает read-only config/SHA/DB checks и
startup; повторного initializer, полного unittest/validators после stop нет.
Startup+journal/smoke должны пройти до watcher и сообщения об успешном возврате.
Неудачный TARGET остаётся FAIL, прежняя LKG не меняется.

При UNKNOWN вручную сохранить evidence/journal/final backup/текущую DB и WAL,
разобрать реальные операции и транзакции на отдельных snapshots. Определить
совместимый код и отдельный план восстановления данных, получить новое решение
оператора. Не удалять journal и не подменять его фазу/схему; не восстанавливать
backup поверх live DB с возможными новыми транзакциями. Карантин units снимается
только проверенным installer; архив legacy configuration не является безопасным
recovery shortcut.

## Независимый Linux стенд и production rollout

Подробные последовательности и ограничения измерений —
[release-pipeline.md](release-pipeline.md#стенд-и-план-rollout).
Этап C фиксирует технические health/confirmation proofs; этап D дополнит итоговый
release report и семантический smoke. Game schema/catalog/balance не изменяются.


## Этап D: final gate и semantic release confirmation

Release report создаётся для одного полного SHA, JSON и Markdown из одного набора proofs:

```bash
python scripts/release_report.py report --project /path/to/reviewed/checkout \
  --sha <FULL_SHA> --output /private/reports/v1.4.1.json
```

Generator читает GitHub API (read-only gh authentication в том же root context для gate; credentials хранить отдельно от игровой .env, не выводить в logs), exact-SHA push jobs/log hashes, фактическую branch protection/effective rules и ancestry/clean checkout. Самоподтверждённые Tests: OK/PASS файлы не принимаются. Deployment/manual inputs принимаются только как protected checksummed records, созданные доверенными tooling/operator commands. Не передавать .env/token/SQLite/player rows в report. PASS содержит run/job ID+log hash, командный hash или protected evidence hash. NOT_RUN/UNKNOWN/STALE не становятся PASS; NOT_APPLICABLE требует явного обоснования.

Ключи readiness: merge_ready, deployment_start_ready, production_release_confirmed. Формальный статус: В разработке → Готово к тестовому стенду → Готово к объединению с main → Можно в прод. Последний требует проверенного actual main SHA, всех prerequisites и отдельного production decision. Он разрешает только начало процедуры; успешный production релиз до startup/real smoke/LKG утверждать нельзя.

Обычный manager после полного preflight и до notice/stop вызывает final gate. Требуются successful CI точного TARGET, actual required checks, полный real staging, independent review, production approval, SQLite/migrations/snapshot, actual installed units и LKG SOURCE/TARGET compatibility. Отсутствующий proof/gh/tooling/UNKNOWN DB блокирует maintenance. --skip-preflight или --force-release нет.

### Изолированный staging до merge

На независимой VM для final feature SHA используется `sudo deploy-shpakdnd --staging-sha <FULL_SHA>`. Он сохраняет полный preflight, lock, backup, SOURCE/STARTED/TARGET и startup checks. Разрешён только при root-owned approved runtime hook, effective bot Environment=SHPAKDND_STAGING_ONLY=1, независимом project path (не /opt/shpakdnd-bot) и полном exact-SHA CI. Это не production разрешение. Отсутствующая initial LKG явно означает недоступный rollback; оператор работает только с disposable staging DB.

Runtime topic injection: root создаёт отдельный `/srv/shpakdnd-stage-runtime`, копирует exact reviewed `deploy/staging_topics.py`, создаёт `sitecustomize.py` с **ровно** `from staging_topics import install; install()`, и root-owned readable config `{ "chat_id": <NEGATIVE_STAGING_GROUP>, "thread_id": <POSITIVE_TOPIC> }`. Paths вне checkout, permissions без group/world write. Bot unit на VM задаёт PYTHONPATH, SHPAKDND_STAGING_ONLY=1, SHPAKDND_STAGING_TOPICS=/srv/.../topics.json и PYTHONDONTWRITEBYTECODE=1. Effective config/helper/hook/topics hashes входят в manifest. Production gate отвергает такой runtime. Injection меняет только topic settings на стенде, не игровые правила; production configured group запрещена. Ни одного второго polling с production token.

Full gameplay/fault matrix, privacy-safe artifacts, scope GAMEPLAY/FULL_STAGING и read-only production cases: [staging-v1.4.1.md](staging-v1.4.1.md). Все реальные сценарии до выполнения оператором NOT_RUN.

### Реальный postdeploy smoke и LKG

Automatic import/router/catalog/SQLite smoke работает на независимом Online Backup и exact archive с dummy token, query_only SQLite и запрещённой сетью/polling. Он не меняет live DB, не создаёт attempts и не доказывает Telegram API/UI. Proof kind=AUTOMATIC_READONLY_SMOKE; технический postdeploy сохраняет .automatic-smoke.json и protected probe log.

Real smoke требует operator-observed UI artifacts по конкретным read-only cases + реальный getMe. getMe один не подтверждает UI. Full gameplay/платные сценарии — только staging, production только безопасные меню/просмотр/права/старый read-only callback. SHA/deployment/process/config/staging hashes и срок (30 минут, staging 7 суток) обязательны; новый SHA или другой deployment требует новых proofs.

После startup без real semantic proof manager сохраняет healthy code и прежнюю LKG, сообщает RELEASE_UNCONFIRMED; это не полностью подтверждённый релиз. EOF/отказ тоже сохраняет прежнюю LKG. Вывод/`.outcome.json` показывает actual TARGET/checkout, current LKG SHA/status, promotion/reason, DB stage, SOURCE/TARGET compatibility, доступность rollback и нужное действие оператора. UNKNOWN/failed state не объявляется running/PASS.

После реальных наблюдений оператор использует **установленный versioned bundle** под тем же FD9 lock; пути и SHA берутся из protected preflight, а не подставляются произвольно:

```bash
# Выполнять только после отдельного решения оператора. Ниже TEMPLATE.
sudo bash -c 'exec 9>>/run/lock/shpakdnd-deploy.lock; flock -n 9 || exit 1
  PYTHON=/opt/shpakdnd-bot/.venv/bin/python
  TOOLS=/usr/local/lib/shpakdnd-deploy/versions/<INSTALLED_TOOLING>
  EVIDENCE=/var/lib/shpakdnd-preflight/<EXACT_PREFLIGHT>.json
  OLD=<FULL_OLD_SHA>; TARGET=<FULL_TARGET_SHA>
  "$PYTHON" "$TOOLS/semantic_evidence.py" postdeploy --project /opt/shpakdnd-bot --sha "$TARGET" \
    --environment production --checklist /var/lib/shpakdnd-release/readonly-checklist.json \
    --staging /var/lib/shpakdnd-release/staging.json --evidence "$EVIDENCE" --output "${EVIDENCE%.json}.semantic.json" || exit 1
  "$PYTHON" "$TOOLS/release_lkg.py" health --evidence "$EVIDENCE" --project /opt/shpakdnd-bot --db /opt/shpakdnd-bot/shpakdnd.db \
    --old "$OLD" --target "$TARGET" --since "$(date -u +%Y-%m-%dT%H:%M:%SZ)" || exit 1
  "$PYTHON" "$TOOLS/release_lkg.py" confirm --evidence "$EVIDENCE" --project /opt/shpakdnd-bot --db /opt/shpakdnd-bot/shpakdnd.db --old "$OLD" --target "$TARGET"
  "$PYTHON" "$TOOLS/release_lkg.py" status --evidence "$EVIDENCE" --project /opt/shpakdnd-bot --db /opt/shpakdnd-bot/shpakdnd.db --old "$OLD" --target "$TARGET"'
```

Attester и confirm отдельно требуют full SHA. Обновление health не допускает смену process/invocation для прежнего semantic proof. LKG version=2 содержит semantic/source/hash и completed_preflight_hash. Старые confirmed C version=1 остаются историческими rollback records, но не позволяют новое D promotion без semantic. Completed-report binding используется только для чтения уже confirmed операции; runtime CLI не имеет обходного флага и не сбрасывает journal.

### Independent review и production decision

После фактического независимого ревью root оператор сохраняет private review artifact и выполняет `release_report.py attest-review --project ... --sha FULL_SHA --artifact /private/actual-review.md --output /var/lib/shpakdnd-release/review.json`. Команда не утверждает, что ChatGPT review состоялось самостоятельно: нужен существующий artifact и explicit SHA оператора. `attest-production` аналогично фиксирует отдельное решение пользователя в production-decision.json. Не создавать эти proofs заранее ради зелёного отчёта. Generator только читает их hashes/source, без приватного содержимого.

### Required checks и squash merge

Фактический audit 2026-10-08: main требует только Linux release checks. systemd-staging и legacy-baseline **не required**; merge readiness заблокирована. PR/1 approval/dismiss stale reviews/strict up-to-date/enforce admins/no force push/no deletion включены, effective rulesets отсутствуют. Настройки Codex не менял.

Read-only audit: `gh api repos/Aruzento/shpakdnd_bot/branches/main/protection` и `gh api repos/Aruzento/shpakdnd_bot/rules/branches/main`. Если API denied — UNKNOWN/NOT VERIFIED. Оператор в GitHub Settings → Branches/Rulesets → main проверяет PR+approval, exact required names Linux release checks/systemd-staging/legacy-baseline, strict up-to-date, dismiss stale reviews, no force push/deletion и отсутствие bypass actors/admin bypass. Изменение settings требует отдельного решения пользователя.

Оба workflows теперь выполняются и на push main. Squash/rebase merge создаёт новый SHA: feature CI/staging/review не автоматически подтверждают merged code. После merge повторить все Linux gates для exact main SHA, report, real staging proofs/review applicability и fresh production preflight; только затем отдельное production decision. Codex merge не выполняет.

### Ошибки и ручное восстановление

До stop FAIL final gate сохраняет bot/HEAD/DB. После stop действуют C SOURCE/TARGET/UNKNOWN и LKG-only consent rollback. Failed Telegram API/semantic не превращает unsafe DB в безопасную и не меняет LKG. Healthy running code без promotion указывается отдельно, watcher остаётся read-only. UNKNOWN/STARTED/изменённые данные/нет compatible LKG — stop и private forensic snapshots/journal/WAL; никакого автоматического backup restore.

Manual recovery фиксировать отдельным incident artifact (actual SHA, UTC, identity, причины, snapshots/log hashes, принятое пользователем решение, фактические операции и новый verification результат), не менять phase/checksum для обхода. После recovery нужны новые exact-SHA preflight/health/semantic/operator proofs.

### Pinned legacy initialization после этапа D

`--initialize-lkg` сохраняет отдельный `gate-legacy-bootstrap`, только для immutable V1.4 SHA. Он не присваивает V1.4.1 release readiness: проверяет свежий строгий PINNED_LEGACY_BASELINE preflight, все 979 старых тестов/validators, actual SOURCE SQLite, installed units и exact-SHA CI **установленного D tooling**. V1.4 не обязан иметь отсутствовавшие A workflows. Наличие уже confirmed LKG запрещает повторную initial override. Требуются actual required CI enforcement, independent review/explicit operator bootstrap decision для baseline SHA и GAMEPLAY staging proof именно baseline. Полная infra matrix с initial LKG была бы circular; она завершается после первого успешного подтверждения. Обычный V1.4.1 deployment продолжает требовать полный final gate, LKG compatibility и всю FULL_STAGING matrix.

До выполнения этих условий initialization остаётся заблокированной. После согласованного stop/start fresh backup и B journal обязательны; только real postdeploy semantic + technical health + полный baseline SHA публикуют initial LKG. Это не разрешение Codex выполнять операцию на production.

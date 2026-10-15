# D&D Mini V1.4.1: официальный операторский staging checklist

**Начальный статус всех сценариев: NOT_RUN.** Codex не имеет отдельного Telegram BOT_TOKEN/группы и Linux VM с игровым ботом. Mock/fake/systemd dummy CI не подтверждают этот checklist.

## Изоляция и точный SHA

Использовать отдельную Linux VM: собственные bot token, группа/тема, пользователь, Git checkout, venv, SQLite, units, backups/evidence/LKG. Не монтировать production DB/token/systemd socket. Полный reviewed SHA записать в переменную STAGE_SHA; не использовать плавающую ветку после начала проверки.

```bash
STAGE_SHA=<FULL_REVIEWED_SHA>
git clone https://github.com/Aruzento/shpakdnd_bot.git /srv/shpakdnd-stage
git -C /srv/shpakdnd-stage switch --detach "$STAGE_SHA"
git -C /srv/shpakdnd-stage rev-parse HEAD
git -C /srv/shpakdnd-stage status --porcelain
python3.13 -m venv /srv/shpakdnd-stage/.venv
/srv/shpakdnd-stage/.venv/bin/python -m pip install -r /srv/shpakdnd-stage/requirements.txt
# .env создать вручную с НОВЫМ staging token, права 0600; не вставлять token в CLI/log.
```

Текущая app/topics.py привязана к production теме. Не править отслеживаемый файл и не переносить production token. Staging использует root-owned runtime topic injection вне checkout (`deploy/staging_topics.py`); это контролируемая test dependency только для isolated_staging, отдельно hash-bound в manifest. Она заменяет исключительно TOPIC_SETTINGS, не баланс/боевые формулы/награды. Установить hook и private topics JSON с настоящими staging chat/thread IDs по инструкции helper. Bot unit на VM должен включать Environment=SHPAKDND_STAGING_ONLY=1 и PYTHONPATH для этого runtime. Перед игровыми действиями getMe и экран бота должны подтвердить отдельную identity и правильную тему. Если настройка темы/identity не проверена — UNKNOWN, polling не запускать.

Из root-owned отдельной reviewed копии установить tooling/observer по docs/deployment.md. Сначала весь Linux CI exact SHA, потом только явное решение оператора на VM о запуске игрового staging bot. Прямой тестовый startup допустим лишь в isolated VM; он не является подтверждением LKG или production deployment.

## Запись результатов

Для **каждого** ID ниже заполнить initial, action, expected, actual, status (PASS/FAIL/NOT_RUN), artifact (абсолютный путь приватного обезличенного лога/скриншота/ledger-diff), sha, date UTC и executor. Ни одна пустая строка не означает PASS. Random win/loss воспроизводить только на тестовой DB штатными fixtures/controlled random dependencies; production правила не изменять.

| ID | Исходное состояние | Действие | Ожидаемый результат | Actual/status/evidence/SHA/date/executor |
|---|---|---|---|---|
| menu | Зарегистрированный тестовый игрок, открыта staging-тема | Открыть launcher, переходы и персональные меню | Ответы в правильной теме; чужие персональные кнопки отвергаются | NOT_RUN — заполнить оператором |
| events-rps | Тестовый Wallet зафиксирован | Ставка, выбор, победа/поражение/ничья обеих сторон | Один session/ledger, списание и выплата по действующим правилам | NOT_RUN — заполнить оператором |
| events-lab | Тестовый баланс и новая сессия | Войти, пройти/проиграть лабиринт | Стоимость, награда/осколки соответствуют каталогу; повтор не платит | NOT_RUN — заполнить оператором |
| events-persistence | Активная Events session | Остановить/запустить только staging процесс, продолжить | Та же сессия/ставка сохранены; нет повторного списания | NOT_RUN — заполнить оператором |
| gacha | Тестовые ресурсы и коллекция | Призыв, дубликат, просмотр коллекции | Баланс/осколки/коллекция корректны; эксклюзивы не попадают в обычную гачу | NOT_RUN — заполнить оператором |
| shadow-mythic | Тестовые условия доступности подготовлены штатными сервисами | Проверить действующие условия получения и недоступность обычного призыва | Эксклюзивные правила соблюдены без изменения каталогов | NOT_RUN — заполнить оператором |
| boss | Создана только staging регистрация | Выбрать героя, начать, выполнить ход, завершить бой | Snapshots/порядок/награды/завершение сохранены | NOT_RUN — заполнить оператором |
| combat-v2 | Staging бой с известными героями/боссом | Проверить способности, пассивки, фракции, теги | Фактический результат совпадает с существующими правилами | NOT_RUN — заполнить оператором |
| tower | Известный этаж и выбранный герой | Пройти этаж и отдельно проиграть | Progress/rewards/поражение сохранены без двойных выплат | NOT_RUN — заполнить оператором |
| equipment | Тестовый инвентарь и несколько слотов | Экипировать, посмотреть общий бонус, перезапустить staging | Слоты, бонус и инвентарь сохранены | NOT_RUN — заполнить оператором |
| village | Известные постройки и время производства | Просмотреть/собрать производство на тестовой DB | Накопления и операции соответствуют времени, повтор защищён | NOT_RUN — заполнить оператором |
| duels | Два согласившихся тестовых пользователя | Создать, завершить, повторить callback | Один исход/ledger; повторы не создают выплат | NOT_RUN — заполнить оператором |
| wallet | Баланс и ledger до сценария сохранены приватно | Выполнить staging списание/начисление и прочитать историю | Точная дельта/ledger совпадают, без двойных операций | NOT_RUN — заполнить оператором |
| admin | Обычный пользователь и администратор в нужной/чужой теме | Проверить разрешённые и запрещённые команды | Права и chat/topic scope соблюдены | NOT_RUN — заполнить оператором |
| telegram-ui | Публикация, ephemeral экран и старое сообщение | Открыть, закрыть, нажать устаревшие/чужие callbacks | Доставка/очистка/безопасная обработка без неизвестных исключений | NOT_RUN — заполнить оператором |
| double-click | Приватно сохранены balances/session/progress и journal; только staging | Повторить одну и ту же кнопку | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| concurrent-actions | Приватно сохранены balances/session/progress и journal; только staging | Одновременно выполнить два действия одного игрока | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| stale-callback | Приватно сохранены balances/session/progress и journal; только staging | Нажать кнопку завершённой сессии | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| telegram-error | Приватно сохранены balances/session/progress и journal; только staging | Контролируемо заблокировать Telegram endpoint только staging, затем восстановить | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| boss-restart | Приватно сохранены balances/session/progress и journal; только staging | Restart staging во время Boss | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| events-restart | Приватно сохранены balances/session/progress и journal; только staging | Restart staging во время Events | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| wallet-restart | Приватно сохранены balances/session/progress и journal; только staging | Restart staging после ledger списания | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| duel-restart | Приватно сохранены balances/session/progress и journal; только staging | Restart staging незавершённого Duel | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| tower-recovery | Приватно сохранены balances/session/progress и journal; только staging | Restart и восстановить Tower progress | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| inventory-restart | Приватно сохранены balances/session/progress и journal; только staging | Restart при существующей экипировке/инвентаре | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| wrong-user-topic | Приватно сохранены balances/session/progress и journal; только staging | Нажать как другой пользователь/из другой темы | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| access-denied | Приватно сохранены balances/session/progress и journal; только staging | Выполнить admin действие без прав | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| db-error | Приватно сохранены balances/session/progress и journal; только staging | Закрыть доступ staging user к тестовой DB, проверить отказ, восстановить права | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| duplicate-process | Приватно сохранены balances/session/progress и journal; только staging | Попытаться запустить второй экземпляр только на стенде; остановить его до polling | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |
| no-double-payout | Приватно сохранены balances/session/progress и journal; только staging | Повторить завершённые денежные операции и сверить ledger | Нет двойных выплат/потери прогресса, права соблюдены, ошибка диагностируется и не превращается в PASS | NOT_RUN — заполнить оператором |

## Infrastructure matrix на той же независимой VM

| ID | Исходное состояние / действие | Ожидаемое / обязательное evidence | Actual/status/evidence/SHA/date/executor |
|---|---|---|---|
| bootstrap | Root-owned reviewed checkout; install-deploy-shpakdnd.sh FULL_SHA | Pinned source/blobs/syntax/one lock/atomic command; live game не изменена | NOT_RUN — заполнить оператором |
| units | Здоровый staging bot; install-systemd-units.sh FULL_SHA | Safe helper/unit hashes/effective properties, PID не меняется | NOT_RUN — заполнить оператором |
| observer | Изменить Python/JSON тестовой копии, затем вернуть clean exact SHA | Journal event, no restart/no SQLite write; repeated event не deploy | NOT_RUN — заполнить оператором |
| preflight | Full --preflight при active bot | Exact TARGET snapshot/WAL/migration/preservation, tests до stop | NOT_RUN — заполнить оператором |
| preflight-failure | Fault-inject тест/validator на отдельной проверяемой fixture | Nonzero; bot/HEAD/DB unchanged; no maintenance | NOT_RUN — заполнить оператором |
| deployment | Контролируемый staging deployment точного SHA | Final gate → consent → stop → backup → checkout → STARTED/TARGET → startup | NOT_RUN — заполнить оператором |
| downtime | Замерить монотонные timestamps до stop и после здоровья bot | Фактический downtime и watcher interval, без фиксированной обещанной длительности | NOT_RUN — заполнить оператором |
| backup | Создать fresh backup после stop | Online Backup integrity/FK и checksum до checkout | NOT_RUN — заполнить оператором |
| runtime-init | Runtime initialization только staging DB | TARGET/exit0/data preservation; partial failure UNKNOWN | NOT_RUN — заполнить оператором |
| startup | Запустить проверенный код | Fresh /proc, PID/NRestarts/InvocationID stable | NOT_RUN — заполнить оператором |
| journal | Прочитать ВЕСЬ journal invocation | Нет необъяснённых ERROR/CRITICAL/restarts; ошибки сохраняются | NOT_RUN — заполнить оператором |
| initial-lkg | Gameplay evidence → technical health → real readonly smoke → полный SHA | LKG только с proofs; первый этап gameplay scope без recovery разрешён | NOT_RUN — заполнить оператором |
| compatible-rollback | Failed TARGET + exact compatible LKG + explicit consent | SOURCE/TARGET policy, correct LKG checkout/startup, failed target не promoted | NOT_RUN — заполнить оператором |
| incompatible-rollback | LKG несовместима / UNKNOWN DB | Rollback блокирован, backup не восстанавливается | NOT_RUN — заполнить оператором |
| kill-reboot-repeat | Убить deployment/installer до publication, reboot/retry | Нет ложного PASS/LKG, quarantine сохраняется, lock/repeats безопасны | NOT_RUN — заполнить оператором |

## Attestation: gameplay, полный staging и production read-only

Создать пустой NOT_RUN template: `sudo python semantic_evidence.py staging --template --project /srv/shpakdnd-stage --sha FULL_SHA --environment isolated_staging --checklist /var/lib/shpakdnd-release/staging-checklist.json --output /var/lib/shpakdnd-release/staging.json`. Никакого PASS/LKG это не создаёт.

Private checklist JSON хранится вне checkout. Формат: sha, environment_kind=isolated_staging, environment_id (opaque label), source=operator_observed_real_telegram, operator_sha, cases keyed ровно всеми ID таблиц. Case содержит initial/action/expected/actual/status/artifact/date/executor. Не включать токены и сырые личные данные. Artifacts root-owned 0600; каталог 0700. Attester сохраняет только hashes, ID, timestamp/status, а не actual/player text.

До первой LKG можно завершить GAME_CASES+FAULT_CASES, указав ещё не выполнимые infrastructure cases NOT_RUN. Это **GAMEPLAY scope**, не пройденный полный стенд/merge gate. После init/recovery дополнить полный checklist; лишь все 45 ID=PASS дают FULL_STAGING. Promotion требует подтверждённых затронутых игровых функций, а merge readiness — полного стенда.

```bash
sudo python3 /usr/local/lib/shpakdnd-deploy/versions/<tooling>/semantic_evidence.py staging   --project /srv/shpakdnd-stage --sha "$STAGE_SHA" --environment isolated_staging   --checklist /var/lib/shpakdnd-release/staging-checklist.json   --output /var/lib/shpakdnd-release/staging.json
```

Команда проверяет root-private artifacts, SHA/срок/case completeness и настоящий read-only getMe. Ввести полный SHA после реальных наблюдений. Ошибка API, EOF, пропущенный case или mock не подтверждают smoke. Attestation не меняет LKG.

Postdeploy production **без ставок/платных действий**: только telegram-launcher, personal-navigation, events-both-menus, collection-equipment-progress, wallet-history-readonly, world-topic-access, ephemeral-stale-callback. Не вызывать Gacha/Boss/Duel/Tower attempt/Event ставки ради аттестации. Старый callback брать из безопасного read-only меню, не денежного действия. Оператор записывает реальные доставку/ответы; getMe один не доказывает UI.

После технического health сохранить .semantic.json с kind=REAL_TELEGRAM_POSTDEPLOY, deployment ID/evidence hash/process/systemd/tooling/staging hashes. Использовать semantic_evidence.py postdeploy под тем же FD9 lock; затем refresh health и release_lkg.py confirm (полный SHA). Staging token и production identity должны отличаться. Смена SHA/deployment/process, expired evidence или failed getMe блокируют LKG.

## Решение

Все реальные scenarios и infrastructure на VM до выполнения — NOT_RUN. Полный стенд, independent review и required checks нужны для merge. После squash merge SHA новый: повторить Linux release gate и staging/attestations для нового SHA, затем отдельно принять production decision. Read-only production smoke выполняется после фактического запуска, до новой LKG. Не называть branch CI или dummy systemd успешным production релизом.

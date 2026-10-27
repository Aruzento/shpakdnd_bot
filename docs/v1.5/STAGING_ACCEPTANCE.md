# D&D Mini V1.5 — приёмка существующего TEST VM-338333

**В разработке. План и инструменты подготовки; игровые кейсы ниже ещё не пройдены.**
Владелец 27.10.2026 разрешил замену TEST, остановку прежнего тестового бота и удаление
только доказанно устаревших TEST-копий после успешного запуска новой версии.
Production, main и production rollback исключены. PR #8 остаётся Draft.

## Два точных SHA и границы работ

- **Приложение TEST:** `d795df06207d3f6927dee3378be7cf02fc25b93e` (APP_SHA).
- **Инструменты/инструкции:** TOOLING_SHA из окончательного отчёта этой итерации.
  Не подменять APP_SHA непроверенным HEAD. В APP_SHA нет нового launcher: извлекать
  его отдельно из TOOLING_SHA, сверять Git blob/SHA-256 и хранить root-owned вне checkout.
- Игровой `app` tree: `64823c903daa7e3cea5d12613f364da67f630a54`.
  Каталоги/экономика/PNG/авторские JSON не изменяются.
- 79 героев **всего**, 78 `active=1`: `panic_dungeon_engineer` из прежнего каталога
  намеренно `active=0`. Не исправлять данные ради ожидания «79 active».
- Подтверждено владельцем: legacy бои используют frozen 14400 секунд; новые V1.5 —
  900 секунд. Условные замечания об ожидании 4 часов в сохранённых сценариях B2 ниже
  закрыты этим решением; механика не меняется.

## Сведения владельца и обязательная свежая проверка

VM-338333, 88.214.54.194, root SSH с паролем. 27.10.2026 владелец установил временный restrict public key; SSH root/hostname
VM-338333 проверены. Ключ после завершения работ удалить точечно. Пароль не запрашивать/не записывать,
пользователей и ключи не подбирать. Однократная установка публичного ключа выполняется
владельцем вручную; после работ временный доступ удалить отдельно.

Владелец сообщил: активен `shpakdnd-bot.service`, WorkingDirectory
`/opt/shpakdnd-v144-stage`, detached SHA `94669086821fcb41c8349748a30724e24f88d893`,
изменён `app/topics.py`. SQLite `.../shpakdnd.db` — **symlink** на
`/opt/shpakdnd-bot/shpakdnd.db`. В `/opt/shpakdnd-bot` есть другие локальные изменения.
Сохранённый конфиг `/etc/shpakdnd-staging/topics.py`, watcher disabled/inactive.
Свежая проверка подтвердила SHA/процесс/пути/Python 3.11.2; выявлено отличие:
активный topics.py содержит только Mini scope, saved topics.py дополнительно содержит
утверждённый D&D scope. Mini settings идентичны, обе группы и членство существующего
@shpakdnd_test_bot (ID 8830916110) подтверждены read-only Bot API. Webhook пуст.
Collector сохраняет actual existing_scopes отдельно от saved_scopes/saved_scope_additions;
не выдаёт saved D&D за уже активную настройку. Любое иное отличие/production scope — STOP.

### Шаг 1 — read-only, до изменения сервера

В root-сессии, без вывода env/token/unit Environment/ExecStart в общий лог:

```bash
set -euo pipefail
[ "$(hostname)" = VM-338333 ] && [ "$(id -u)" = 0 ] || exit 1
systemctl show shpakdnd-bot.service -p ActiveState -p SubState -p MainPID \
  -p User -p WorkingDirectory -p FragmentPath -p Triggers -p PartOf -p Requires
systemctl list-units --all --no-pager 'shpakdnd*'
systemctl list-unit-files --no-pager 'shpakdnd*'
readlink -- /opt/shpakdnd-v144-stage/shpakdnd.db
readlink -f -- /opt/shpakdnd-v144-stage/shpakdnd.db
for d in /opt/shpakdnd-v144-stage /opt/shpakdnd-bot; do
  test -d "$d" && test ! -L "$d" || exit 1
  /usr/sbin/runuser -u shpakbot -- git --no-optional-locks -C "$d" rev-parse HEAD
  /usr/sbin/runuser -u shpakbot -- git --no-optional-locks -C "$d" status --short --untracked-files=no
done
# Просматривать unit, env и полный список файлов приватно: возможны секреты.
# Не применять git reset/clean и не выполнять deploy-shpakdnd.
```

Из проверенного TOOLING_SHA извлечь `deploy/staging_inventory.py` во временный
root-only каталог; сверить байты с Git и SHA-256. Запустить от имени фактического User TEST unit (`runuser -u shpakbot --
/opt/shpakdnd-bot/.venv/bin/python ...`), сохранить stdout в private root JSON.
Это сохраняет Git ownership guard без global safe.directory; root проверяет остальные
процессы/cgroup/контейнеры отдельно. Не менять global Git trust settings. Сборщик проверяет hostname,
unit/EnvironmentFiles без вывода секретов, единственный polling, точные scopes,
локальные/saved topics и известный legacy symlink. **SQLite не открывает**.
Любая ошибка — exit 2, не «условный PASS».

Дополнительно приватно проверить все процессы Python, unit/drop-in и контейнеры,
планировщики, watcher; сборщик распознаёт именованные bot.py/staging-launch.py,
но не доказывает отсутствие произвольного `python -c`, контейнера или remote polling.
Нельзя запускать новый бот до сверки getMe именно существующего TEST-токена,
отсутствия production bot ID и участия этого бота только в тестовых группах.
getUpdates для проверки не вызывать: он конкурирует с polling. Проверить webhook
через getWebhookInfo без изменения состояния. Не печатать URL API с token.

Подтверждаемые TEST scopes: Mini `-1003883723333/2`, D&D `-5320347021/0`.
Запрещены production chats `-1003376315265`, `694384548` и любые неизвестные scopes.
Сохранить приватно текущие .env, unit/drop-ins, локальный topics.py, saved topics.py,
CSV/editorial настройки и git diff; права 600/700, hashes в публичный отчёт без секретов.
Наличие файла в /opt само по себе не доказывает принадлежность TEST.

### Шаг 2 — один консистентный backup TEST SQLite

После проверки, что разрешённый источник — именно TEST DB, сделать **один** SQLite
online backup до остановки бота. Не `cp` файла при работающем WAL и не копировать
только symlink. В root-only каталоге backup сохранить snapshot, SHA-256, время начала
и конца, resolved source, integrity=ok и FK=[]. Никогда не затрагивать production.
Пример после проверки путей и свободного места:

```bash
BACKUP=/var/backups/shpakdnd-test-v15
[ "$(readlink -f /var/backups)" = /var/backups ] && [ ! -L "$BACKUP" ] || exit 1
install -d -m 700 -o root -g root "$BACKUP"
# Переменная TEST_BACKUP — новый уникальный путь, не существующий файл.
export TEST_BACKUP="$BACKUP/test-before-v15-$(date -u +%Y%m%dT%H%M%SZ).db"
python3 - <<'PY'
import os, sqlite3
from contextlib import closing
from pathlib import Path
source = Path('/opt/shpakdnd-v144-stage/shpakdnd.db')
expected = Path('/opt/shpakdnd-bot/shpakdnd.db')
if source.resolve(strict=True) != expected or expected.is_symlink():
    raise SystemExit('BLOCKED_DB_SOURCE')
target = Path(os.environ['TEST_BACKUP'])
if target.parent != Path('/var/backups/shpakdnd-test-v15') or target.exists() or target.is_symlink():
    raise SystemExit('BLOCKED_BACKUP_PATH')
fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
try:
    with closing(sqlite3.connect(expected.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, sleep=0.05)
            if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or dst.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('BLOCKED_BACKUP_INTEGRITY')
except Exception:
    print('BACKUP_FAILED_KEEP_FOR_DIAGNOSIS')
    raise
print('BACKUP_INTEGRITY_OK_FK_EMPTY')
PY
sha256sum -- "$TEST_BACKUP"
```

Старый бот может сделать тестовые ходы после snapshot и до stop: зафиксировать cutoff,
последующие изменения остаются в **нетронутой старой DB**, не переносить их в новую.
При ошибке backup не останавливать текущий TEST. Это не backup production copy.

## Подготовка чистого checkout до остановки старого TEST

Команды root после проверки hostname. Не использовать глобальный production installer.
Не продолжать при уже существующем STAGE: сначала инвентаризация, а не rm -rf.
Пользователь shpak-stage должен быть отдельным системным пользователем без доступа
к старым секретам/DB; если уже существует, сверить uid/groups/home/shell, не менять слепо.

```bash
APP_SHA=d795df06207d3f6927dee3378be7cf02fc25b93e
STAGE=/srv/shpakdnd-v15-staging
CODE="$STAGE/code"
[ "$(hostname)" = VM-338333 ] && [ "$(id -u)" = 0 ] || exit 1
# Если отсутствует: useradd --system --home-dir "$STAGE" --shell /usr/sbin/nologin shpak-stage
id shpak-stage
[ "$(readlink -f /srv)" = /srv ] || exit 1
[ ! -e "$STAGE" ] && [ ! -L "$STAGE" ] || exit 1
install -d -o root -g shpak-stage -m 750 "$STAGE"
install -d -o root -g shpak-stage -m 750 "$STAGE/config" "$STAGE/locks"
install -d -o shpak-stage -g shpak-stage -m 750 "$CODE" "$STAGE/logs" "$STAGE/runtime" "$STAGE/runtime/editorial"
install -d -o root -g shpak-stage -m 750 "$STAGE/stories"
# Каталоги существуют ДО clone; code пуст и доступен непривилегированному пользователю.
runuser -u shpak-stage -- git clone --no-checkout https://github.com/Aruzento/shpakdnd_bot.git "$CODE"
runuser -u shpak-stage -- git -C "$CODE" checkout --detach "$APP_SHA"
test "$(git -c safe.directory="$CODE" -C "$CODE" rev-parse HEAD)" = "$APP_SHA"
runuser -u shpak-stage -- python3 -m venv "$CODE/.venv"
runuser -u shpak-stage -- "$CODE/.venv/bin/python" -m pip install -r "$CODE/requirements.txt"
install -o root -g shpak-stage -m 440 "$CODE/content/stories/unpublished/tayna_propavshey_ovtsy/tayna_propavshey_ovtsy.json" "$STAGE/stories/tayna_propavshey_ovtsy.json"
printf '%s  %s\n' f185595d8c89fc1644e331a4ff1ae34c6df61920b4a69c9948654cd60903cce2 "$STAGE/stories/tayna_propavshey_ovtsy.json" | sha256sum -c -
```

Скопировать **только проверенные TEST** настройки в новый приватный `.env` 600,
owner shpak-stage. Тот же TEST BOT_TOKEN, BOT_TIMEZONE и ровно отдельные пути:

```dotenv
STORIES_DIR=/srv/shpakdnd-v15-staging/stories
MINI_EDITORIAL_DIR=/srv/shpakdnd-v15-staging/runtime/editorial
DND_SHOP_CSV_PATH=/srv/shpakdnd-v15-staging/runtime/shop.csv
DND_LOOT_CSV_PATH=/srv/shpakdnd-v15-staging/runtime/loot.csv
DND_SHOP_CODE_MAP_PATH=/srv/shpakdnd-v15-staging/runtime/loot_code_map.csv
DND_SHOP_OFFERS_PATH=/srv/shpakdnd-v15-staging/runtime/shop_offers.csv
```

Никаких DB symlink/copy: `shpakdnd.db` и `timers.db` создаются непосредственно в CODE.
Нельзя перенести outbox/таймеры/пользователей старой DB или production copy в gameplay TEST.
CSV при необходимости копировать из проверенного TEST, проверять scopes через тот же
внешний adapter, не выполнять publish из canonical production app.topics.

Свежая SQLite не содержит конфигурации обычного D&D-магазина. На проверенном TEST
существующий CSV использует `pricing_mode=m`, `factor=1/10000`, settings_version=2
и 15 зарегистрированных денежных номиналов. Сохранить эти существующие метаданные
из консистентного TEST backup, включая явное `shop_currency_units.master`; не
придумывать номиналы и не переписывать CSV для обхода проверки. Регистрировать
валюты штатным `register_in_transaction`, сохраняя их business code и номинал,
а новые item_id и currency_signature получать через `pricing.preflight` и
`control.persist_state`. Балансы, инвентари игроков, историю, outbox и старые ID
не импортировать. Затем штатные `app.dnd_shop.cli validate/publish` через adapter
должны пройти на новой SQLite, с неизменными SHA-256 четырёх CSV. Отказ проверки
не является разрешением сбросить цены/номиналы: текущий TEST продолжает работать,
пока совместимость не подтверждена.

### Внешний launcher и preflight без polling

Root извлекает из **проверенного TOOLING_SHA** `deploy/staging_launcher.py` и
`deploy/staging_inventory.py`, сравнивает Git blob bytes/hash, устанавливает первый
в `STAGE/config/staging-launch.py` 440 root:shpak-stage. Родители config/locks root-owned,
не writable для бота; .env writable владельцу бота, но его SHA-256 привязан к root policy.
Не заменять source `app/topics.py`. Launcher очищает/обновляет **тот же объект**
TOPIC_SETTINGS до imports app.context/worlds/handlers; это проверено реальными imports
bot.py и многократным check_bot.py на синтетическом checkout APP_SHA.

Root-only просмотр старого topics через AST literal_eval (не exec) →
`STAGE/config/topic-settings.json` 440 root:shpak-stage. JSON содержит исходные TEST
chat/thread, admin/characters, mini и дополнительные известные поля; сохраняет имена.
Точная allowlist исключает production. Сохранённый `/etc/.../topics.py` должен совпадать по каждому активному Mini scope;
единственное допустимое расширение — уже утверждённый D&D scope -5320347021/0.

Root policy `STAGE/config/policy.json` 440 содержит **ровно**:

```json
{
  "schema_version": 1,
  "mode": "replace_existing_test",
  "candidate_sha": "d795df06207d3f6927dee3378be7cf02fc25b93e",
  "tooling_sha": "<40_HEX_FROM_FINAL_REPORT>",
  "env_sha256": "<SHA256_NEW_PRIVATE_ENV>",
  "topics_sha256": "<SHA256_PRIVATE_TOPIC_JSON>",
  "launcher_sha256": "<SHA256_REVIEWED_EXTERNAL_LAUNCHER>",
  "expected_bot_id": 0,
  "allowed_scopes": [[-1003883723333, 2], [-5320347021, 0]]
}
```

`expected_bot_id` заменить числовым ID реального TEST getMe, сверить с префиксом
токена/инвентаризацией. Не использовать 0 и не записывать token в policy.
Root свежий collector output → config/test-inventory.json 440; неполный/старше 300s
протокол блокирует --verify/--check. Ошибка helper/hash/непроверенный SHA/грязный code,
неверный hostname/path/token/scope/симлинк → exit2 до SQLite/Telegram.
Инструменты устанавливаются отдельно, candidate checkout остаётся чистым APP_SHA.

```bash
# Из CODE, от shpak-stage, с чистым окружением и HOME под STAGE; .env читается адаптером.
.venv/bin/python /srv/shpakdnd-v15-staging/config/staging-launch.py --verify
.venv/bin/python scripts/run_isolated_checks.py --label staging-story -- \
  tools/validate_release_content.py --directory /srv/shpakdnd-v15-staging/stories
.venv/bin/python scripts/run_isolated_checks.py --label staging-offline -- tools/check_v15.py
.venv/bin/python scripts/run_isolated_checks.py --label staging-tests -- -m unittest discover -s tests -q
.venv/bin/python /srv/shpakdnd-v15-staging/config/staging-launch.py --check
# Свежий inventory обновлять между долгими проверками; --check повторить ещё дважды.
```

Story PASS0 / missing BLOCKED2 / invalid FAIL1. Никакой публикации на сервер production.
После --check проверить только новую DB: integrity=ok, FK=[], 79 total/78 active,
четыре raid каталога и Mini world только -1003883723333/2, синтетические игроки ещё пусты.
Сверить все 45 PNG с CONTENT_ASSET_SHA256.json; app tree и авторский Story hash неизменны.
Полные проверки финального TOOLING_SHA отдельно, на своих одноразовых SQLite, обе Linux
3.11/3.13 jobs; baseline результаты не считаются результатом нового SHA.

## Переключение TEST и единственность polling

После PASS preflight и backup сохранить текущий unit/drop-ins приватно. Подготовить
**отдельный** `shpakdnd-v15-staging.service` disabled, не запускать параллельно старому.
User/Group=shpak-stage, WorkingDirectory=STAGE/code,
ExecStart=STAGE/code/.venv/bin/python STAGE/config/staging-launch.py --poll,
EnvironmentFile отсутствует (adapter читает hash-bound .env), UMask=0077,
Restart=no на период приёмки, KillMode=control-group, NoNewPrivileges=true,
ProtectSystem=strict, ProtectHome=true, ReadWritePaths только CODE/logs/runtime/locks.
Config/Stories read-only. locks root-owned 750; заранее создать файл
`locks/polling-<TEST_BOT_ID>.lock` root:shpak-stage 660, не symlink/hardlink.
Проверить systemd-analyze verify и Triggers/Requires/PartOf: без production units,
без watcher и автоперезапуска прежнего TEST. Не устанавливать deploy bundle.

1. Остановить **только** старый `shpakdnd-bot.service`. Проверить inactive/MainPID=0,
   все его cgroup-процессы завершены, watcher inactive, containers/cron не poll тот же token.
2. Свежий root inventory старого TEST (теперь inactive/0/no processes) →
   config/test-inventory.json 440. Старые DB/topics/env оставлять на месте для отката.
3. Из внешнего launcher validate вычислить fingerprint policy/env/topics/inventory;
   root записывает start-approval.json 440 с ровно approved_at (UTC epoch int),
   candidate_sha, fingerprint, polling_approved=true, bot_id. Срок approval 60 секунд;
   fingerprint брать из реально проверенных файлов, не из примера.
   Пример формирования approval: запустить из CODE от shpak-stage
   `env -i PATH=/usr/bin:/bin HOME="$STAGE" "$CODE/.venv/bin/python" "$STAGE/config/staging-launch.py" --describe`,
   сохранить stdout приватно. Root парсит JSON, требует stopped=true, добавляет
   approved_at=int(time.time()), polling_approved=true, удаляет поле stopped;
   пишет новый временный root:shpak-stage 440 файл в config и os.replace на
   start-approval.json. Не использовать shell eval и не публиковать конфиг.
4. В пределах 60s start **только** shpakdnd-v15-staging.service. --poll дополнительно
   проверяет stopped inventory, живой /proc и эксклюзивный per-bot flock. Неизвестный
   процесс/permission denied/второй запуск блокирует start. --verify/--check не запускают poll.
5. Проверить active/substate/MainPID/NRestarts и private journal: нет 409/Conflict,
   startup exception, production scopes, повторной доставки uncertain. Проверить Mini/D&D
   меню из двух TEST scopes, регистрацию/админа, scopes в новой SQLite и restart persistence.
6. Для restart остановить НОВЫЙ unit, повторить stopped legacy inventory/approval и
   start; прежняя новая DB остаётся физическим файлом, не пересоздаётся.

60-секундное разрешение и Restart=no намеренно требуют свежей root-проверки после crash;
это ограничение приёмочного стенда, не обещание unattended restart. Не ослаблять guard,
не выдавать долгоживущий approval ради systemd restart loop.

### Точный план очистки и возврата старого TEST

До успешного нового start **ничего не удалять**. `/opt/shpakdnd-v144-stage` сохранить
как выключенный старый checkout с локальным topics и symlink; `/opt/shpakdnd-bot`
сохранить полностью: это target старой DB и каталог с неклассифицированными изменениями.
Сохранить `/etc/shpakdnd-staging`, старый unit/drop-ins и один проверенный DB backup.
После успешного запуска составить список **конкретных** лишних TEST-копий с realpath,
назначением, владельцем, SHA, localdiff, service/process references и наличием нужных
настроек в backup. Удалять только утверждённые доказанно лишние пути; при сомнении оставить.
Этот план пока содержит **ноль путей к удалению**: свежая серверная инвентаризация нужна
для доказательства, запрещена автоматическая очистка /opt по маске.

Если новая версия не стартовала: stop новый unit, сохранить логи/новую DB, убедиться,
что новый polling завершён, проверить неизменность/совместимость старой DB и unit,
затем стартовать прежний TEST на **его нетронутой DB**. Не подавать V15 SQLite старому коду,
не восстанавливать backup поверх продолжающегося нового сеанса. После новых тестовых
ходов возврат — отдельный checkpoint и frozen compatibility guard на копии.
Временный SSH ключ удалить после работ; полный authorized_keys не заменять.

Реальные B/M/R/T/S/E отмечать NOT RUN до UI+SQLite evidence. Восстановление R0 ниже
относится только к новой TEST SQLite; прежние symlink DB и production copy исключены.

## Правила протокола и SQL-доказательств

Каждая строка ниже — отдельный кейс: время, SHA, tester, synthetic IDs, начальный backup,
действия, UI screenshot (без token), до/после SQLite rows, expected/actual, PASS/FAIL,
cleanup. Для таймеров фиксировать timezone. PASS только при совпадении UI и DB.
Любой неожиданный повтор receipt, разница баланса, неверный scope, corrupt DB — FAIL,
остановить новые действия, сохранить evidence и staging backup, не править SQL вручную.

SQL открывать только staging/copy через `sqlite3 -readonly "$CODE/shpakdnd.db"`.
Во всех примерах `:id/:player` заменять на реальные **synthetic** ID, задать параметры
через SQLite CLI `.parameter set`; пустая выборка из-за NULL не является PASS.
Базовые проверки до/после каждого набора:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
SELECT COUNT(*), COUNT(DISTINCT code) FROM mini_heroes WHERE active=1;
SELECT * FROM mini_players WHERE id=:player;
SELECT * FROM mini_wallet_transactions WHERE player_id=:player ORDER BY id;
SELECT * FROM mini_mythic_ledger WHERE player_id=:player ORDER BY id;
```

Восстановление R0 для всех кейсов: остановить только staging polling, сохранить текущую
DB через SQLite backup+hash, .env/config/content hashes и private logs; на **отдельной
новой staging копии** восстановить исходный synthetic checkpoint. Не накатывать старый
backup поверх продолжающейся сессии и не отправлять заново uncertain outbox.
Без потери данных предпочтителен исправленный кандидат/штатная отмена с согласованием.

## Обычные боссы

| Кейс и действия | Ожидаемый результат / SQL | PASS/FAIL и восстановление |
|---|---|---|
| B1: admin создаёт, два игрока регистрируются, admin начинает; повтор кнопки join/start | Одна сессия и одна запись участника; `mini_bosses`, `mini_boss_participants` по boss_id; неизменный snapshot | PASS: один старт/очередь, повтор без второго эффекта. FAIL: дубль/чужой scope. R0 |
| B2: legacy snapshot с 4-часовым таймером, дождаться deadline; отдельно новый V15 бой | Legacy `turn_timeout_seconds=14400` и сохранённый deadline; новые V15 **900 секунд**, не 4 часа. `mini_bosses(turn_started_at,turn_timeout_seconds,rules_version)` | PASS: timeout по своему frozen policy, старое не переписано. R0. Если владелец ожидает 4 часа для НОВЫХ боёв — это нерешённое отличие запроса от утверждённой O02, не менять формулу |
| B3: обычный skip, admin skip, затем старый callback и чужой игрок | Единственное действие в `mini_boss_actions`, audit/admin timeline; один переход очереди, старый шаг отклонён | PASS: queue/round и damage соответствуют одному шагу. R0 |
| B4: урон/победа/награды, два одновременных callback | HP и total_damage, frozen reward, один wallet receipt; `mini_boss_actions`, `mini_boss_participants` reward fields, wallet | PASS: нет двойного damage/reward, баланс сходится. R0 |
| B5: разные командные эффекты, реальный удар босса, skip/щит | `team_state_json`, participant `hero_state_json`, action `event_json` и timeline подтверждают одно применение на нужное событие | PASS: UI/event/HP совпадают с действующими правилами. R0 |

4-часовой legacy fixture готовить на изолированной копии тестовым harness, не SQL-правкой
таймера текущей игровой сессии. Приёмка должна явно решить конфликт ожидания 4h/15min;
это замечание к плану, не новое изменение игровой механики.

## Mythic

| Кейс и действия | Ожидаемый результат / SQL | PASS/FAIL и восстановление |
|---|---|---|
| M1: первое яйцо, повтор claim, покупка и повтор confirm, недостаток средств | Одна выдача/списание; `mini_mythic_pets`, `mini_mythic_operations`, ledger/wallet | PASS: разрешённые элементы/цены и один receipt, отрицательный баланс невозможен. R0 |
| M2: уход, cooldown, настроение, активный→резерв→активный, restart | `mood`, `cooldowns_json`, `location`, `stage_seconds`, `settled_at` сохраняются; один active | PASS: offline время/резерв по frozen policy, cooldown нельзя обойти restart. R0 |
| M3: egg→young/wonder→adult каждого из 8 элементов; конкурентное взросление | Один hero_id нужного code; `evolution_pending` сохраняется до успешной выдачи и снимается после неё; прогресс не теряется | PASS: 8 авторских связей, egg/young/adult PNG верны, нет дубля героя. R0 |
| M4: улучшение до 6★, недостаток universal shards, повтор confirm | `mini_player_heroes` stars, `mini_players.mythic_shards`, ledger/operations; один debit | PASS: точная действующая стоимость, максимум 6, валюта не hero-specific. R0 |
| M5: normal и raid пассивки каждого Mythic, повтор/перезапуск | Boss/Tower normal events против raid `events_json`, сохранённые arcana/Arise | PASS: режимы не смешиваются, результаты не reroll. Moon text proposal не применять. R0 |
| M6: ordinary gacha, старый craft, collection | Mythic доступен коллекции/выдаче взрослением; отсутствует в ordinary drop/craft | PASS: gameplay receipts и catalog flags; единичный случайный gacha pull не доказывает вероятность, сверить автоматические negative tests. R0 |

## Рейды — по отдельной новой сессии каждого автора

| Кейс и действия | Ожидаемый результат / SQL | PASS/FAIL и восстановление |
|---|---|---|
| R1: `baldur_adamantine_colossus`, 100000 HP, Fire/Ice | `armor_cycle`: 10 armored → 5 exposed → следующий цикл; frozen `snapshot_json`, persisted `state_json`, audit/attacks | PASS: границы 10/11/15/16, restart посреди фазы не сбрасывает счётчик. R0 |
| R2: `tsurumi_thunderbird`, 115000 HP, Lightning/Star | `flight_cycle`: точные grounded/flying длительности из авторского frozen mechanic; melee/ranged/passives применяются в нужной фазе | PASS: boundary и сохранение state, повтор не продвигает цикл. R0 |
| R3: `wood_steel_butcher_golem`, 130000 HP, Dark/Sun | После каждых 15 атак +25bps original HP, Sun suppression по policy; лечение ≤ max_hp, не начисляет player damage | PASS: attacks14/15/16, heal audit, frozen original HP, suppress/restart/repeat. R0 |
| R4: `spatial_anomaly_angel`, 150000 HP, Star/Moon | Порог 75/50/25% original HP; tags `[armored,flying]→[armored]→[flying]→[]`, monotonic | PASS: ровно на границе, перескок нескольких порогов и последующее лечение не возвращают фазу. R0 |
| R5: counter hero confirmed при actual=0, потом повтор/другой герой | Действующая counter policy активируется на первом confirmed counter attack, включая 0; один counter receipt/сохранённый state | PASS: actual=0 не отменяет подтверждение и не повторяет бонус. R0 |
| R6: победа, участники с разным damage, конкурентный last hit, restart | `mini_mythic_raid_damage/rewards/attacks/audit`, ledger; cap actual damage, один reward на player | PASS: ranking и сумма наград по frozen formula, повтор выплаты невозможен. R0 |
| R7: admin отмена без награды и отдельно отмена с наградой, reason, revision race | `cancel_requests`, status/result, audit+ledger. Безнаградная отмена компенсирует только утверждённые правила O01, не произвольные суммы | PASS: confirm привязан к actor/world/revision; старый/чужой/повторный confirm не платит второй раз. R0 |

```sql
SELECT * FROM mini_mythic_raids WHERE id=:id;
SELECT * FROM mini_mythic_raid_attacks WHERE raid_id=:id ORDER BY id;
SELECT * FROM mini_mythic_raid_damage WHERE raid_id=:id;
SELECT * FROM mini_mythic_raid_rewards WHERE raid_id=:id;
SELECT * FROM mini_mythic_raid_audit WHERE raid_id=:id ORDER BY id;
SELECT * FROM mini_mythic_raid_publications WHERE raid_id=:id;
```

## Башня

| Кейс и действия | Ожидаемый результат / SQL | PASS/FAIL и восстановление |
|---|---|---|
| T1: выбор героя/экипировка и старт; изменить loadout после старта | Frozen hero_json/enemy_json/equipment_bonus в `mini_tower_attempts`; исходно shields=3 | PASS: активный snapshot не меняется. R0 |
| T2: три щита, normal пассивки, поражение/победа, restart | shields 0..3, turn/runtime/events сохраняются, уникальная active attempt | PASS: повтор атаки не меняет результат второй раз. R0 |
| T3: награда этажа, сундук/повтор кнопки и конкурентное открытие | `mini_tower_rewards`, progress, inventory и wallet/ledger | PASS: один reward/receipt, сундук имеет один результат, restart не reroll. R0 |

## Истории — два игрока и администратор

Перед живым прогоном `validate_story_candidate.py` доказывает seven endings/once paths
на изолированных фикстурах. В живом Telegram выполнить **семь отдельных сессий**:
`ending_k1` … `ending_k7`, маршруты взять из авторского GRAPH_AUDIT/JSON, не редактировать
переходы. Для каждой сохранить список node_id, votes, visible labels и result.

| Кейс и действия | Ожидаемый результат / SQL | PASS/FAIL и восстановление |
|---|---|---|
| S1: admin register, 2 players join/vote/revote, одинаковый choice одновременно | `story_participants`, votes PK(session,step,user), один step commit | PASS: группа одна, revote обновляет свой голос, не создаёт новый step. R0 |
| S2: посещённая сцена/возврат и переход в остаточный тупик | Кнопки таких choices скрыты; `story_steps` — общий durable visited set группы | PASS: каждый предложенный next имеет путь к настоящей ending без visited nodes. R0 |
| S3: после фильтрации нажать вторую/последнюю кнопку, затем старую кнопку | choice_id остаётся авторским; callback не путает filtered index с original index; stale step отклонён | PASS: DB choice_id/next_node ровно соответствует надписи, старый шаг не повторяется. R0 |
| S4: restart с голосами и после перехода; повтор callback | visited/votes/snapshot/step сохраняются, та же сцена не публикуется повторно | PASS: SQL ниже пуст для дублей, один transition receipt. R0 |
| S5: пройти все seven endings в отдельных session_id | `story_sessions.status='finished'`, настоящий ending_kN, авторский текст без искусственных узлов | PASS: все 7 протоколов/SQL и ни одного повторного node в каждой группе. R0 |
| S6: V1 fixture отдельной сессии | V1 routing по прежнему контракту, без принудительного once | PASS: прежние тесты и staging fixture в отдельной STORIES_DIR, не production JSON. R0 |
| S7: обрыв доставки до/после Telegram ответа; crash sending | `story_publications`: pending/sending→sent либо uncertain; progression/rewards не повторяются | PASS: uncertain не отправляется автоматически. Сверка по TELEGRAM_UNCERTAIN_RUNBOOK; не SQL-reset, не слепой retry; отдельная согласованная cancel/new-session. R0 |

```sql
SELECT * FROM story_sessions WHERE id=:id;
SELECT * FROM story_steps WHERE session_id=:id ORDER BY step;
SELECT node_id,COUNT(*) FROM story_steps WHERE session_id=:id GROUP BY node_id HAVING COUNT(*)>1;
SELECT * FROM story_votes WHERE session_id=:id ORDER BY step,user_id;
SELECT * FROM story_publications WHERE session_id=:id ORDER BY step;
```

Для active node учитывать текущий `story_sessions.node_id`, если финальная сцена ещё не
записана отдельной строкой. SQL на дубли необходим, но сам по себе не доказывает
отсутствие тупиков: дополнительно сверять каждый visible next с available_choices на
том же snapshot/visited и полной автоматической проверкой графа, без mutation.

## Экономика и регрессии

| Кейс и действия | Ожидаемый результат / SQL | PASS/FAIL и восстановление |
|---|---|---|
| E1: кошелёк grant/spend, недостаток средств, повтор operation key | `mini_players`, wallet transactions/ledger, баланс до+receipts=после | PASS: один debit/credit, запрет отрицательного остатка. R0 |
| E2: gacha single/batch/pity, collection/stars и одновременное подтверждение | `mini_gacha_pulls`, batches, player_heroes и wallet; прежние probabilities/costs | PASS: один итог на operation key, нет Mythic в ordinary pool. R0 |
| E3: shop покупка/инвентарь/экипировка; повтор и sold-out | Wallet/owned/equipment slots и shop receipts; ordinary D&D scope отдельно от Mini | PASS: stock/баланс/ownership согласованы, чужой scope недоступен. R0 |
| E4: PvP create/accept/cancel/expire/finish, escrow, retry, uncertain | `mini_duels`, locks/escrow/publications по актуальной DATABASE.md и wallet | PASS: нет потери/дубля денег, один settlement, uncertain не обходится. R0 |
| E5: boss/raid/Tower/Star rewards конкурентно и после restart | mode/battle/player receipts и universal ledger, отсутствуют повторные grants | PASS: reconciliation до/после сходится для каждого игрока. R0 |

## Безопасный откат именно стенда

1. Остановить только `shpakdnd-v15-staging.service`; watcher production не трогать.
2. Сохранить полную текущую staging SQLite через backup, её hash/integrity/FK,
   private env/config hashes, Story manifest, логи, SHA и все uncertain IDs.
3. Проверить frozen release_guard на **копии** и выбранный target. Неизвестный target,
   V15 state в старом коде, unresolved PvP/publications → fail closed. Не `--force`.
4. Предпочесть исправленный candidate и новую отдельную копию checkpoint. Разрешённый
   возврат прежнего кода — отдельный checkout/venv/service, с проверенной совместимой
   staging DB copy. Старый DB backup не заливать поверх новой активности.
5. Uncertain сообщения сверять вручную; если нельзя доказать доставку — оставить
   blocked и согласовать штатную отмену, не дублировать outbox или награду.
6. Installer rollback описан отдельно в ATOMIC_BUNDLE_AND_CI.md; он не откатывает игру.
   Глобальный bundle на staging/production в текущей задаче не устанавливается.

## Критерий завершения будущей приёмки

Все B/M/R/T/S/E строки имеют evidence и PASS, семь концовок действительно пройдены,
restart/uncertain/конкуренция проверены, source production copy не менялась, isolation
bot/scopes/paths доказана. Отдельно согласовать ожидание 4h legacy против 15min новых
боёв. Любая незакрытая строка — BLOCKED/FAIL, а не «условно принято».
Даже успешный стенд не разрешает merge/production: требуется отдельное решение владельца.

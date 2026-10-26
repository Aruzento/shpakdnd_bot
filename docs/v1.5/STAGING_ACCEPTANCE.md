# D&D Mini V1.5 — отдельная приёмка Telegram staging

**ПЛАН, НЕ ВЫПОЛНЕНО. В разработке.** Требуется отдельное разрешение владельца.
Кандидат: полный SHA из финального отчёта Draft PR #8; записать его в `candidate.txt`
и протокол каждого прогона. Команда ниже проверяет именно это значение, а не branch tip.
Проверки Windows/Linux не означают, что живой стенд пройден. Main/production не менять.

## Предварительные блокеры и изоляция

- Независимое повторное ревью P2-01/P2-02 и всех изменений PR, зелёные обе Linux jobs
  именно SHA кандидата. Пакет доказательств: логи, exit codes/skips, timing, manifest.
- Владелец отдельно предоставляет staging token нового bot, два тестовых аккаунта,
  admin username, ID **новой частной группы** и её Mini/D&D тем. Bot не добавлять в
  production группы. Ввод token приватный, не в shell history/Git/PR/логи.
- Отдельная VM/пользователь `shpak-stage`, без доступа к production host, SSH keys,
  SQLite, каталогам, токену и systemd units. Желательно отдельный хост, а не соседняя VM
  с общими mount. Исходящие Telegram разрешать только после проверки token/getMe.
- `/srv/shpakdnd-v15-staging/code`, собственные `.venv`, `.env`, `shpakdnd.db`,
  `timers.db`, logs и backup. В приложении DB_PATH вычисляется относительно checkout,
  поэтому отдельный checkout обязателен: одного env DB_PATH недостаточно.
- Внешний `/srv/shpakdnd-v15-staging/stories`, вне Git. Обязательный
  `tayna_propavshey_ovtsy.json`, SHA-256
  `f185595d8c89fc1644e331a4ff1ae34c6df61920b4a69c9948654cd60903cce2`.
  Только копия утверждённого unpublished JSON, не шаблон и не production STORIES_DIR.
- Shop/loot/code-map/offers CSV, editorial runtime и каждый иной writable path — только
  staging paths. Проверить все default `/var/lib/shpakdnd` и Telegram scopes перед start.
- Production SQLite copy владельца — только отдельный offline migration sandbox,
  **никогда не база polling-стенда**: таймеры/публикации копии могут содержать реальные
  chat IDs и outbox. На живом стенде только синтетические тестовые игроки/балансы.
- В этой итерации не меняются Moon text, контрпик actual=0, экономика и вероятности.

## Команды подготовки — выполнять позже, после разрешения

```bash
# В отдельной VM. CANDIDATE переписать из финального отчёта, проверить все 40 hex.
CANDIDATE='<FINAL_SHA_FROM_REVIEWED_REPORT>'
STAGE=/srv/shpakdnd-v15-staging
CODE="$STAGE/code"
git clone --no-checkout https://github.com/Aruzento/shpakdnd_bot.git "$CODE"
git -C "$CODE" checkout --detach "$CANDIDATE"
test "$(git -C "$CODE" rev-parse HEAD)" = "$CANDIDATE"
test -z "$(git -C "$CODE" status --porcelain)"
printf '%s\n' "$CANDIDATE" > "$STAGE/candidate.txt"
python3 -m venv "$CODE/.venv"
"$CODE/.venv/bin/python" -m pip install -r "$CODE/requirements.txt"
mkdir -p "$STAGE/stories" "$STAGE/logs" "$STAGE/backups" "$STAGE/runtime"
install -m 444 "$CODE/content/stories/unpublished/tayna_propavshey_ovtsy/tayna_propavshey_ovtsy.json" "$STAGE/stories/tayna_propavshey_ovtsy.json"
printf '%s  %s\n' f185595d8c89fc1644e331a4ff1ae34c6df61920b4a69c9948654cd60903cce2 "$STAGE/stories/tayna_propavshey_ovtsy.json" | sha256sum -c -
# Через приватный редактор создать CODE/.env, chmod 600. Не копировать production env.
# BOT_TOKEN=<STAGING_ONLY>; STORIES_DIR=/srv/shpakdnd-v15-staging/stories
# DND_SHOP_CSV_PATH, DND_LOOT_CSV_PATH, DND_SHOP_CODE_MAP_PATH,
# DND_SHOP_OFFERS_PATH — абсолютные отдельные пути внутри STAGE/runtime.
cd "$CODE"
.venv/bin/python scripts/run_isolated_checks.py --label staging-offline -- tools/check_v15.py
.venv/bin/python scripts/run_isolated_checks.py --label staging-tests -- -m unittest discover -s tests -q
.venv/bin/python tools/validate_release_content.py --directory "$STAGE/stories"
```

Последняя команда должна PASS0; отсутствующий сценарий — BLOCKED2; неверный hash,
невалидный/лишний JSON — FAIL1. Реальные Stories не публикуются этими offline checks.
Сверить 45 PNG по `docs/v1.5/CONTENT_ASSET_SHA256.json`; 79 кодов (71+8), 4 raid,
egg/young/adult связи, отсутствие дублей; сравнить manifest и каталоги с audited SHA.

### Telegram scopes — обязательный отдельный конфиг стенда

`app/topics.py` содержит реальные scopes, env override для них отсутствует. Нельзя
просто запустить bot.py с новым token и заявить изоляцию. До polling требуется отдельно
просмотренный **внешний staging launcher**, заменяющий `app.topics.TOPIC_SETTINGS`
до импорта handlers/worlds и запуска bot.py. Он не меняет tracked game files.
Формат private `STAGE/runtime/topic-settings.json`: mapping новых chat IDs к новым
thread IDs с `admin`, `characters` двух тестовых аккаунтов; Mini theme `mini=true`.
Проверить совпадение нормализованных usernames с тестовыми Telegram аккаунтами.

Подготовленный алгоритм внешнего launcher (пути подтвердить отдельно):

```python
# STAGE/runtime/staging-launch.py, root-reviewed, not writable by bot.
import json, os, runpy, sys
from pathlib import Path
code = Path('/srv/shpakdnd-v15-staging/code').resolve(strict=True)
assert Path.cwd().resolve() == code
sys.path.insert(0, str(code))
import app.topics as topics
production_ids = set(topics.TOPIC_SETTINGS)
path = Path('/srv/shpakdnd-v15-staging/runtime/topic-settings.json')
raw = json.loads(path.read_text(encoding='utf-8'))
settings = {int(chat): {int(thread): value for thread, value in entries.items()}
            for chat, entries in raw.items()}
assert settings and not (set(settings) & production_ids)
assert all(value.get('admin') for entries in settings.values() for value in entries.values())
topics.TOPIC_SETTINGS = settings
# Тот же launcher использовать для check_bot.py и bot.py, не только для polling.
runpy.run_path(str(code / ('check_bot.py' if '--check' in sys.argv else 'bot.py')),
               run_name='__main__')
```

Перед первым start: отдельно проверить private scope JSON, его hash, отсутствие bot
в production-группах, token/getMe, все writable paths и settings без production IDs.
Данный adapter требует review как часть конфигурации стенда; создание/запуск на VM
в текущем задании не выполняется. Если изоляция scopes не доказана — STOP/BLOCKED.

### systemd и миграции стенда

Отдельный `shpakdnd-v15-staging.service`: User=shpak-stage,
WorkingDirectory=STAGE/code, ExecStart=STAGE/code/.venv/bin/python
STAGE/runtime/staging-launch.py; собственный EnvironmentFile=CODE/.env,
Restart=on-failure, UMask=0077, NoNewPrivileges=true, ProtectSystem=strict,
ReadWritePaths только STAGE/code (SQLite), STAGE/logs/backups/runtime.
STORIES_DIR read-only. Защитить `.env` и runtime config; не использовать production
watch.path/update.service и не устанавливать глобальный deploy bundle на стенде.

До запуска polling выполнить внешний launcher `--check`, сохранить init/migration log,
затем повторить дважды. На staging SQLite проверить integrity=ok, FK=[], отсутствие
потери существующих synthetic rows, 79 active heroes, прежние balances/ownership.
Отдельно на переданной **offline-копии**:

```bash
.venv/bin/python scripts/run_isolated_checks.py --label provided-copy -- tools/validate_v15_migration.py --source '<OWNER_OFFLINE_COPY>' --output '<PRIVATE_REPORT>'
# sha256sum source до/после одинаков; source не становится DB_PATH стенда.
```

Проверить start/stop только staging unit, PID/restart-count, startup exceptions,
`journalctl -u shpakdnd-v15-staging.service`, отсутствие restart loop и production units
в `systemctl show ... Triggers/PartOf/Requires`. Polling не запускать дважды одним token.
Отдельный manual start — только после разрешения, PASS offline и проверки private config.

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

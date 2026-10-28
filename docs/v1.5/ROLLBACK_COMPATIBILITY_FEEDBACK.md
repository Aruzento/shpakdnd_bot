# Release/rollback compatibility — кандидат PR #9, 28.10.2026

Статус: исправление для независимого ревью ChatGPT; production deployment не разрешён.
Исходный commit: 046f7dc1baee9e08207af72650fb4636b7da2d52.
Финальный tooling commit и CI указываются в отчёте PR #9 после фиксации и проверок.
Игровой app tree остаётся cb678fe9f0978bb115a12e27c56a7160d0c94e4c.

## Причина блокера и аудит

Старый guard распознавал только предыдущий V1.5 tree и production V1.4.
Тест SHA=cef77cc проверял именно старую V1.5, хотя отдельный тест app-tree уже
фиксировал cb678fe9. Поэтому прежний зелёный CI не подтверждал capability кандидата.
Присвоение новому tree старой capability оставило бы BLOCKED_FEEDBACK_STATE_UNSUPPORTED.

Проверены реальные immutable app trees и изменения 35 app-файлов между прежней V1.5
и 046f7dc: новые UI/Stories журналы, упаковка callback с поколением/картой действий,
части публикаций и неизвестная доставка, admin reminders, атомарная регистрация
достижений без выплат. Изменение лимита рейдовых атак 10→5 и выключение новых PvP/дуэлей
уже входят в принятый игровой кандидат; этот hardening их не меняет.

| Commit / app tree | Capability | Поддержка |
|---|---|---|
| d077dd2a / 908868852fc3b1e132a65e325ca546025b4034de | legacy-pre-v15 | прежний production формат |
| cef77cc / 64823c903daa7e3cea5d12613f364da67f630a54 | v15-once-raid-pets-economy | Story once, Raid, Pets, ledger/Star/ownership |
| 046f7dc и tooling-only потомки / cb678fe9f0978bb115a12e27c56a7160d0c94e4c | v15-feedback-ui-stories | всё выше + четыре feedback журнала |

Разрешён ровно один новый tree. Название ветки, startup PASS и заявленный файлом
capability не служат доказательством. Проверяются полный SHA, объект commit и тип
app-tree; refs/replace блокируются, Git environment overrides не используются.
--current должен совпадать с фактическим HEAD; при проверке rollback до и после checkout
shell передаёт head_now, поэтому обе фазы привязаны к своей реальной версии.

## Сохранённые состояния

- mini_private_windows: bot/world/user, поколение, адрес и callbacks_json.
  Поколения нельзя обнулить или удалить ради возврата к старым активным кнопкам.
  Pending reserve без адреса безопасен для совместимого кода: старое окно не оживает.
- story_publication_parts: подтверждённые части сообщения и связь с publication.
  Старый код их не использует. sending/uncertain требуют ручного разбора; confirmed
  parts при pending parent считаются неизвестным/неполным состоянием.
- story_reminders: получатели, step, operation key, 15-минутный claim и delivery status.
  sending/uncertain блокируют любой target; автоматически повторять отправку нельзя.
- story_achievements: уникальный user/story/ending, session, earned_at. Состояние хранится
  после завершения истории. Выплаты выключены игровым кодом; guard ничего не начисляет.

mini_public_notifications — настоящий delivery journal: sending/uncertain требуют
ручной проверки для всех target. Отдельный старый mini_ui_publications хранит постоянный
one-shot claim с единственным штатным status='sending', включая два ожидаемых backfill
receipt. Это НЕ lease незавершённого запроса: такой backfill не блокируется как unknown
delivery. Любой неизвестный status в обоих журналах блокируется.

Legacy V1.5 не получает поддержку этих журналов. Даже одна заполненная таблица запрещает
downgrade к cef77cc. Отсутствующие все четыре журнала допустимы на старой БД; неполный
набор или неизвестная колонка запрещены. Старый семиколоночный private-window журнал
не входит в аудированную схему: обходить отказ либо удалять его нельзя.

Raid active/rewarded/cancelled, receipts/operations/audit/rewards; Pet inventory/reserve/
active/adult/evolution_pending; Story registration/active/finished/cancelled и once history;
universal balances, owned Mythic, Star/lunar/Arise: сохранён прежний persistent guard.
Завершение рейдов и историй не удаляет ledger/ownership и не разрешает legacy downgrade.
Новые UI changes не меняют форматы существующих финансовых операций.

PvP использует прежний frozen pvp_ops.py без изменений: pause-new, escrow, pending
publication/edit leases и economic mismatch продолжают блокировать rollback.
Найден дополнительный дефект прежнего guard: настоящая V1.4 Boss-схема не имеет
team_state_json/rules_version. Теперь это допускается только для точной legacy-описи,
без V1.5 schema. Отсутствующие поля на V1.5 по-прежнему блокируются.

Boss/Tower проверяются по сохранённым rules_version/reward_policy/status; неизвестные
версии и незавершённые V1.5 snapshots блокируют старый target.

## Frozen schema inventory

release_schema.json — опись для read-only helper, НЕ игровая схема/миграция.
Добавлен _baseline: 80 существующих таблиц, известные варианты четырёх наборов колонок,
обязательные группы Mini/D&D и целостная группа Boss по legacy/V1.5 профилю, семь известных trigger/view definitions.
Опись получена запуском настоящих init_db/init_mini_db/init_boss_db трёх immutable
версий на отдельных временных SQLite: 78 /101 /105 пользовательских таблиц.
sqlite_sequence — внутренний объект SQLite, отдельно в числа не входит.

Варианты mini_players, mini_heroes, mini_bosses, mini_tower_rewards выбираются по
legacy/V1.5 профилю, не смешиваются произвольно. table_xinfo обнаруживает также
generated/hidden columns. Дополнительные signatures проверяют порядок/тип/default/nullability/PK колонок, FK и unique/partial indexes. Совпадения одних имён колонок недостаточно. Неизвестные таблицы, колонки, views/triggers либо частичные
группы блокируются. Это более строгая диагностика, а не модификация базы.
Пользовательские SQL objects, не вошедшие в аудит, требуют отдельного анализа:
их нельзя удалять ради PASS. Индексы и ограничения не переопределяются helper-ом.

Guard открывает только явный физический project/shpakdnd.db через mode=ro,
query_only=ON и единую read transaction. WAL-aware: immutable=1 не используется.
Никаких импортов target app, Telegram, migrations, settle/recovery или wallet операций.
Повторный запуск сохраняет исходные DB/WAL bytes и возвращает тот же агрегированный результат.

## Матрица rollback

Все ALLOW ниже условны: integrity/FK/schema/status валидны, PvP frozen guard готов,
нет sending/uncertain и иных blockers. ALLOW — совместимость, НЕ разрешение запуска.

| SQLite | legacy V1.4 | прежняя V1.5 | feedback V1.5 |
|---|---|---|---|
| Аудированная legacy БД без V1.5 прогресса | ALLOW | ALLOW | ALLOW |
| V1.5 schema, пустые новые journals, без V1.5 persistent rows | ALLOW | ALLOW | ALLOW |
| Raid/Story history, ledger/operations, ownership/баланс/Star/Pet | BLOCKED | ALLOW | ALLOW |
| Любой заполненный feedback journal, включая terminal achievement | BLOCKED | BLOCKED | ALLOW |
| Все четыре feedback tables отсутствуют на старой БД | по persistent matrix | по persistent matrix | ALLOW |
| Не все четыре feedback tables или неизвестные columns/objects/status | BLOCKED | BLOCKED | BLOCKED |
| Publication/reminder sending/uncertain, partial delivery | BLOCKED | BLOCKED | BLOCKED |
| Активный PvP escrow, unresolved edit/economy, pause-new не установлен | BLOCKED | BLOCKED | BLOCKED |
| Unknown app-tree/SHA или Git replacement refs | BLOCKED | BLOCKED | BLOCKED |

Совместимый target умеет возобновить активные Raid/Story/Pet/Boss/Tower состояния,
поэтому они сами по себе не требуют экономического drain. Оператор отдельно проверяет
условия сохранения игровой семантики: старая V1.5 имеет прежние UX/лимит рейда и режимы.
Guard не даёт разрешения возвращать старые правила без решения владельца.

## Frozen bundle: установка без deployment

Команды ниже — инструкция после независимого ревью и отдельного разрешения.
В рамках этой задачи installer, systemd, polling и deploy НЕ выполняются.

1. Взять полный финальный tooling SHA из отчёта PR #9, подготовить чистый доверенный
   checkout этого SHA. Убедиться, что app tree равен cb678fe9... и manifest валиден.
2. Сохранить текущий entrypoint/retained bundle SHA и private operator evidence.
3. Установить новый комплект одной штатной командой:

       sudo bash /ABS/TRUSTED/CHECKOUT/deploy/install-deploy-shpakdnd.sh --sha "$FINAL_SHA"

   Installer проверяет Git bytes всего комплекта и общий deploy lock; stage→immutable
   releases/SHA→атомарный entrypoint. Предыдущий bundle остаётся. Бот не останавливается,
   SQLite/.env/Stories не читаются. Никакого копирования одного release_guard.py поверх
   активного комплекта.
4. Замена release_schema.json требует именно нового frozen bundle: старый helper и
   новый target не пройдут validate_frozen_bundle(). Новый guard/schema/preflight должны
   быть из одного проверенного commit. Manifest SHA-256 пересчитывается целиком.
5. После разрешённой установки оператор отдельно выполняет штатный dry-run/preflight.
   Проверка новой production offline copy именно новым guard остаётся обязательной:
   прежний migration/startup PASS не доказывает новый rollback verdict.
   Никакой пишущий check_bot/migration против рабочей SQLite.

## Возврат bundle отдельно от отката игры

Только после проверки retained SHA и отдельного разрешения оператора:

    sudo bash /ABS/TRUSTED/CHECKOUT/deploy/install-deploy-shpakdnd.sh --sha "$FINAL_SHA" --activate-existing "$PREVIOUS_BUNDLE_SHA"

Это переключает лишь deploy entrypoint, не app HEAD/SQLite/systemd. Возврат старого
bundle НЕ даёт ему capability нового app tree. Прежний helper может снова блокировать
046f7dc и имеет более слабую диагностику: не использовать его как обход нового guard.
Для эксплуатации нового кандидата нужен совместимый reviewed bundle.

## Заблокированное восстановление

При BLOCKED сохранить текущий SHA, последнюю БД/WAL и логи; старый код автоматически
не запускать. Frozen shell повторяет guard до checkout и до start. Восстановление
старой SQLite поверх новых операций запрещено. Не удалять journals/ledger/ownership,
не применять --force, maintenance/drain/ручной refund для получения зелёного результата.
Нужен отдельный проверенный совместимый target или согласованный ручной план доставки/
экономики. Если безопасность последней БД не доказана, оставить стек остановленным.

По сообщению владельца: игровая приёмка V1.5 и миграции production copy уже пройдены,
внешний Story подготовлен. Эти результаты не повторялись на серверах данной задачей;
новый tooling SHA требует собственных CI и независимого ревью. Production V1.4,
main и рабочие данные остаются вне изменений.

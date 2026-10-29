# Отдельное ТЗ: SQLite INTEGER overflow — проект для последующего согласования

Статус: PREPARED / NOT_AUTHORIZED_FOR_RUNTIME_IMPLEMENTATION.
В рамках текущего guard-only этапа app/runtime/экономика/миграции не меняются.
База анализа:8e89473563e42fdd30c2aa15c04bd816a1f97ced; app tree cb678fe9f0978bb115a12e27c56a7160d0c94e4c.

## Цель и инварианты

Защитить ordinary mini_players.shards и mini_player_heroes.stars/copies от SQL
INTEGER→REAL overflow. Сохранить точные награды,цены,валюты,вероятности,порядок
проверки operation keys и первоначальные defaults. Не clamp,не насыщать MAX,
не округлять/переводить REAL в int и не помечать неполученную награду успешной.
Физический диапазон SQLite INTEGER:0..9223372036854775807. Это не новый игровой лимит.
Отдельные существующие правила Mythic1..6/прочих rarities не изменять.
Не вводить новую трактовку исторического copies=0 или иных legacy значений без аудита.

## Исходные доказательства

Shards: configured superadmin может штатно выдать MAX в synthetic мире с0.
Следующий SQL add_kill_shards(+1) пишет REAL. Непривилегированный remote exploit
не продемонстрирован. Stars: actual Arise пишет REAL при synthetic initial MAX;
короткий gameplay/admin star setter путь не найден. SQL copies+1 в тех же upserts
требует защиты одновременно. Текущий release guard блокирует плохие shards/stars,
но не предотвращает запись работающим приложением.

## Обязательный аудит writers перед изменениями

Проверить все INSERT/UPDATE/upsert прямые и динамические через rg и use-case paths:
- shards.change_shards_in_transaction;
- Boss repository.add_kill_shards, runtime,rewards,combat bonus paths;
- combat.star_rewards.award (его существующий upper-bound не убирать);
- Events._finish, предмет shard_casket, gacha duplicate grants/coupons;
- hero_upgrades upgrade/sell,superadmin,любые grant functions;
- village collection/settle и связанные resource-unit writes;
- combat.arise.award и legacy shadow.extract_in_transaction;
- Mythic pet adult issuance,Mythic upgrades и остальные ownership creators.
Mythic universal shards — отдельная валюта, её wallet/стоимости не объединять с ordinary.
Доказать,какие пути уже fail-closed при Python binding overflow,а какие выполняют
SQL arithmetic и могут сохранить REAL. Одного исправления add_kill_shards недостаточно.

## Минимальная реализация после отдельного разрешения

1. Общий checked integer calculation: проверить тип старого значения и delta,
   вычислить old+delta в Python arbitrary-precision int,проверить физический диапазон.
   Не конвертировать испорченный NULL/REAL/TEXT/BLOB в «починенный» int.
2. Использовать существующий caller BEGIN IMMEDIATE, тот же connection.
   UPDATE SET value=? уже проверенным Python int; не новый nested transaction/commit.
   Не оставлять прямых SQL increments/decrements для защищённых полей.
3. В Shadow/Arise duplicate branch проверять stars+1 И copies+1 до записи.
   Первый grant/defaults и Mythic issuance сохранить в точности.
4. Проверки actor/world/authorization сохранить. При повторной operation key
   возвращать прежний committed result,не выполнять новое начисление/списание.
   Новые guards не должны превращать корректный повтор в повторную выплату.
5. При out-of-range — конкретная controlled error и rollback всего текущего
   атомарного use case:ресурсы,combat result где он в той же транзакции,ledger,
   operation result,receipt,outbox. Не частичная выплата/автоматический refund.
6. Telegram success/publication после соответствующего commit по прежнему контракту.
   При uncertain delivery не пытаться «лечить» её повторной финансовой операцией.
7. При плохих текущих данных —BLOCKED/manual review,не auto-repair.
   DDL rebuild/новые triggers/migrations для обхода исторических схем не вводить.

## Тесты и совместимость

Unit boundaries:0,MAX-1+1→MAX integer,MAX+1→ошибка;spend-to-zero,overspend,
огромный delta;NULL/REAL/TEXT/BLOB;stars/copies overflow каждого поля.
Проверить defaults нового ownership и отсутствие изменения price/reward formulas.

Integration:actual Boss/Events/item/gacha/village/Shadow paths,rollback всех полей
и ledger/receipt/outbox;двойной callback,operation-key retry,конкурентные два начисления,
spend+reward,перезапуск до/после commit,uncertain Telegram delivery.
Одинаковая логика на clean и исторических схемах без CHECK;старые users/IDs/ownership
сохраняются. Ни actual TEST/production SQLite,ни её ручные правки не используются.

Сначала только затронутые tests,затем один связанный integration suite.
После окончательной реализации —новый app tree audit/capability pin,финальный commit,
полный unittest/validators/check_bot/compileall/diff и Linux3.11/3.13 exact-SHA CI.
Никакого полного CI после промежуточных правок,удаления tests или новых skips ради PASS.

## Приёмка и границы

Успешные операции дают те же целые суммы и результаты;повторы идемпотентны.
Переполнение не коммитит даже часть операции и не создаёт успех/receipt/outbox.
Ниже физической границы gameplay/экономика не меняются.
Это отдельное следующее изменение только после завершения guard-only этапа и
согласования владельцем. Merge,deployment,restart,rollback,production/main/TEST
этим документом не разрешаются.

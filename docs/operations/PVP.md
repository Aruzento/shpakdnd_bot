# PvP operations V1.4.3

Статус: **В разработке**. Только доверенная серверная консоль под пользователем
бота. Production действия требуют отдельного согласованного окна. Ниже шаблоны;
в разработке проверены только временные БД. Публичных admin-кнопок/HTTP endpoint нет.

## Короткая инструкция при инциденте

1. При отказе rollback оставить стек остановленным; сохранить current/target SHA,
   путь backup и JSON диагностики. Старую БД поверх текущей не восстанавливать.
2. Standalone helper с explicit --db: status, затем drain-status. При ошибке
   integrity/прав/ledger не угадывать баланс, перейти к ручному разбору.
3. Из проверенного нового checkout явно pause-new --confirm. Завершить choosing
   либо дождаться реальных deadline; при остановленном боте явно expire --confirm.
4. Сверить refunds/публикации. Для uncertain/legacy review требуются внешние
   доказательства; для известного недоступного сообщения возможен abandon-edit.
5. Выполнить rollback-check. Только rollback_ready=true и отдельное согласование
   позволяют повторить штатную команду с актуальным frozen helper. Иначе сохранить
   блокировку. resume — отдельное явное решение, он снова открывает новые ставки.

## Диагностика

Обязателен точный доверенный --db. Инструмент не подбирает production путь и не
создаёт отсутствующую БД. Read-команды работают без BOT_TOKEN, приложения и .env,
ничего не мигрируют, учитывают committed WAL.

    python deploy/pvp_ops.py --db /trusted/copy.db status
    python deploy/pvp_ops.py --db /trusted/copy.db drain-status
    python deploy/pvp_ops.py --db /trusted/copy.db rollback-check

tools/pvp_ops.py — тот же entry point из checkout. rollback-check возвращает 0
только при rollback_ready=true, иначе 2. Успешный status/drain-status возвращает 0
даже с blockers: читать JSON. Ошибка чтения/схемы — 2 и rollback_ready=false.
Аудит всей истории может занимать время; не запускать в частом цикле.

Сводка: paused, blocked_create/blocked_accept (накопительные), open/choosing,
active_stake_coins, unknown_publications, unfinished_publications, economic_errors,
rollback_ready и blockers. Сессия: ID/world/chat/topic, status/publication,
наличие message_id, время попыток, безопасные тип/описание ошибки, retry_count,
next_retry_at, edit_status, delivery_uncertain, refunds_confirmed, errors/warnings.
Имена и скрытые выборы не выводятся.

Точный набор Wallet: −5 каждому вступившему; win +8 победителю и receipt +2 shards
проигравшему; draw/expired/cancelled/publication_failed +5 каждому. Неожиданные,
отсутствующие, повторные операции, частичный refund, неверный участник/результат/
receipt блокируют rollback. Баланс сам по себе не доказывает конкретное начисление.
При errors сохранить БД штатным SQLite backup и обезличенную диагностику для
разбора; не исправлять SQL-статус/баланс по результату одной проверки.

## Drain

Включение/выключение — только явное действие с --confirm. Нужен доверенный
checkout новой версии с зависимостями; --project только для вызова существующих
экономических сервисов. Read-only helper не зависит от checkout.

    python tools/pvp_ops.py --db /trusted/copy.db --project /trusted/new-checkout pause-new --confirm
    python tools/pvp_ops.py --db /trusted/copy.db drain-status
    python tools/pvp_ops.py --db /trusted/copy.db --project /trusted/new-checkout expire --confirm
    python tools/pvp_ops.py --db /trusted/copy.db --project /trusted/new-checkout resume --confirm

pause-new атомарно пишет SQLite режим и возвращает ставки open. create и accept
проверяют режим внутри BEGIN IMMEDIATE, включая старые callbacks. choosing
продолжает выбирать, watcher/expire обслуживают сроки/выплаты/refunds. Solo, Hero
Duels, Boss, Tower не отключаются. Повторный pause безопасен; restart/процессы
видят один режим. Миграция сама maintenance не включает.

При повреждённом stake ledger pause всё равно закрывает новые входы, но не
угадывает возврат спорной игре; она остаётся для разбора и блокирует rollback.
expire обрабатывает реальные истёкшие сроки и request leases без Telegram API.
Choosing deadline не ускоряется, живой lease нужно дождаться. До expire/resume
экономический аудит должен пройти. resume не повторяет отправки или награды.

## Доставка и ручной разбор

Одна первоначальная попытка send, timeout 25 секунд, lease 45. Timeout/crash/пустой
message_id закрывают игру с refund и publication=uncertain. API мог уже доставить
сообщение: повторный send запрещён. Его кнопки проверяют закрытую игру. Участнику
доступна «Последняя игра» в личном меню. Поздний ack сохраняет ID без оживления игры.

Известный ID редактируется с backoff 10/30/60/120/300/900/900 секунд. 8-я
последовательная ошибка — exhausted; 400/403 — permanent, 429 уважает retry_after.
Очередь переживает restart; успех сбрасывает счётчик. Новая ревизия не обходит
permanent/exhausted. Экономика сохраняется при недоступном Telegram. Лог возникает
на переход ошибки/истечение lease, а не на каждый пропущенный watcher tick.

После экономической сверки терминальной игры оператор может явно подтвердить
разбор публикации либо прекратить edit:

    python deploy/pvp_ops.py --db /trusted/copy.db review-publication --session 42 --note "INC-123 publication checked" --confirm
    python deploy/pvp_ops.py --db /trusted/copy.db abandon-edit --session 42 --note "INC-123 deleted message checked" --confirm

review-publication не превращает uncertain в успех и ничего не отправляет.
Pending терминальной игры без send claim помечается failed. abandon-edit доступен
только известному сообщению терминальной игры без живого lease, переводит edit в
permanent и пишет review. Активная игра/живой запрос/экономическая ошибка не
позволяют скрыть риск. Меняются только служебные поля со временем/заметкой.
Не помещать токены, имена или скрытые выборы в note.

Legacy win до safety не имел shard receipt. Диагностика выдаёт
legacy_shards_unverified даже при правдоподобном балансе. Сначала сверить внешние
доказательства конкретной выдачи, затем при наличии доказательств:

    python deploy/pvp_ops.py --db /trusted/copy.db review-economy --session 42 --note "INC-123 legacy shards evidence checked" --confirm

Это разрешает только единственную неопределённость legacy shard history:
фиксируется свидетельство оператора, warning остаётся, receipt и ресурсы не
создаются. Другие нарушения Wallet/receipt так разрешить нельзя. Без доказательств
оставить блокировку и разбирать восстановление отдельно.

## Rollback

pause-new → завершение choosing/истечение → разбор публикаций → rollback-check.
Для новой схемы paused обязателен даже при пустой очереди. До будущего
согласованного релиза обновить root deployment bundle: старый bundle не получает
защиту автоматически. См. [deployment](../deployment.md).

rollback() останавливает bot/watcher/update и вызывает frozen helper до checkout,
перед checkout и после старых checks перед startup. Ошибка/изменение БД блокирует
переход; при последнем отказе старый HEAD может уже стоять, но бот не запускается.
Сторонние SQLite writers исключаются на всё окно. Override и DB restore нет.
Если новый бот не стартует, standalone диагностика доступна; экономическое
завершение — сервисами проверенного нового checkout либо ручное восстановление
при остановленном стеке.

# Неопределённая Telegram-доставка: ручная сверка

SQLite commit и Telegram API не атомарны. Модель: pending → sending → sent/uncertain.
При crash sending восстанавливается как uncertain, pending допускает один durable claim.
Слепой retry sending/uncertain запрещён. Нет обещания exactly-once внешнего API.
Этот документ не разрешает live операции; реальная сверка требует отдельного решения.

1. Сохранить приватный incident record: exact code SHA, тип публикации (Story/raid/PvP),
   session/raid ID, step/revision, timestamps из существующих логов (UTC и timezone),
   chat/thread, известный message_id или UNKNOWN, DB snapshot/hash. Если timestamp
   отсутствует в схеме, отметить UNKNOWN и взять время из private logs, не выдумывать.
   В публичном PR только обезличенные incident ID, агрегаты и reason codes; реальные
   chat/user IDs, тексты/токены туда не переносить.
2. Read-only сверить story_sessions + steps/votes/operations/publications либо
   raid state/attacks/rewards/ledger/publications. Не запускать polling/повтор выплаты
   и не изменять progression. Сопоставить message_id и видимый чат вручную: Bot API
   не предоставляет надёжного поиска всех потерянных sendMessage ответов.
3. Если сообщение существует и соответствует текущему step, использовать существующие
   кнопки: server-side step/visited/vote guard остаётся авторитетным. При старом step
   не голосовать повторно. Обычный read-only просмотр состояния не является новым
   переходом и не начисляет наград. Сохранить доказательство сверки в private incident.
4. Если доставка не доказана, удерживать uncertain и BLOCKED; не переводить её SQL-ом
   в pending/sent, не присваивать вымышленный message_id, не вызывать recovery вручную
   ради повторной отправки. Безопасный выход для Story — отдельно согласованная
   административная /story cancel с сохранением всех snapshots/steps, затем новая
   session_id только по решению группы. Это не повтор/выплата в прежней сессии.
5. Для raid/PvP не создавать наградную отмену ради очистки публикации. Нужна отдельная
   ручная процедура владельца с проверкой escrow/ledger и разрешённых идемпотентных
   команд. Существующий PvP review применяется только к своим документированным
   terminal-state условиям; он не является универсальным Story/raid bypass.
6. Для Stories/raid сейчас нет согласованного durable review поля. Incident record
   сам по себе не снимает guard: historical uncertain остаётся блокером автоматического
   rollback. При необходимости отдельное ТЗ на проверяемую процедуру восстановления;
   новые schema/mutation/auto retry в этом hardening не добавляются.

После отдельно разрешённого staging проверить crash до/после commit и сетевого ответа,
один publication claim, отсутствие новых step/reward receipts и приватную сверку.

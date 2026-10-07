# Recovery build

Чистая корневая структура проекта без вложенной копии проекта в `deploy/`.

Содержит:
- D&D Mini launcher / private buttons;
- daily;
- wallet;
- shop;
- gacha / collection / active hero;
- star upgrades and shard sale;
- Tower 200 floors, Equipment 300 items и Mini owner-команды (numeric owner ID);
- read-only `/admitems`;
- обычные D&D-команды с topic guards; legacy `/add`, `/del`, `/clean`, `/admclean` удалены.

Не содержит `.env`, `shpakdnd.db`, `timers.db`.

## Уведомления Mini V1.3.1

Титулы действуют по `mini_titles.expires_at` (UTC Unix seconds), без RAM-only
таймера. При startup и затем раз в минуту watcher ставит expiration intent в
`mini_public_notifications`. Отправленные уведомления хранят Telegram message_id;
повторный restart не отправляет их снова. Новая выдача отменяет старый pending
expiration и сохраняет отдельный title id.

`pending` доставляются после восстановления доступа к чату. `sending` прошлого
процесса переходят в `uncertain`; неоднозначная сетевая ошибка также оставляет
`uncertain` с `last_error`. Telegram sendMessage не поддерживает idempotency key,
поэтому такие записи не отправляются повторно автоматически. Если сообщение
требуется восстановить, сначала проверь фактическую публикацию в игровой теме;
после проверки отсутствия можно вернуть конкретный notification id в pending.
Простое массовое повторение uncertain-записей может создавать дубли.

Стартовые 3 билета и welcome intent сохраняются вместе с player. Сроки титулов,
балансы, inventory и onboarding claims не зависят от доставки уведомления.

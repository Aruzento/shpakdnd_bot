# Recovery build

Чистая корневая структура проекта без вложенной копии проекта в `deploy/`.

Содержит:
- D&D Mini launcher / private buttons;
- daily;
- wallet;
- shop;
- gacha / collection / active hero;
- star upgrades and shard sale;
- Tower 200 floors, Equipment 300 items и Mini `/super*` (numeric owner ID);
- read-only `/admitems`;
- обычные D&D-команды с topic guards; legacy `/add`, `/del`, `/clean`, `/admclean` удалены.

Не содержит `.env`, `shpakdnd.db`, `timers.db`.

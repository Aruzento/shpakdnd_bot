# D&D Mini V1.5 — утверждённые O01–O10

Статус: **В разработке** до авторского контента и живой приёмки. Все механические решения O01–O10 утверждены; повторного согласования не требуется. Приоритетный источник: [O01_O10_APPROVED_TZ.md](O01_O10_APPROVED_TZ.md), предоставленный как123.md. R01–R20 сохраняются за исключением явных новых решений.

Ветка codex/v1.5, тот же draft PR#8. Начальный проверенный HEAD dfe7df7311d9789469063c783d65f8390b281bcb; PR base/origin/main d077dd2a2c29ad420b138bf07af23c77f4674d0b. Оригинальное пользовательское дерево Z: и рабочая SQLite сохранены; merge/deployment/systemd/polling не выполнялись.

| O | Реализация | Статус |
|---|---|---|
| O01 | Две отмены; compensation P=floor(ceil(M/5000)*min(D,M)/M), largest remainder с player_id tie, forecast/reason/revision/audit/ledger/replay/races, cancelled без победных бонусов | IMPLEMENTED_VERIFIED |
| O02 | Первая подтверждённая контр-атака активирует100% даже приactual0; источник сохранён; legacy positive-only snapshots не переписываются | IMPLEMENTED_VERIFIED |
| O03 | Строгий контракт ровно4Boss, PNG/Shadow/features/counter, empty-catalog UI и temporary-fixture tests готовы | BLOCKED_BY_INPUT: реальные4records+4PNG |
| O04 | Раздельные normal/raid, none/not_configured без normal leak; typed effects/ссылки/поля проверяются | IMPLEMENTED_VERIFIED; авторские ordinary raid definitions добавляются позднее |
| O05 | Eclipse10–15% original HP один раз, current/max увеличиваются, p/gain/stars frozen, фонды R09/O01 original | IMPLEMENTED_VERIFIED |
| O06 | Независимый Star-бросок каждому eligible normal Boss; Tower player/floor once; durable journal, no admin/banish/loss/Raid/PvP | IMPLEMENTED_VERIFIED |
| O07 | Golden/Thief/Blessing стандартной награды; независимые источники не умножаются/не дублируются, chest сохраняется; floor draw frozen с первого входа | IMPLEMENTED_VERIFIED |
| O08 | Inventory/manual active/manual reserve,75currency/8distinct/no ninth,active-time care/growth/claim/equipment,explicit confirmation, evolution_pending | IMPLEMENTED_VERIFIED; взрослые арты/records — вход следующего этапа |
| O09 | Import/schema/PNG/unique/references/strict0/1/2 проверены на temporary fixtures; fake production content не создаётся | BLOCKED_BY_INPUT: авторский комплект |
| O10 | Solo3обычных щита; отмена→Ice→Lunar→обычные; zero не даёт on-hit/Arise, extra без рекурсии, explicit Shadow target, restart/reward protection | IMPLEMENTED_VERIFIED |

IMPLEMENTED_VERIFIED означает трассировку и изолированные автоматические проверки, а не выполненную Telegram/релизную приёмку. Финальный exact SHA, полные count/skip/exit/log и Linux3.11/3.13 фиксируются в итоговом отчёте после последнего коммита. Подробная матрица O→handler→service→SQLite→UI→test: COMPLETION_AUDIT.md.

Активная policy schema_version2/statusAPPROVED. PROVISIONAL остаётся только в immutable legacy constants и именах старых frozen policies для совместимости, не как открытое решение владельца. Никаких BLOCKED_BY_RULE по O01–O10 больше нет. Strict content пока BLOCKED_BY_INPUT/exit2, отдельно от технических проверок.

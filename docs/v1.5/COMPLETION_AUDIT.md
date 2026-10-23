# Независимый completion-аудит V1.5

**В разработке**. Источники: весь master prompt от23.10.2026 (712 строк с R01–R20), затем обязательный completion audit prompt; поздние R20.10–R20.12 приоритетнее черновиков. Реальные Telegram, staging/production и рабочая SQLite не запускались.

## Исходная точка и границы доказательства

Worktree: `C:/Users/Aruko/.codex/worktrees/v15/shpakdnd_bot`, существующая `codex/v1.5`, тот же draft PR #8, remote `https://github.com/Aruzento/shpakdnd_bot.git`. Проверенные base/origin/main: `d077dd2a2c29ad420b138bf07af23c77f4674d0b`; начальный HEAD `c4dfb0f687dc860f6c080505b6c6401006d44eb8`. Baseline Windows/Python3.13: **1973 теста, 0 failures/errors, 19 platform skips, 652.842s, exit0**. Log: `C:/Users/Aruko/AppData/Local/Temp/dnd-v15-audit-baseline-c4dfb0f-cxbztsux/output.log`.

Исходный Z: checkout на локальном main `f06c0d1` не менялся: пользовательские boss/content/bosses.json, content/heroes/shadow.json, пять Shadow PNG, `.worktrees/`, docs/balance и docs/planning сохранены. Данные/секреты оригинала не использовались тестами. Аудит сравнивает feature с фактической базой, не предполагает актуальность исторического SHA.

DONE означает проверенный доступный программный контракт на фикстурах. BLOCKED_BY_INPUT — нет реальных авторских файлов. PARTIAL — реализуемая техническая часть готова, но имеется явно неутверждённое правило/внешняя приёмка. Промежуточный зелёный тест не заменяет final-SHA evidence. SHA/count/log/OS/Linux URL сообщаются после последнего коммита в итоговом отчёте и PR.

## 00–09: контракт → обработчик → сервис → SQLite → тест

Все пути относительны корню. Stories router включён в `app/handlers/__init__.py`; Mythic Mini router включает Raid. `bot.main` восстанавливает публикации перед polling, сам polling при проверках не запускался.

| Блок / статус | Реальная цепочка | SQLite / frozen state | Доказательство |
|---|---|---|---|
| 00 DONE | startup→mini/schema→migrations/v1_5; combat/modes.freeze_hero | Аддитивные поля/таблицы, legacy/15, два frozen режима | PersistenceMatrix, RaidBoundaries restart, validate_v15_migration: 3 replay, nonzero fragments, integrity/FK, старый код на отдельной копии |
| 01 DONE | village.handlers.home/action/screen→service.get_state/settle/mutate; gacha UI→общий perform_gacha_pull ×1/×10 | residents/stars/food/boost/fractional units; copies/pity/shards атомарны | Старые Village/Gacha/UI, GachaProgression: большие copies, пачка, без ретрокомпенсаций; реальные worker pages/hourly_income |
| 02 DONE | Editorial decorators/schema/defaults/hot reload; ui/hero_cards/limits | Прежний storage; UTF-16 budgets | UI/editorial/hot reload/screen contracts и PipelineAudit; старые Tower fixtures без runtime читаются |
| 03 DONE | boss.handlers/v15_handlers/superadmin→service/combat/operations | original HP/drop, timeout900 только новым, started quota, timeline/audit/snapshots | BossTests/старые combat/registration: 14 значений, квота/Москва/старый deadline/skip/change/history |
| 04 BLOCKED_BY_INPUT | tools/import_v15_s5→catalog validator→hero sync/gacha | Настоящий manifest, активный каталог не подменён | AssetMatrix/hero validator; требуются 17 PNG до полного импорта |
| 05 DONE | boss.calculations/runtime + tower.combat→5 normal hero_abilities effects | Frozen definition и hero state; raid не задан→none | test_new_hero_passives, NormalMatrix, PipelineAudit; сами S5 арты отдельно заблокированы |
| 06 PARTIAL | raid.handlers.action/public_open/publish→service.start/attack/cancel/state→effects/rewards | Unique active/world, hero/day, actual events/damage, revision/reward/publication/audit | RaidTests/RaidBoundaries/RaidMatrix/TeamAriseAudit/TelegramAudit: concurrency/R09/rollback/UI/restart. Нет 4 Boss records/PNG; O01 выплата BLOCKED_BY_RULE |
| 07 BLOCKED_BY_INPUT | mythic.handlers.action/send_pet→pets.buy/activate/care/consume/claim/settle | inventory/active/reserve, active_seconds/CD/protection, player/Moscow day limits | PetTests и реальный handler→consume→DB replay; взрослый unlock ждёт настоящего героя/PNG |
| 08 PARTIAL | hero upgrade→mythic.upgrades/wallet; Boss/Tower/Raid adapters; Duel UI/service reject | Universal ledger, legacy fragments, frozen stars/16 свойств/12+4 arcana | NormalMatrix/RaidMatrix все звёзды, PvpBoundary, upgrade cost300; нет8 records/24PNG, O05–O07/O10 PROVISIONAL |
| 09 BLOCKED_BY_INPUT | /story/callback/PendingStoryCharacter→enrollment+service→publication | Общие story_* и scope/session/step/user; настоящие characters/profiles; frozen DAG/votes | StoryTests/StoryIntegration/TelegramAudit: private wizard/text replay/mixed group/tie/kick/restart; нет авторских JSON |

## Каждый R01–R20

| Решение / итог | Обработчик/сервис → состояние | Автоматическая проверка |
|---|---|---|
| R01 DONE | boss.start_battle/clock/operations→frozen timeout; gacha progressive base*(copies_after−1), no cap | BossTests.test_new_timeout_and_legacy_preserved; GachaProgression |
| R02 DONE | boss/content/bosses.json14 HP/drop/potion; ready→fighting world/Moscow quota под lock; без второго ×1.5 | Catalog tests; BossTests.test_ten_started_quota_and_moscow_midnight; rewards |
| R03 DONE | boss_abilities training dummy: successful event→10% original max HP, clamp | BossTests.test_training_percent_and_hp_history; damage precedence |
| R04 DONE | handlers/registration/service/combat: registered creates, participant starts, creator cancels before start, solo | Registration/combat tests и новые quota/solo/server permissions |
| R05 DONE | v15_handlers.skip→operations.admin_skip: specific token/confirm/audit, не timeout/fake hit | BossTests.test_admin_skip_does_not_count_timeout_and_is_stale_safe; no-hit/zero Arise |
| R06 BLOCKED_BY_INPUT | 5 normal definitions/engine и raid none реализованы, importer требует настоящий полный комплект | New hero passives/SnapshotSafety/AssetMatrix; не хватает17 PNG |
| R07 DONE | Village personal actual income/pages; Editorial schema/hot reload; UTF-16 limits | Village/UI/Editorial tests и PipelineAudit |
| R08 DONE | raid service: active/world,10/day,owned hero1/day/raid,free order,multiday | RaidTests/RaidBoundaries: Москва/concurrency/revision/replay |
| R09 DONE | allocate→_finalize: positive actual,ceil(original/5000),largest remainder,1+15/10/5,tie once | ArithmeticTests/finalization/overkill/concurrent/rollback/Telegram saved ranking |
| R10 BLOCKED_BY_INPUT | first_egg после actual positive raid hit; buy75 из8remaining; _unlock не создаёт fake hero | PetTests/PersistenceMatrix.first_egg_required/Telegram receipt; нужны реальные records/PNG |
| R11 DONE | pets.settle/care/activate:72h/96h,threshold30/80,mood70,−3/8h,sleep16h,reserve freezes | PetTests growth/care/reserve/boundaries |
| R12 DONE | pets.damage_delta=mood−50 только Boss/Raid snapshots; Tower/PvP не получают combat pet buff | Pet/effect tests и проверенные adapters |
| R13 DONE | eatask/eat→consume:confirm,unequipped quantity,shared limit5,1shard,mood unchanged | TelegramAudit.test_pet_equipment_confirmation_handler_and_replay; PetTests equipment |
| R14 DONE | manual claim,active adult mood100,player/Moscow day1,no accumulation | PetTests.test_claim_shared_day_and_atomic_upgrade_total |
| R15 DONE | old craft/grants закрыты, fragments без конверсии; universal ledger отдельно | tests/v1_4/test_mythic.py; populated migration/old code |
| R16 PARTIAL | 8normal+8raid,12+4arcana,fixed ATK1/2/2/3/2/3/1/1,1→6cost300; frozen provisional policy | NormalMatrix/RaidMatrix все уровни; PipelineAudit/upgrade; нет records/PNG и утверждения O06/O07 |
| R17 DONE | real D&D/Mini accounts; level1/freeform race/class как /create и /charset; private original scope | StoryIntegration и TelegramAudit private wizard duplicate text |
| R18 BLOCKED_BY_INPUT | DAG/hot discovery; all votes,immutable,kick/tie once,zero waits,snapshot/replay/publication | StoryTests/mixed accounts/parallel votes/restart; реальных сценариев нет |
| R19 BLOCKED_BY_INPUT | PNG CRC/1:1/фон/name/path,shared adult,importer/strict diagnostics | AssetMatrix; strict exit2; точные имена в CONTENT_IMPORT_CHECKLIST |
| R20 PARTIAL | Утверждённая арифметика реализована; открытые параметры отдельно | Подматрица ниже, O01/O02/O05–O10 не считаются утверждёнными |

### R20.1–R20.12

| № | Конкретный путь / проверка |
|---|---|
| .1 | boss.team.initialize freezes original_coins; operations.change не меняет базу. Admin frozen test |
| .2 | team.coins: cap60% Драгхара по starting presence, −25п.п.Soul Seal, steals/boss loss, floor0, затем Lunar. PipelineAudit frozen example75 при late drop200 |
| .3 | Boss primary/extra/runtime и Tower extra запускают allowed passive/class hooks, nested extra не рекурсирует. NormalMatrix Lightning и PipelineAudit Tower class/no-recursion |
| .4 | Normal shared Dark owner+hero + один Lunar Shadows при старте, personal/lantern отдельно, independent until success/max1. TeamAriseAudit и старые Shadow/Lantern |
| .5 | Raid first positive own snapshot owner+hero, no day/upgrade duplication, shared Dark всем positive; normal victory HP0/validShadow only. Atomic/concurrent/replay/zero/missing target tests |
| .6 | modes overlays сохраняют physical/magic и один наибольший elemental multiplier; initial team elements действуют сразу. Arithmetic/SnapshotSafety/PipelineAudit |
| .7 | ordinary percentages additive bps/integer floor; HP-special отдельно. Arithmetic/effect matrix |
| .8 | Fixed Mythic attacks, stars меняют свойства,cost20/35/55/80/110=300. Arithmetic/upgrade |
| .9 | Duel UI filter+callback/service reject before escrow; Events PvP прежний RPS без hero selection. PvpBoundary/весь старый PvP; временные PvP arcana не внедрялись |
| .10 | Raid Earth personal actor exception, прочие extra triggering attacker; Sun/zero/Eclipse/counter. RaidMatrix115+15/overkill/regen. Открытые параметры не догаданы |
| .11 | original60%/−25п.п.,отдельные потери,floor0,positive Lunar last. Admin/PipelineAudit frozen example |
| .12 | Каждый event clamp к оставшемусяHP, следующий видит новыйHP,no paid overkill/no double attribution,one transaction. Final/parallel/rollback tests |

## Найдено и исправлено

1. Shared Dark Arise отсутствовал у союзников в Raid и normal. Теперь team sources frozen, personal отдельно, max1/player; первый positive snapshot owner+hero за raid не меняется из-за дней/upgrade/JSON. Старый V15 fallback использует только saved participants; legacy<15 сохраняет прежний путь. Village settles перед Shadow stars.
2. Normal Duplicate Echoes попадал в Raid ATK: freeze теперь принимает mode; raid none не заимствует normal damage.
3. Initial elemental overlays действуют сразу; Draghar cap зависит от starting presence, не rescue; Star не платится при banishment.
4. Tower Sun neutralization до faction; extra warrior/beast/class hooks без рекурсии; отдельные Ice/Lunar shields и совместимость UI snapshots без runtime.
5. Arise требует positive actual HP: normal total_damage, новые Tower actual_hit_count отдельно от attempt hit_count. **Условие прежних победных пассивок, например holy_relic, сохранено**. Полная регрессия поймала слишком широкое ограничение; оба исходных теста сохранены и проверены.
6. Story private wizard раньше обрывался: input scope отдельно от authoritative Story scope, реальные D&D rows, text message idempotency. Race/class freeform соответствует существующему API, собственный enum не вводился. Посторонний ввод/команды не захватываются.
7. Некорректный Story start_node теперь ValueError и изоляция файла, не TypeError всего discovery.
8. Raid receipt показывает actual/attempts/egg/payout/Arise; final ranking использует saved rank, личные страницы; старая кнопка завершённого рейда не открывает новый.
9. Cancel оба UI/confirm/permissions/revision/idempotency/audit; O01 with_reward пишет отказ, без выдуманной экономики. Stale revision проверяется под lock.
10. Startup pending recovery Story/Raid; sending→uncertain без пересылки; orphan Raid publication row восстанавливается. Fixed world_id в recovered button, тест world_id≠raid_id.
11. O05–O07 вынесены в policies.json со статусом PROVISIONAL и frozen в Boss/Tower/Raid; legacy constants без live retroactivation, строгие типы/селекторы. Игровые числа сохранены.
12. UTF-16 budgets не разрезают surrogate pair. Blood_frenzy description соответствует R20.3.
13. Ctrl+C: Python-redactor и затем tee могли прервать поток, вызвав traceback/BrokenPipe и exit1. Только logger call защищает redactor INT/TERM и tee -i; основной bash сохраняет traps/cleanup/130/143. Original PTY tests/helpers неизменны; exact protected validator разрешает только эту замену. Deploy не запускался.

## Независимые блокеры и необходимые входы

**Правила:** O01 eligible/формула/валюта/размер/status/Arise отмены с наградой (BLOCKED_BY_RULE); O02 окончательное «присутствует» counter (сохранён first positive hit); O05 Eclipse basis/fund; O06 Star outcomes; O07 Lunar rewards/chest/anti-farm; O08 inventory/activation UX; O10 Tower частные сочетания/разрешённые targets. PROVISIONAL не означает утверждение.

**Авторские файлы:** O03 четыре реальных Raid Boss records/PNG/counter/features/Shadow links; O04 будущие explicit ordinary raid abilities (none допустим до авторского ввода); O09 17S5PNG+24MythicPNG+8records+StoriesJSON/доступный STORIES_DIR. Проверены Downloads/attachments/исходный проект/worktree, full independent content set не найден. Никаких fake production records/images.

**Технические ошибки** перечислены выше отдельно. Любой красный final CI — BLOCKED независимо от baseline. Production SQLite copy/реальная Telegram/staging приёмка НЕ ВЫПОЛНЕНЫ; не выдумываются и не выдаются за PASS.

## Проверка прежних тестов и области diff

Старые ожидания менялись только по новым утверждённым контрактам:900s/solo/14Boss values/admin eligibility/progressive copies/additive/floor/actual/extra/retired craft. Прежние craft-success tests заменены на запрет craft/сохранение17 legacy fragments/atomic universal ledger по R10/R15, не ради CI. D&D/PvP validators сравнивают все старые колонки/экономические строки и разрешают точный whitelist новых таблиц. Watcher добавляет только source dirs Stories/Raid, не внешний контент/SQLite. Workflow сохраняет весь unittest/real PTY/D&D shop/coins/loot/benchmark/historical migrations; старое «Mini не изменился в V1.4.4» неприменимо к явному V1.5.

Исходные118 файлов PR сверены по области изменений; новые audit tests/policy/limits/validator/docs добавлены адресно. Полный перечень с назначением ниже. Это проверка состава diff и интеграций, не утверждение выполненной визуальной приёмки.

| Файл | Проверенная область |
|---|---|
| `.github/workflows/linux-release.yml` | Linux3.11/3.13 full/PTy/disposable SQLite |
| `app/handlers/__init__.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/boss/boss_abilities/catalog.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/boss_abilities/engine.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/calculations.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/combat.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/content/bosses.json` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/handlers.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/notices.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/operations.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/presentation.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/public.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/registration.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/repository.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/rewards.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/runtime.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/schema.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/service.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/team.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/ui.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/v15_handlers.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/boss/v15_schema.py` | Normal Boss/права/арифметика/история/frozen state |
| `app/mini/catalog.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/combat/arise.py` | Общие боевые reducers/snapshots/16 свойств/Arise |
| `app/mini/combat/creatures.py` | Общие боевые reducers/snapshots/16 свойств/Arise |
| `app/mini/combat/hero_abilities/abilities.json` | Общие боевые reducers/snapshots/16 свойств/Arise |
| `app/mini/combat/hero_abilities/catalog.py` | Общие боевые reducers/snapshots/16 свойств/Arise |
| `app/mini/combat/hero_abilities/engine.py` | Общие боевые reducers/snapshots/16 свойств/Arise |
| `app/mini/combat/modes.py` | Общие боевые reducers/snapshots/16 свойств/Arise |
| `app/mini/combat/team.py` | Общие боевые reducers/snapshots/16 свойств/Arise |
| `app/mini/duels/handlers.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/duels/service.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/editorial/defaults/ru/glossary.json` | Editorial schema/defaults/hot reload |
| `app/mini/editorial/defaults/ru/rules.json` | Editorial schema/defaults/hot reload |
| `app/mini/editorial/defaults/ru/v15.json` | Editorial schema/defaults/hot reload |
| `app/mini/editorial/schema.json` | Editorial schema/defaults/hot reload |
| `app/mini/gacha.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/hero_upgrades.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/heroes.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/migrations/__init__.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/migrations/v1_5.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/mythic/content.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/handlers.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/pets.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/policies.json` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/policies.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/raid/__init__.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/raid/catalog.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/raid/contracts.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/raid/effects.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/raid/handlers.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/raid/rewards.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/raid/service.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/schema.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/service.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/upgrades.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/mythic/wallet.py` | Mythic/Raid/Pets/валюта/контентный контракт |
| `app/mini/schema.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/shadow.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/superadmin/handlers.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/tower/combat.py` | Solo Tower adapter/reward/replay/UI |
| `app/mini/tower/handlers.py` | Solo Tower adapter/reward/replay/UI |
| `app/mini/tower/repository.py` | Solo Tower adapter/reward/replay/UI |
| `app/mini/tower/service.py` | Solo Tower adapter/reward/replay/UI |
| `app/mini/ui/hero_cards.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/ui/limits.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/ui/registration.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/village/handlers.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/mini/village/service.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `app/stories/__init__.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/catalog.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/enrollment.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/handlers.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/presentation.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/publication.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/repository.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/schema.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `app/stories/service.py` | Общие Истории: handlers/service/snapshot/publication/SQLite |
| `bot.py` | Подключение/совместимость каталога,UI,Village/Gacha,upgrade,PvP boundary |
| `deploy/deploy-shpakdnd.sh` | Только Git watcher config/адресный logger fix; не запускался |
| `deploy/shpakdnd-bot-watch.path` | Только Git watcher config/адресный logger fix; не запускался |
| `docs/ui/v1.5-matrix.json` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/ADR_NORMAL_RAID.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/CHANGED_FILES.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/COMPLETION_AUDIT.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/CONTENT_IMPORT_CHECKLIST.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/DATABASE.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/IMPLEMENTATION_STATUS.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/RELEASE_AND_ROLLBACK.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/S5_IMPORT_MANIFEST.json` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/STAGING_ACCEPTANCE.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/STORIES_AUTHOR_GUIDE.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/TEST_MATRIX.md` | Документация/manifest/screen contracts, без fake content |
| `docs/v1.5/WORK_LOG.md` | Документация/manifest/screen contracts, без fake content |
| `scripts/run_isolated_checks.py` | Изолированные проверки/импортный инструмент |
| `tests/boss/test_abilities_integration.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/boss/test_combat.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/boss/test_combat_messages.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/boss/test_combat_v2.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/boss/test_creature_traits.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/boss/test_new_hero_passives.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/boss/test_registration.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/test_mini_migrations.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_3_2/test_boss_classes.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4/test_bosses.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4/test_catalog.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4/test_damage_precedence.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4/test_fixes.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4/test_mythic.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4/test_ui.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4_2/test_screen_contract.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4_2/test_screen_contracts.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_4_4/test_main_compatibility.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_5/__init__.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_5/test_boss_stories.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_5/test_completion_audit.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_5/test_core.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_5/test_effect_matrix.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tests/v1_5/test_integration.py` | Регрессии утверждённых контрактов; protected tests не ослаблены |
| `tools/check_v15.py` | Изолированные проверки/импортный инструмент |
| `tools/import_v15_s5.py` | Изолированные проверки/импортный инструмент |
| `tools/validate_deploy_bundle.py` | Изолированные проверки/импортный инструмент |
| `tools/validate_dnd_shop_release.py` | Изолированные проверки/импортный инструмент |
| `tools/validate_pvp_migration.py` | Изолированные проверки/импортный инструмент |
| `tools/validate_v15_migration.py` | Изолированные проверки/импортный инструмент |

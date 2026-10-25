# O01–O10: актуальная техническая трассировка

Правила окончательно утверждены в O01_O10_APPROVED_TZ.md. Исторический аудит R01–R20 на dfe7df7 сохранён отдельно: [COMPLETION_AUDIT_DFE7DF7.md](COMPLETION_AUDIT_DFE7DF7.md); его прежние ограничения правил заменены текущей матрицей. Ветка/PR прежние, base d077dd2a, начальный HEAD dfe7df7. Перед редактированием100V1.5тестов PASS16.690s; прежний полный baseline на этом SHA2001тест/19Windows skips уже проверен. Оригинальные пользовательские изменения не затирались.

## O → обработчик → сервис → SQLite → UI → tests

Пути относительны корню. Новые tests находятся в tests/v1_5/test_approved_o01_o10.py, реальные Telegram services используют disposable SQLite; неожиданный HTTP запрещён AiohttpSession.make_request.

| O / статус | Обработчик / сервис / игровые расчёты | SQLite и видимый результат | Автоматическое доказательство |
|---|---|---|---|
| O01 IMPLEMENTED_VERIFIED | raid.handlers.action(cancelask/cancelpage/cancelreason/cancelconfirm), PendingCancelReason/cancel_reason→request_cancel/cancel_request/confirm_cancel/cancel→rewards.compensation | cancel_requests токен привязан кworld/actor/revision; reason≤500, forecast всех выплат листается; audit/ledger/rewards/result/statuscancelled атомарны, source raid_cancel_compensation; public_text/screen/history показывают отмену, а не победу | CompensationArithmetic; ApprovedRaid partial/tie/D0/P0/D>M/replay/two-admin/last-hit-race/rollback/revision/permissions; ApprovedTelegram forecast/reason/scope/public/history |
| O02 IMPLEMENTED_VERIFIED | raid.catalog.COUNTER_POLICY→start freezes→effects.resolve→attack | state.counter_active/counter_source, audit actioncounter_activated один раз; первая matching атака100% дажеactual0; rollback не сохраняет активацию | ApprovedRaid counter_zero/rollback/legacy/midnight/restart; ApprovedContracts multi-element/full first; RaidMatrix legacy сохраняется |
| O03 BLOCKED_BY_INPUT | raid.catalog.validate/load, handlers.screen/home/start, content.report | Непустой каталог ровно4, PNG/HP≥100000/knownfields/Shadowlinks, отсутствующий каталог не создаёт рейд | ApprovedContracts4valid fixtures/partial/duplicate/unknown/badPNG/Shadow; реальных4records+PNG пока нет |
| O04 IMPLEMENTED_VERIFIED | catalog.passive_contract/ability registry→freeze_hero(mode=raid)→contracts.authored/validate→effects.resolve→attack ledger | normal/raid frozen independently, none не переносит duplicate_echoes/holy_smite/Arise/economy; строгие typed fields/trigger/key | ApprovedContracts normal leak/multielement/types; RaidMatrix explicit ordinary contract; old snapshot tests и реальные attack/state/ledger |
| O05 IMPLEMENTED_VERIFIED | raid.effects Lunar choice→Eclipse | lunar.arcana/owner/level/stars/percent/gain/original saved; max/current+gain once, R09F/O01P отoriginalM, overflow rejects whole transaction | ApprovedRaid Eclipse6levels/persistence/originalpool/overflow; RaidTests existing Eclipse/R09 |
| O06 IMPLEMENTED_VERIFIED | boss.rewards.grant_victory_rewards→star_rewards.award; tower.service.grant_reward→award | mini_star_rewards(mode,battle_id,player) records chance/stars/success/amount; Boss battle_id=BossID, Tower battle_id=floor. Каждому eligible отдельно; ordinary300 once, no forbidden outcomes | ApprovedNormal independent mixed outcomes/highestfrozen/eligible/adminNoHit/timeout/legacy/admin/banish/loss/rollback; ApprovedTower6chances/once/chest/restart/failed/legacy |
| O07 IMPLEMENTED_VERIFIED | team.coins/Boss reward adapter; Tower repository.create_attempt/service.grant_reward | Golden послештрафов/floor0; Thief10% standard; Blessingstandard→5mythic. Tower modifies base only, independent passive/Star separately, chestonce. first entry draw+policy in mini_tower_lunar_choices; cached mini_tower_rewards.result_json | ApprovedNormal Star+Blessing; ApprovedTower Golden6levels/Thief/Blessing/chest/baseonly/firstentry/hero-switch/noReroll; old frozen policy tests |
| O08 IMPLEMENTED_VERIFIED | mythic.handlers.buyask/buy/eatask/eat/reserve/activate/care/claim→pets | manual reserve explicit, inventory/active/reserve shown; clocks/CD/protection frozen, ledger/idempotency/shareddays, adult unlock remains evolution_pending without realhero | PetTests existing growth/care/threshold/claim/equip; ApprovedRaid8distinct/no9th/reserve3startup/integrity; ApprovedTelegram buyconfirm/replay/reservefreeze |
| O09 BLOCKED_BY_INPUT | import_v15_s5/read-onlydry-run; content.main/report; catalog/PNG validators; external Story discovery | strict exit0 onlycomplete,2missing,1invalid; all41knownhero PNG paths,4Bossimage names from pendingauthorrecords. S5manifest remains outside gacha untilwholepack | ApprovedContracts strictCLI temporary full45PNG+records+Story; removefile→2, corrupt→1; AssetMatrix; importer--help/dryrun |
| O10 IMPLEMENTED_VERIFIED | tower.combat→team.reward_attack; tower.catalog optional explicitShadow fields; service.grant_reward/Arise | Complete cancel first,Ice thenLunar then3standard; one source/event; old runtime retained, no invented users/targets. Valid explicitShadow+positive only, rewards unique | ApprovedTower shield16combinations/SoulSeal/new snapshot/positiveShadow/zero/no repeats; PipelineAudit existingextra/class/noRecursion/Sun/UTF16; legacy Tower regressions |

## Экономические определения, которые теперь обязательны

O01: M=original_max_hp,D=sumpositiveactual,F=ceil(M/5000),P=floor(F*min(D,M)/M). Распределение P пропорционально D_i с остатком descending и tieplayer_idascending. В result_json сохраняются M,D,F,P,tie_break,все вклады/размеры выплат,reason/actor/time. Записи wallet/reward только amount>0; нулевые доли остаются в result для прозрачности. При D0/P0 — информативный отказ и audit, active/revision/economy неизменны. Без награды — cancelled/rewards[], использованные атаки/законное яйцо не возвращаются и не отнимаются. Никаких +1/top3/Lunar/Star/Arise/killcoins/items.

O06/O07: Blessing заменяет **стандартную** награду. Star — отдельный ранее разрешённый источник: независимый успешный roll даёт300ordinary вместе с5mythic при Blessing, но не автоматически и не дважды. Golden/Thief не умножают Star или другие независимые passive shards Tower. Нормальный Boss Star eligibility использует прежний reward_eligible: admin no-hit не исключён, обычный виновный timeout без попадания исключён. Фонарь/Arise — отдельные правила, Blessing сам Arise не создаёт.

Tower: при первом входе любого героя сохраняется weighted draw наplayer/floor. Если Moon появится позднее, используется тот же index и фиксируются звёзды первого Moon; non-Moon сам по себе Moon-свойств не получает. Возврат Moon после поражения/смены героя не перебрасывает выбор. Policy этажа заморожена с первого входа. Одна выплата и один chest, result cached наplayer/floor.

## Совместимость

Новые бои используют reward_policy.version2 и active JSONstatusAPPROVED; старые Boss/Tower snapshots не обновляются миграцией и сохраняют version1 sharedStar/старый Tower расчёт. Новые raid snapshots явно хранят first_confirmed_counter_hero_attack_on_this_raid. Старый explicit positive-only policy и snapshots безcounter_policy продолжают old positive-only; active counter не сбрасывается. Нет массового UPDATE старых JSON/HP/payout.

Миграция добавляет только mini_star_rewards,mini_mythic_raid_cancel_requests и mini_tower_rewards.result_json. Старые fragments/grants сохраняются. Все деньги/финал/аудит делаются BEGINIMMEDIATE. Вклад каждого игрока/HP проверяются наSQLite int64 до записи; агрегаты/умножения компенсации используют arbitrary precision Python integer. Сеть вне транзакций, pending/sending/sent/uncertain contract прежний.

## Изменения старых тестов — обоснование

- O01 oldwith_reward alwaysblocked заменён наzero-pool refusal и реальную compensationintegration; исторические blockedoperation keys всё ещё не превращаются в выплаты.
- Direct cancel теперь требует expected_revision; две старые setup-отмены дополнены точнойrevision, assertпроArise/публикации сохранены.
- Policy validation использует validAPPROVED envelopeversion2, чтобы плохойfield отклонялся по содержимому, а не просто из-заlegacyversion.
- Старый migration fixture охватывает все прежние Mini tables; whitelist добавляет строго2новые. Отдельный новыйtest заполняет оба новых журнала, compensation/Star,nonzero legacy7 и проверяет3replays/integrity/FK.
- D&D shop validator whitelist расширен теми же2tables; сравнение всех старых значений сохранено. Protected deployment tests/helpers не менялись.

На первом preflight старый migrationfixture закономерно не знал2newtables; исправлены именно метаданные и добавлено непустое replay-доказательство. Новые проверки не удалялись/не skipались. Formatting-only изменения прежних Boss/Tower блоков сокращены с AST-equivalence assertion.

## Остаток

Механические O01/O02/O05/O06/O07/O08/O10 утверждены и реализованы. O03/O09 — только реальные авторские файлы следующего этапа; O04 typed contract готов, ordinary raid definitions автор добавит позднее. Никакого повторного согласования механик не нужно. Strict real content остаётся BLOCKED_BY_INPUT, статусрелиза «В разработке». Production SQLite copy/staging/real Telegram/deployment не выполнялись. Полные final-SHA результаты/CI ссылки — в итоговом отчёте после последнего коммита.

## Дополнение: утверждённые четыре Boss-механики

Трассировка mechanic JSON→catalog.validate_profile→start frozen snapshot/state→
effects.prepare/resolve/finish→attack atomic audit/ledger/revision/finalize→
handlers.public_text/action/publish→test_raid_mechanics полностью описана в
[RAID_BOSSES_MECHANICS.md](RAID_BOSSES_MECHANICS.md). Старые O01–O10 не переопределяются.
Реальные четыре Boss records не сочинены; четыре авторских арта уже сохранены.

# Creature traits: порядок и persistent contracts

Правила нового события включаются при create_boss_event: trait_rules_version=1.
Исторические события имеют 0 и сохраняют прежний Combat v2.
Старт snapshot-ит выбранные характеристики/attack/passive в той же транзакции.
Участник для counters: зарегистрирован и banished=0. Forced skips не исключают
его из состава. Смена активного героя и каталога не меняет snapshots.

## Ход героя

1. Reachability flying: ranged или special_trait=flying. Иначе damage=0,
   нет attack RNG, снятия magic shield, extra attacks или on-hit trait.
   Действие использует очередь и увеличивает обычный hit_count, как раньше.
2. Старый pipeline: attack snapshot (со stars) -> hero attack passive ->
   faction -> main boss ability damage hook -> potion rounding.
   Это полное старое значение HERO_FINAL_DAMAGE; оно сохраняется в diagnostics.
   Расположение существующего magic_shield/mechanism относительно зелья не меняется.
3. Feature armored отнимает аддитивные 10/20% от полного числа. Новые features
   не вмешиваются внутрь старого pipeline. Нулевой damage остаётся 0; положительные
   faction/armor множители округляются вниз с прежним минимумом 1.
4. Наносится damage; существующий after_attack hook сохраняет cadence для
   reachable zero-damage magic-shield ударов. Unreachable не вызывает hook.
5. On-hit poisonous/holy требует положительного HP damage и reachability.
   Poison не стакается и не наносит урон немедленно. Holy — один pending flag.
6. Старые blood_frenzy extras: attack -> faction -> main ability -> potion ->
   armor, без старых attack/kill hooks, но с новыми on-hit traits при попадании.
7. Любой HP=0 -> общая finish_boss_death. Undead сначала воскресает один раз
   с max(1, floor(max_hp*25/100)); это не victory и не kill reward.
   Окончательная смерть запускает прежний death ability/kamikaze и payout.
   Echo и poison пользуются той же процедурой.
8. Construct героя после собственного обычного действия (включая unreachable)
   имеет 10% chance восстановить один shield до текущего max.
   Если battle ended или shields full, RNG не вызывается.
   Extra/echo/timeout/paralysis не добавляют отдельный proc собственного действия.
9. Очередь продвигается один раз. Callback ожидает round/position;
   mutations, RNG result, counters, journal и rewards атомарны.

Один special_trait означает, что holy + flying у одного героя не существует.
Это уточнено пользователем: ranged holy достигает flying босса;
melee flying достигает, но самостоятельного Holy эффекта не получает.

## Ход босса и конец раунда

1. Pending mockery пропускает весь ход как раньше: не consumes Holy,
   не выполняет attack или boss-turn passives. Round всё равно завершён.
2. Holy pending снимается на следующей попытке атаки, roll=25% ДО damaging RNG.
   При miss нет critical/rapier RNG, impact, shield loss, reward passive/guard
   consumption или direct damage. Недамажащие paralysis/banishment и cadence
   boss_turns сохраняются.
3. При hit прежняя main ability и sequential attacks, reward passive/guard.
   Guard поглощает весь impact; poisonous обходит обычный reward shield,
   но не этот старый полный guard. Poisonous с обычным shield одновременно
   снимает shield и вызывает ОДИН canonical reward_decay; без shield и при
   rapier вызывается тот же decay один раз на impact.
4. Если награда уничтожена, end-turn эффекты уже не вызываются.
5. Прежняя hydra regeneration.
6. Poison: floor(max_hp*2/100), минимум 1. Pending flag снимается. Death ->
   resurrection или victory; при victory дальнейшие lifecycle hooks прекращаются.
7. Holy regeneration +25% max HP, если нет текущего demonic/demon героя.
8. Demonic corruption +10 процентных пунктов. Effective запас=0 -> прежний defeat.
9. Construct +10% max HP один раз в конце раунда, если нет technical.
   Выполняется и после mockery-skipped boss turn, но не после завершения боя.

Heal cap=max_hp. После banishment counters пересчитываются немедленно.
Holy miss не отменяет hydra, poison, holy, corruption или round regeneration.
После poison resurrection возможны последующие heal hooks этого же хода.

## Награда и SQLite

mini_bosses получает только четыре additive колонки:

- trait_rules_version INTEGER DEFAULT 0;
- feature_state_json TEXT DEFAULT '{}': boss_undead_revived, boss_poisoned,
  holy_miss_active;
- reward_temp_hp INTEGER DEFAULT 0;
- reward_corruption INTEGER DEFAULT 0.

Существующие reward_shields/reward_shields_max остаются current/max.
Новые таблицы/зависимости не нужны. Init не сбрасывает active state.
Новые start bonuses: один undead даёт 15 points temp HP, один armored даёт
+1 current/max shield. Повторный start запрещён status-check до бонусов.

Все величины награды — целые процентные пункты ORIGINAL reward (100 = full).
Real HP = reward_percent; effective HP = max(0, real + temp - corruption).
При impact тратится min(temp, reward_decay_percent), остаток уменьшает real.
Corruption не уменьшает real; при victory/admin victory она и temp очищаются.
Выплата = reward_coins * real // 100; temp не увеличивает payout.
Если coins=0, процентный запас всё равно защищает предметную награду.
Округление coins вниз используется только для display/payout, не для поражения.
Постоянная потеря награды при победе не восстанавливается.

Features/traits: common CREATURE_TRAITS и legacy alias в combat/tags.
Чистые hooks в combat/creatures.py; SQLite persistence через repository allowlist.
RNG: creatures.roll_success или injected roller, фиксируется effect plan.
Старые открытые metadata tags остаются допустимыми и inert, новые исполняемые
keys требуют shared vocabulary, hook, labels/descriptions и tests.

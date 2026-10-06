# Combat System v2 backend

Current hero and boss catalogs deliberately use placeholders. Hero/boss card
formatters and images are unchanged. Faction damage is currently 100%; current
boss ability is none.

## Hero selection and snapshots

Registration fixes the active hero as the initial battle selection.
select_battle_hero() can replace it in announced or ready, with ownership
checked under a SQLite write lock. The private boss menu offers a paginated,
ephemeral selector. start_battle() uses the selected participant hero; only a
legacy NULL selection falls back once to active hero. The start transaction
snapshots attack including stars and the five Combat v2 traits. Fighting battles
never recalculate those snapshots or use active hero as the combat source.
Existing hero passives continue to resolve by the fixed participant hero ID.

## Damage and rounding

Attack snapshot (already includes stars) -> hero passive -> faction percent ->
boss damage hook -> external bonus. Faction/mechanism use floor integer division
with a minimum of 1. Potion rounding retains the existing ceiling rule.
Magic shield explicitly blocks HP damage to 0, including potion bonuses; a
magical hero removes it but deals 0 with that attack.

Faction cycle: commoners -> beasts -> monsters -> warriors -> dark -> commoners.
Forward adjacent: 200%; backward adjacent: 25%; same, nonadjacent or neutral:
100%. Unknown codes are validation errors.

## Boss hooks and state

boss_abilities/ is independent of hero abilities/. Hooks return effect plans;
combat applies and logs them inside the same transaction. Implemented hooks:
battle_start, modify_hero_damage, boss_turn, after_boss_turn, boss_death.
Random rolls/target choice are injectable or patchable.

Keys: none, paralysis, critical_strike, banishment, shapeshifter, rapier,
hydra_regeneration, kamikaze, magic_shield, mechanism. Config is defined in
boss_abilities/abilities.json, optionally overridden by boss ability_config.
The boss event snapshots faction, ability, text, features and config at creation.

Pending mockery skips are consumed before any boss hook. Real turns run the
ability, sequential reward attacks, then after-turn effects. Reward destruction
uses the existing consolation rewards; admin finish retains its existing
all-registered victory behavior. Paralysis consumes the next hero turn
automatically and has separate events from timeout. Banishment keeps registration
and accumulated contribution; it never removes the last active participant.

hit_boss() returns pipeline diagnostics, boss events, forced skip events and
all reward events. Events are also stored as structured event_json in the
existing action log. Card layouts do not yet display the new traits or events.

## Additive migration

Hero: faction, damage_type, class_tag, attack_range, special_trait.
Boss: faction, ability_key, ability_text, features_json, ability_config_json,
ability_state_json.
Participant: hero_snapshot_json, forced_skip_turns, banished (hero_id and attack
already existed).
Action: event_json.

Initialization only adds missing columns with safe defaults. Runtime effects
reset only at the start of a new ready battle. Existing fighting state, message
IDs, participants, selected heroes, attacks, reward balances and logs survive.

The next content stage can assign real traits/abilities and update cards
separately; it requires no enum migration for new class, special or feature tags.

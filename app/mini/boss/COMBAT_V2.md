# Combat System v2 backend

The live hero and boss catalogs now include faction, class and ability assignments.
The boss catalog uses magic_shield, banishment, hydra_regeneration, mechanism,
shapeshifter, critical_strike and rapier. Base attack, existing passive keys,
boss HP, images and reward balance are unchanged.

Magic shield accepts the live class tag mage and the legacy tag magical.
The technical tag remains exempt from mechanism damage reduction. The vampire
shapeshifter rolls once at battle start, with target_faction beasts configured
on its template; faction then stays fixed. Public notices and descriptions match
these mechanics. Feature tags such as poisonous are metadata without extra
damage/status effects. Mini cards use shared Russian labels.

## Hero selection and snapshots

Registration fixes the active hero as the initial battle selection.
select_battle_hero() can replace it in announced or ready, with ownership
checked under a SQLite write lock. The private boss menu offers a paginated,
ephemeral selector. start_battle() uses the selected participant hero; only a
legacy NULL selection falls back once to active hero. The start transaction
snapshots attack including stars, the five Combat v2 traits, passive_key and
passive_text. Fighting battles never recalculate those snapshots or use active
hero as the combat source. All passive hooks read the frozen passive_key.

Migration fills missing traits of legacy fighting loadouts with the original
placeholders, never with live catalog traits. Missing passive keys are captured
once from the installed database before hero catalog synchronization. Existing
snapshot values, hero IDs and attack values survive. Snapshot freezing and
catalog synchronization share a SQLite write transaction.

## Damage and rounding

Attack snapshot (already includes stars) -> hero passive -> faction percent ->
boss damage hook -> external bonus. Faction/mechanism use floor integer division
with a minimum of 1. Potion rounding retains the existing ceiling rule.
Magic shield explicitly blocks HP damage to 0, including potion bonuses; a
mage or legacy magical hero removes it but deals 0 with that attack.

Faction cycle: commoners -> beasts -> monsters -> warriors -> dark -> commoners.
Forward adjacent: 200%; backward adjacent: 25%; same, nonadjacent or neutral:
100%. Unknown codes are validation errors.

## Boss hooks and state

boss_abilities/ is independent of shared ../combat/hero_abilities/. Hooks return effect plans;
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
Banished participants no longer run boss-attack passives such as emergency_salvage,
but retain their existing reward eligibility.

hit_boss() returns pipeline diagnostics, boss events, forced skip events and
all reward events. Hero actions remain in mini_boss_actions; boss abilities use
mini_boss_events with nullable target_player_id and no fabricated player actor.
Existing boss ability action history is preserved and copied once into this
canonical journal, with targets taken from event_json rather than the old actor.

Callbacks, menu recovery and the watcher share notice formatting. Paralysis
notices distinguish forced skips from the four-hour timeout. Public turn messages
use the current participant's battle hero photo, with a text fallback if the
image is unavailable. The publisher retains its lock and state checks; an
uncertain Telegram API response never triggers a second fallback send. Persisted
turn_notice_json keeps ability notices visible after watcher caption updates.

## Additive migration

Hero: faction, damage_type, class_tag, attack_range, special_trait.
Boss: faction, ability_key, ability_text, features_json, ability_config_json,
ability_state_json, turn_message_kind, turn_notice_json.
Participant: hero_snapshot_json, hero_state_json, forced_skip_turns, banished (hero_id and attack
already existed).
Action: event_json. New mini_boss_events journal with legacy_action_id for
idempotent history migration.

Initialization only adds missing columns with safe defaults. Runtime effects
reset only at the start of a new ready battle. Existing fighting state, message
IDs, participants, selected heroes, attacks, reward balances and logs survive.

Future content updates can change traits/abilities without changing running
loadouts; new class, special or feature tags require no enum migration.


## Release hardening and offline content checks

panic_dungeon_engineer is an exclusive compensation hero with active=false.
The flag controls ordinary gacha availability only: existing ownership and stars
survive catalog sync, admin grants (individual and ALL) still accept its code,
and the owned hero remains selectable/upgradable with emergency_salvage intact.
No schema migration or runtime battle reset is required.

app/mini/content_safety.py is called by check_bot.py, not by combat hooks.
Active boss factions require an ACTIVE faction counter; neutral is exempt because
it has no advantageous matchup. Active magic_shield requires an ACTIVE mage or
legacy magical hero, and mechanism requires an ACTIVE technical hero. All current
hero tags (including inactive owned heroes) and boss feature/faction tags must
have readable Russian labels. Structural catalog validation still accepts new
open class/special/feature tags; release validation additionally requires labels.

RULES_TEXT explains separate battle selection and the faction cycle. slashing
keeps its internal code and displays as Режущий.

check_bot.py reports a nonblocking WARN for rapier + reward_shields=0.
The user assigned fallen_sun_champion 420 HP and 2 shields; this configuration
now passes without that warning. Content balance remains user-owned.


## New hero effects

The six passives are configured in hero abilities.json, never by hero code.
Runtime uses the additive participant hero_state_json column (default {}).
Only a new ready battle resets it; migration/reinitialization preserves running
snapshots, counters, HP, rewards, queue position and all existing runtime values.

* rune_spark: 20% on an ordinary attack to remove an active magic_shield. The
  removal attack deals zero HP damage; the class hook runs normally on non-proc.
  A proc replaces the class removal, producing one event.
* unstable_shell / infernal_guard: every fourth / third own ordinary hit creates
  a reward guard charge, even when HP damage is zero. Real reward impacts first
  run boss_attack hero passives, then consume one active participant's charge in
  queue order. Critical impacts consume charges sequentially; rapier is absorbed
  too. A mockery skip runs neither attack passives nor charge consumption.
* holy_relic: each participant with hit_count > 0 rolls 15% for +3 shared shards
  inside normal victory rewards. Banished contributors remain eligible, phantom
  participation without hits does not. reward_granted makes the roll/grant
  atomic and once per battle, including restarts. Failure/admin victory bypass it.
* battle_echo: each third own ordinary hit stores 50% of actual HP removed,
  minimum 1 for positive damage. The next actionable own turn consumes it before
  input, without attack, faction, class, potion or kill hooks and without a hit
  increment. Forced skips preserve it; banishment prevents it. Actual echo damage
  increases total_damage. Echo death follows the shared boss-death/victory path.
  The journal and turn_notice_json preserve its automatic public event.
* blood_frenzy: ordinary hit numbers cycle through 100/115/130/145% at the passive
  stage. Every fourth hit requests one extra attack unless the primary killed.
  Extra starts from the attack snapshot, then faction, boss hook, potion; it
  neither increments hits nor invokes hero attack/after-attack/kill hooks.

Existing main-hit total_damage accounting remains unchanged. New automatic and
extra hits record actual HP removed; echo is based on primary actual HP damage.

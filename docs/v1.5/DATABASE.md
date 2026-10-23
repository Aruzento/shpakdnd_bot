# SQLite V1.5

Миграция `app/mini/migrations/v1_5.py` выполняется последней в существующем ordered migrate. Она вызывает Mythic/Story CREATE/ALTER под транзакцией init_mini_db. Обычный Boss расширяет существующий init_boss_db. Старые колонки и данные не удаляются, три повторных запуска проверяются автоматически.

Новые таблицы:

- `mini_mythic_ledger`: отдельная универсальная валюта, UNIQUE(player_id,operation_key), balance_after>=0.
- `mini_mythic_operations`: точный payload/result для повторов расходных pet/upgrade действий.
- `mini_mythic_pets`: UNIQUE(player_id,element), partial unique один location=active; mood/stage/активные часы/защита/CD/evolution_pending.
- `mini_mythic_pet_days`: player_id + московская game_day, consumed<=5, claimed<=1.
- `mini_mythic_raids`: immutable snapshot и исходный HP, runtime/revision/result; partial unique active/world.
- `mini_mythic_raid_attacks`: unique operation/player и unique raid/player/hero/day; frozen hero + HP events + сохранённый результат.
- `mini_mythic_raid_damage`: фактический вклад player/raid, включая отдельно атрибутированный Земной урон.
- `mini_mythic_raid_rewards`: unique raid/player, сохранённые rank/damage/amount/result.
- `mini_mythic_raid_audit`: start/cancel actor/payload/key, UNIQUE operation_key.
- `mini_mythic_raid_publications`: pending/sending/sent/uncertain + message_id/media_type; неизвестная доставка не повторяется автоматически.
- `mini_tower_lunar_choices`: unique player/floor, первый Лунный выбор.
- `mini_combat_arise`: unique mode/battle/player, независимые проверки источников до первого успеха.
- `mini_boss_admin_audit`: scoped actor/key/payload/result для skip/change.
- `mini_boss_timeline`: общая autoincrement-последовательность actions/events. Два AFTER INSERT trigger только для rules_version>=15; старая история не переупорядочивается догадкой.
- `story_sessions`, `story_participants`, `story_votes`, `story_steps`, `story_publications`, `story_operations`, `story_enrollment`: независимое общее прохождение и регистрация, partial unique active chat/thread, immutable vote/session/step/user.

Новые столбцы: mini_players.mythic_shards (0, nonnegative); mini_heroes.passives_json/mythic_element; mini_bosses.rules_version (legacy0), turn_timeout_seconds (старый skip_after_hours×3600, новым900), team_state_json, original_max_hp, original_reward_coins, reward_snapshot_version. При очень старой минимальной boss-схеме недостающие starts_at/skip_after_hours добавляются без изменения существующих deadlines. Номера версий normal snapshots и контрактов отделены от gameplay content_version.

Основные индексы: mini_pet_one_active, mini_raid_one_active, mini_raid_daily, mini_boss_daily_started, story one-active scope и перечисленные PRIMARY/UNIQUE keys. Все экономические действия и боевой шаг используют BEGIN IMMEDIATE; HP, попытка, вклад, награда и idempotency фиксируются одной транзакцией. Telegram-сеть находится за её пределами, публикация предварительно получает persisted claim.

Исторические mini_mythic_fragments/mini_mythic_grants остаются. Ни миграция, ни новое API не выдумывают курс конверсии; старый craft/grant-per-hero явно отклоняются. Обычные shards/coins/equipment имеют прежних владельцев записи.

import asyncio

from app.mini.boss.combat import BossCombatError, advance_expired_turns, list_fighting_bosses
from app.mini.boss.public import refresh_public_boss
from app.mini.worlds import get_mini_world_by_id


async def boss_watch_loop(bot, *, interval_seconds: int = 60) -> None:
    """Фоново пропускает просроченные 4-часовые ходы и обновляет карточку боя."""
    interval_seconds = max(10, int(interval_seconds))

    while True:
        try:
            for boss in list_fighting_bosses():
                try:
                    result = advance_expired_turns(int(boss["id"]))
                    if not result["changed"]:
                        continue
                    updated = result["state"]["boss"]
                    world = get_mini_world_by_id(int(updated["world_id"]))
                    if world is not None:
                        await refresh_public_boss(bot, world, updated)
                except BossCombatError as error:
                    print(f"Boss watcher: boss={boss.get('id')} error={error}")
                except Exception as error:
                    print(
                        "Boss watcher: ошибка обновления босса "
                        f"{boss.get('id')}: {type(error).__name__}: {error}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Boss watcher: {type(error).__name__}: {error}")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise

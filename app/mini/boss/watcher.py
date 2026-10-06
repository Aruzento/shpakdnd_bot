import asyncio

from app.mini.boss.notices import timeout_event_lines
from app.mini.boss.combat import (
    BossCombatError,
    advance_expired_turns,
    list_fighting_bosses,
)
from app.mini.boss.public import (
    ensure_public_turn,
    refresh_public_boss,
    replace_public_turn,
)
from app.mini.worlds import get_mini_world_by_id


def _timeout_notice(result: dict) -> str:
    return "\n".join(timeout_event_lines(result))


async def boss_watch_loop(bot, *, interval_seconds: int = 60) -> None:
    """
    Фоново пропускает просроченные ходы и поддерживает публичное сообщение боя.

    На первом проходе также восстанавливает публичный ход уже идущего боя.
    Поэтому обновление бота не требует отменять или запускать босса заново.
    """
    interval_seconds = max(10, int(interval_seconds))
    first_pass = True

    while True:
        try:
            for boss in list_fighting_bosses():
                try:
                    result = advance_expired_turns(int(boss["id"]))
                    updated = result["state"]["boss"]
                    world = get_mini_world_by_id(int(updated["world_id"]))
                    if world is None:
                        continue

                    if first_pass or result["changed"]:
                        await refresh_public_boss(bot, world, updated)

                    if result["changed"]:
                        await replace_public_turn(
                            bot,
                            world,
                            updated,
                            notice=_timeout_notice(result),
                        )
                    elif first_pass:
                        await ensure_public_turn(bot, world, updated)
                except BossCombatError as error:
                    print(f"Boss watcher: boss={boss.get('id')} error={error}")
                except Exception as error:
                    print(
                        "Boss watcher: ошибка обновления босса "
                        f"{boss.get('id')}: {type(error).__name__}: {error}"
                    )
            first_pass = False
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Boss watcher: {type(error).__name__}: {error}")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise

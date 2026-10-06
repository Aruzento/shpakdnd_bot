"""Combat notices shared by hit callbacks, home recovery and the watcher."""
from app.mini.presentation import faction_label


def _target(event: dict, participants: list[dict]) -> str:
    player_id = event.get("player_id")
    row = next((p for p in participants if p.get("player_id") == player_id), {})
    return str(row.get("username") or row.get("character_name") or "участника")


def boss_event_line(event: dict, participants: list[dict]) -> str:
    target = _target(event, participants)
    kind = event.get("type")
    lines = {
        "paralysis": f"⚡ Босс парализовал {target}: следующий ход будет пропущен.",
        "paralysis_skip": f"⏭ {target} пропускает ход из-за паралича. Эффект снят.",
        "critical_strike": "💥 Критический удар: босс атакует награду дважды.",
        "banishment": f"🚪 Босс изгнал {target} до конца боя.",
        "banishment_no_target": "🚪 Изгнание: подходящей цели нет — последний участник остаётся.",
        "shapeshifter": f"🔄 Босс сменил фракцию: {faction_label(event.get('faction'))}.",
        "shapeshifter_unchanged": "🔄 Перевёртыш не сработал: фракция босса осталась прежней.",
        "rapier": "🗡 Рапира: босс ударил по награде, обойдя щиты.",
        "hydra_regeneration": f"💚 Гидра восстановила {int(event.get('healed_hp', 0))} HP.",
        "kamikaze_destroyed_reward": "💥 Взрыв босса уничтожил награду: бой проигран.",
        "kamikaze_absorbed": "🛡 Щиты поглотили взрыв босса: победа сохранена.",
        "magic_shield_activated": "🔮 Босс активировал магический щит.",
        "magic_shield_removed": "🔮 Магический герой снял щит. Эта атака не наносит HP-урона.",
        "magic_shield_blocked": "🔮 Магический щит заблокировал HP-урон.",
    }
    return lines.get(kind, str(event.get("message") or ""))


def reward_event_lines(event: dict) -> list[str]:
    # The outer event summarizes the last attack; attacks preserve both hits.
    attacks = event.get("attacks") or [event]
    lines = []
    for attack in attacks:
        kind = attack.get("type")
        if kind == "shield":
            lines.append(f"🛡 Босс разбил щит награды. Осталось: {attack['shields']}.")
        elif kind == "reward_damage":
            lines.append(f"💎 Состояние награды: {attack['reward_percent']}%.")
        elif kind == "boss_skip":
            lines.append("🎵 Босс пропустил весь ход.")
        for passive in attack.get("passive_events", []):
            who = passive.get("username") or passive.get("character_name") or passive.get("hero_name") or "Игрок"
            if passive.get("message"):
                lines.append(f"{who}: {passive['message']}")
    return lines


def combat_event_lines(result: dict) -> list[str]:
    participants = result.get("state", {}).get("participants", [])
    lines = []
    timeout = result.get("timeout_result")
    if timeout and timeout.get("changed"):
        lines.extend(timeout_event_lines(timeout))
    lines.extend(filter(None, (boss_event_line(e, participants) for e in result.get("boss_events", []))))
    rewards = result.get("reward_events") or ([result["reward_event"]] if result.get("reward_event") else [])
    for reward in rewards:
        lines.extend(filter(None, (boss_event_line(e, participants) for e in reward.get("boss_events", []))))
        lines.extend(reward_event_lines(reward))
    lines.extend(filter(None, (boss_event_line(e, participants) for e in result.get("forced_skip_events", []))))
    return lines


def timeout_event_lines(result: dict) -> list[str]:
    labels = [str(row.get("username") or row.get("character_name") or "Игрок") for row in result.get("skipped", [])]
    lines = ["⏭ По таймеру пропущен ход: " + ", ".join(labels)] if labels else []
    lines.extend(combat_event_lines({key: value for key, value in result.items() if key != "timeout_result"}))
    return lines

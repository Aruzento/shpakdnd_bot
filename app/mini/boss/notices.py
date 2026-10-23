from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.presentation import format_player_mention
"""Combat notices shared by hit callbacks, home recovery and the watcher."""
from app.mini.presentation import faction_label



@editorial_screen
def _target(event: dict, participants: list[dict]) -> str:
    player_id = event.get("player_id")
    row = next((p for p in participants if p.get("player_id") == player_id), {})
    return format_player_mention(row) if row else editorial_text('boss.target.line_01')


@editorial_screen
def boss_event_line(event: dict, participants: list[dict]) -> str:
    target = _target(event, participants)
    kind = event.get("type")
    lines = {
        "paralysis": editorial_text('boss.boss_event_line.line_01', target=f'{target}'),
        "paralysis_skip": editorial_text('boss.boss_event_line.line_02', target=f'{target}'),
        "critical_strike": editorial_text('boss.boss_event_line.line_03'),
        "banishment": editorial_text('boss.boss_event_line.line_04', target=f'{target}'),
        "banishment_no_target": editorial_text('boss.boss_event_line.line_05'),
        "shapeshifter": editorial_text('boss.boss_event_line.line_06', value_1=f"{faction_label(event.get('faction'))}"),
        "shapeshifter_unchanged": editorial_text('boss.boss_event_line.line_07'),
        "rapier": editorial_text('boss.boss_event_line.line_08'),
        "hydra_regeneration": editorial_text('boss.boss_event_line.line_09', value_1=f"{int(event.get('healed_hp', 0))}"),
        "kamikaze_destroyed_reward": editorial_text('boss.boss_event_line.line_10'),
        "kamikaze_absorbed": editorial_text('boss.boss_event_line.line_11'),
        "magic_shield_activated": editorial_text('boss.boss_event_line.line_12'),
        "magic_shield_removed": editorial_text('boss.boss_event_line.line_13'),
        "magic_shield_blocked": editorial_text('boss.boss_event_line.line_14'),
    }
    message=lines.get(kind,str(event.get('message') or ''))
    if kind=='arise' and event.get('player_id') is not None:
        message=f"{target} — {message}"
    return message


@editorial_screen
def reward_event_lines(event: dict) -> list[str]:
    # The outer event summarizes the last attack; attacks preserve both hits.
    attacks = event.get("attacks") or [event]
    lines = []
    for attack in attacks:
        kind = attack.get("type")
        if kind == "reward_guard":
            lines.append(str(attack["guard_event"]["message"]))
        elif kind == "shield":
            lines.append(editorial_text('boss.reward_event_lines.line_01', shields=f"{attack['shields']}"))
            if attack.get("poisonous"):
                lines.append(editorial_text('boss.reward_event_lines.line_02', effective_reward_percent=f"{attack['effective_reward_percent']}"))
        elif kind == "reward_damage":
            lines.append(editorial_text('boss.reward_event_lines.line_03', value_1=f"{attack.get('display_reward_percent', attack['reward_percent'])}"))
        elif kind == "boss_skip":
            lines.append(editorial_text('boss.reward_event_lines.line_04'))
        for passive in attack.get("passive_events", []):
            who = format_player_mention(passive)
            if passive.get("message"):
                lines.append(f"{who}: {passive['message']}")
    return lines


@editorial_screen
def combat_event_lines(result: dict) -> list[str]:
    participants = result.get("state", {}).get("participants", [])
    lines = []
    timeout = result.get("timeout_result")
    if timeout and timeout.get("changed"):
        lines.extend(timeout_event_lines(timeout))
    lines.extend(filter(None, (boss_event_line(e, participants) for e in result.get("hero_events", []))))
    # An automatic victory recovered before a manual hit is also included in
    # timeout_result. Render its reward passives once, like its other events.
    if not (timeout and timeout.get("changed") and result.get("rewards") == timeout.get("rewards")):
        lines.extend(filter(None, (boss_event_line(e, participants)
                                  for e in (result.get("rewards") or {}).get("passive_events", []))))
    lines.extend(filter(None, (boss_event_line(e, participants) for e in result.get("boss_events", []))))
    rewards = result.get("reward_events") or ([result["reward_event"]] if result.get("reward_event") else [])
    for reward in rewards:
        lines.extend(filter(None, (boss_event_line(e, participants) for e in reward.get("boss_events", []))))
        lines.extend(reward_event_lines(reward))
    lines.extend(filter(None, (boss_event_line(e, participants) for e in result.get("forced_skip_events", []))))
    return lines


@editorial_screen
def timeout_event_lines(result: dict) -> list[str]:
    labels = [format_player_mention(row) for row in result.get("skipped", [])]
    lines = [editorial_text('boss.timeout_event_lines.line_01') + ", ".join(labels)] if labels else []
    lines.extend(combat_event_lines({key: value for key, value in result.items() if key != "timeout_result"}))
    return lines

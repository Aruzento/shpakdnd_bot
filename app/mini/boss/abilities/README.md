# Пассивки D&D Mini

Папка полностью относится только к бою с боссами.
`heroes.json` по-прежнему хранит `passive_key` и текст героя, а эта папка объясняет боевому движку, что именно делает этот `passive_key`.

## Как добавить новую пассивку без Python

Если новая способность укладывается в уже поддерживаемый тип эффекта:

1. В `app/mini/content/heroes.json` у героя укажи, например:

```json
"passive_key": "my_new_passive",
"passive_text": "Каждый второй удар наносит двойной урон."
```

2. В `abilities/abilities.json` добавь:

```json
"my_new_passive": {
  "name": "Моя пассивка",
  "description": "Каждый второй удар наносит двойной урон.",
  "effects": [
    {
      "trigger": "attack",
      "type": "every_n_damage_multiplier",
      "every": 2,
      "multiplier_percent": 200,
      "message": "✨ Моя пассивка: двойной урон"
    }
  ]
}
```

После рестарта бот подхватит её. Для существующих generic-типов Python менять не надо.

## Поддерживаемые effect type

- `chance_damage_multiplier` — шанс умножить урон.
- `every_n_damage_multiplier` — усиление каждого N-го удара.
- `first_hit_damage_multiplier` — усиление первого удара в бою.
- `after_first_damage_multiplier` — усиление всех ударов после первого.
- `boss_hp_below_damage_multiplier` — усиление, когда HP босса ниже порога.
- `kill_chance_shards` — шанс получить общие осколки за смертельный удар.

Если понадобится совершенно новая механика, добавляется новый `type` в `engine.py`. Остальной бой при этом менять не нужно.

## Важное про текущие тексты heroes.json

В трёх пассивках исходный текст не задаёт точный шанс/силу:
- `lucky_strike` — «иногда» и «усиленный удар»;
- `precise_strike` — «с небольшим шансом»;
- `double_strike` — «иногда».

Поэтому сейчас параметры явно вынесены в `abilities.json`:
- lucky_strike: 20% шанс, +50% урона;
- precise_strike: 20% шанс, ×2;
- double_strike: 20% шанс, ×2.

Их можно балансировать одной правкой JSON.

## Проверка после добавления героя

Запусти:

```bash
python -m app.mini.boss.abilities.validate
```

Если в `heroes.json` появился `passive_key`, которого нет в `abilities.json`, команда покажет его и завершится с ошибкой.

# D&D Mini — шаг 7: звёзды, улучшения и продажа осколков

## Что добавлено

У каждого полученного героя теперь есть `stars`.

Атака считается от базовой атаки героя из `heroes.json`:

- 0 звёзд: базовая атака;
- каждая новая звезда увеличивает ТЕКУЩУЮ атаку на 50%;
- результат каждого шага округляется вниз.

Пример для Деревенского жителя с базовой атакой 3:

- 0⭐ = 3
- 1⭐ = 4
- 2⭐ = 6
- 3⭐ = 9
- 4⭐ = 13

## Лимиты

- common — максимум 4⭐
- uncommon — максимум 5⭐
- rare — максимум 6⭐
- legendary — без лимита

## Цена улучшения

Цена следующей звезды:

`номер новой звезды × 10 × множитель редкости`

Множители:

- common = 1
- uncommon = 2
- rare = 4
- legendary = 10

Например common: 10 → 20 → 30 → 40 осколков.

## Дубликаты

В `app/mini/content/heroes.json`:

- common = 10 осколков
- uncommon = 20
- rare = 40
- legendary = 100

## Продажа осколков

В карточке героя появляется кнопка `💱 Продать осколки`.

Варианты:

- продать 1;
- продать 10;
- продать все.

Курс: `1 осколок = 1 Mini-монета`.
Начисление записывается в историю кошелька.

## Где редактировать механику

`app/mini/content/heroes.json` → `settings.upgrade`:

```json
"upgrade": {
  "attack_growth_percent": 50,
  "shard_sell_price": 1,
  "star_cost_base": 10,
  "max_stars": {
    "common": 4,
    "uncommon": 5,
    "rare": 6,
    "legendary": null
  },
  "cost_multiplier": {
    "common": 1,
    "uncommon": 2,
    "rare": 4,
    "legendary": 10
  }
}
```

`null` у legendary означает отсутствие лимита.

## База

В `mini_player_heroes` автоматически добавляется колонка:

`stars INTEGER NOT NULL DEFAULT 0`

Существующие герои сохраняются и получают 0 звёзд.

## Установка

Архив полный. Распаковать поверх `/opt/shpakdnd-bot`, не заменяя `.env` и `shpakdnd.db`.

```bash
cd /opt/shpakdnd-bot
cp shpakdnd.db shpakdnd.db.backup
chown -R shpakbot:shpakbot /opt/shpakdnd-bot
./.venv/bin/pip install -r requirements.txt
sudo -u shpakbot ./.venv/bin/python check_bot.py
sudo -u shpakbot ./.venv/bin/python -m compileall -q bot.py app
systemctl restart shpakdnd-bot
systemctl status shpakdnd-bot --no-pager
```

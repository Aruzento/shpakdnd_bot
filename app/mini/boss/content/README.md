# Boss content

Все данные боссов лежат только в этой папке.

- `bosses.json` — каталог боссов.
- `images/` — изображения боссов.
- Код босса (`code`) должен быть уникальным и стабильным.
- Поле `image` содержит только имя файла, например `training_golem.png`.
- Если картинки нет, бот просто отправит текстовую карточку.

Минимальные поля босса:

```json
{
  "code": "training_golem",
  "name": "Каменный голем",
  "description": "Описание",
  "max_hp": 300,
  "min_players": 2,
  "reward_coins": 60,
  "image": "training_golem.png",
  "active": true
}
```

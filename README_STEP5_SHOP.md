# D&D Mini — шаг 5: магазин + структура контента гачи

## Магазин

Кнопка `🛒 Магазин` теперь работает целиком внутри личного ephemeral-меню.

Каталог хранится отдельно:

`app/mini/content/shop.json`

Его можно редактировать без изменения Python-кода. Каталог перечитывается при
каждом открытии магазина. Если JSON сохранён корректно, новые цены, описания,
категории и товары появляются без перезапуска бота.

Поддерживаются:

- категории;
- цена;
- включение/выключение товара;
- общий stock;
- max_per_player;
- обычные предметы (`inventory`);
- сертификаты (`certificate`).

Покупка атомарная: проверка лимита, остатка, баланса, списание денег, запись
покупки и выдача предмета происходят одной SQLite-транзакцией.

## Мои товары

В магазине есть кнопка `🎒 Мои товары`.

Там отображаются:

- предметы из Mini-инвентаря;
- непогашенные сертификаты из магазина.

Погашение сертификатов мастером сделаем отдельной механикой позже.

## Гача / герои

Структура для следующего шага уже подготовлена:

`app/mini/content/heroes.json`

Картинки:

`app/mini/content/hero_images/`

У героя есть имя, редкость, раса, класс, атака, пассивка, описание и имя файла
картинки. Сейчас в примерах стоит `_placeholder.png`.

Пример:

```json
{
  "code": "torvin_ironbeard",
  "name": "Торвин Железнобород",
  "rarity": "rare",
  "race": "Дворф",
  "class_name": "Варвар",
  "attack": 8,
  "passive_key": "stubborn",
  "passive_text": "Описание способности",
  "description": "Описание героя",
  "image": "torvin.webp",
  "active": true
}
```

Файл `torvin.webp` нужно положить в `hero_images/`.

Саму крутку подключим на шаге 6. Билет призыва уже описан в магазине, но пока
`active: false`, чтобы игроки не тратили монеты на ещё неработающую механику.

## Установка

Архив полный. Распакуй поверх `/opt/shpakdnd-bot`, не заменяя `.env` и
`shpakdnd.db`.

```bash
cd /opt/shpakdnd-bot
chown -R shpakbot:shpakbot /opt/shpakdnd-bot
./.venv/bin/pip install -r requirements.txt
sudo -u shpakbot ./.venv/bin/python check_bot.py
sudo -u shpakbot ./.venv/bin/python -m compileall -q bot.py app
systemctl restart shpakdnd-bot
systemctl status shpakdnd-bot --no-pager
```

Старый закреп менять не нужно.

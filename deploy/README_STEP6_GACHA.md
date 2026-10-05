# D&D Mini — шаг 6: гача, коллекция и активный герой

Основа: текущий GitHub `main`, commit `88e9246884820b3174151fc374d142a2b9155643` (`shop`).

## Что работает

В личном меню кнопка `🎴 Коллекция` теперь открывает коллекцию игрока.

Из неё можно:

- открыть `✨ Призыв героев`;
- крутить за Mini-монеты;
- крутить за `🎟 Билет призыва`;
- видеть полученных героев;
- открывать карточку героя с картинкой и описанием;
- выбирать активного героя.

Первый выпавший герой автоматически становится активным.

Активный герой определяет расу, класс, атаку и пассивку Mini-персонажа. Это уже
видно в `👤 Персонаж` и на главном экране.

## Дубликаты

Повторный герой не пропадает:

- `copies` увеличивается на 1;
- выдаются осколки согласно `heroes.json -> settings -> duplicate_shards`.

Осколки пока копятся. Их применение можно подключить отдельным этапом.

## Контент

Герои:

`app/mini/content/heroes.json`

Картинки:

`app/mini/content/hero_images/`

Магазин:

`app/mini/content/shop.json`

Билет призыва в магазине теперь включён (`active: true`).

## Изображения

Для каждого героя поле `image` должно содержать имя файла, например:

`"image": "torvin.webp"`

а сам файл:

`app/mini/content/hero_images/torvin.webp`

Если картинка отсутствует или Telegram не смог её отправить, бот показывает
текстовую карточку вместо падения.

## Установка

Архив содержит полного бота. Распаковать поверх `/opt/shpakdnd-bot`, сохранив
существующие `.env` и `shpakdnd.db`.

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

Старый закреп `/minipanel` пересоздавать не нужно.

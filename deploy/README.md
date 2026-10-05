# shpakdnd_bot

Готовая сборка Telegram-бота для D&D-чата.

## Что уже работает

Обычные функции бота:

- `/timer`, `/stop`
- `/roll`
- `/add`, `/del`, `/inv`, `/clean`
- `/char`, `/charset`, `/lvlup`
- `/create`
- глобальные `/admadd`, `/admdel`, `/admclean`, `/admcharset`

D&D Mini:

- отдельный Mini-мир в теме `2684` чата `-1003376315265`;
- администратор Mini-темы — `@arukozento`;
- публичный закреп-launcher через `/minipanel`;
- кнопка `🎮 Открыть D&D Mini`;
- персональное ephemeral-меню для каждого игрока;
- Mini-персонаж;
- отдельный кошелёк и история операций;
- заготовленные таблицы под дейлики, магазин, гачу и боссов;
- пропуск хода босса заложен на 4 часа;
- Mini полностью отделён от обычных персонажей и инвентаря.

## Главное исправление этой сборки

Mini-тему больше не нужно отдельно регистрировать командой на сервере.

В `app/topics.py` у темы стоит:

```python
"mini": True,
"mini_name": "D&D Mini",
```

При каждом старте бот сам создаёт/включает соответствующий `mini_worlds`.

Callback-кнопки персонального меню теперь содержат `world_id`, поэтому после
перехода в ephemeral-сообщение бот не зависит от того, передал ли Telegram
`message_thread_id` в callback.

## Установка / обновление на сервере

Перед заменой файлов сделай резервную копию базы:

```bash
cd /opt/shpakdnd-bot
cp shpakdnd.db shpakdnd.db.backup
```

Распакуй содержимое архива в `/opt/shpakdnd-bot`, но НЕ удаляй:

- `.env`
- `shpakdnd.db`

Установи зависимости:

```bash
cd /opt/shpakdnd-bot
./.venv/bin/pip install -r requirements.txt
```

Проверь сборку без запуска polling:

```bash
sudo -u shpakbot ./.venv/bin/python check_bot.py
```

Должна появиться строка примерно:

```text
OK: D&D Mini: chat=-1003376315265 topic=2684 name=D&D Mini
```

Потом:

```bash
sudo -u shpakbot ./.venv/bin/python -m compileall -q bot.py app
systemctl restart shpakdnd-bot
systemctl status shpakdnd-bot --no-pager
```

Если сервис не запустился:

```bash
journalctl -u shpakdnd-bot -n 100 --no-pager
```

## Создание закрепа Mini

После успешного запуска зайди в тему `2684` и один раз отправь:

```text
/minipanel
```

Бот создаст общую кнопку:

```text
🎲 D&D Mini
[ 🎮 Открыть D&D Mini ]
```

Если у бота есть право закреплять сообщения, он закрепит её сам.

После нажатия на кнопку каждый игрок получает своё ephemeral-меню. Другие
участники его не видят.

## Создание Mini-персонажа

Нажать `🎮 Открыть D&D Mini` → `👤 Создать персонажа`.

Имя пока вводится ephemeral-командой:

```text
/minicreate "Дед Максим"
```

Она зарегистрирована как приватная команда Telegram.

## Важно

В архиве нет `.env` и нет рабочей базы данных. Это специально: токен и
существующие данные должны оставаться на сервере.


## D&D Mini — шаг 6

Добавлены гача героев, коллекция, картинки карточек, дубликаты/осколки и выбор активного героя. См. `README_STEP6_GACHA.md`.

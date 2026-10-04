# D&D Mini — приватные меню

Теперь `/mini` и `/minicreate` используют Ephemeral Messages Telegram Bot API.

Что это даёт:

- `/mini` объявлен как ephemeral-команда;
- команда пользователя может быть невидима остальным участникам группы;
- ответ бота и inline-меню видит только тот игрок, для которого оно создано;
- у каждого игрока своё независимое меню;
- нажатия по кнопкам редактируют именно его ephemeral-меню;
- чужие кнопки по-прежнему защищены user ID.

## Требование

Нужен aiogram 3.31.0+ — в этой версии есть поддержка Bot API 10.3
и `EphemeralMessageParameters`.

После загрузки обязательно:

```bash
cd /opt/shpakdnd-bot
./.venv/bin/pip install -r requirements.txt
```

## Что заменить на сервере

- `bot.py`
- `requirements.txt`
- всю `app/mini/`
- `app/handlers/__init__.py` из архива оставлен кумулятивно

Базу `shpakdnd.db` не заменять.

После обновления:

```bash
sudo -u shpakbot ./.venv/bin/python -m compileall -q bot.py app
systemctl restart shpakdnd-bot
systemctl status shpakdnd-bot --no-pager
```

После рестарта бот сам зарегистрирует `/mini` и `/minicreate`
как ephemeral-команды для групповых чатов.

Важно: Telegram предупреждает, что ephemeral-сообщение не гарантированно
доставляется офлайн-пользователю. Для меню это нормально — игрок просто
снова вызывает `/mini`, когда находится в чате.

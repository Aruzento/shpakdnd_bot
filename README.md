# shpakdnd_bot

Telegram-бот для D&D на `aiogram`.

Эта версия — тот же бот из текущего `main`, но монолитный `bot.py` разнесён по модулям без намеренного изменения пользовательской логики.

## Структура

```text
bot.py                       # только запуск приложения
app/
├── config.py                # .env, БД, часовой пояс, кубики
├── topics.py                # чаты, темы, админы, персонажи
├── context.py               # работа с chat_id/thread_id и правами
├── db/
│   ├── schema.py            # создание/миграция SQLite
│   ├── inventory.py         # запросы инвентаря
│   └── timers.py            # сохранение таймеров
├── services/
│   ├── inventory.py         # парсинг и вывод предметов
│   └── timers.py            # логика обратного отсчёта
└── handlers/
    ├── common.py            # /start, /chatid, /roll
    ├── inventory.py         # /add, /del, /inv, /clean
    └── timers.py            # /timer, /stop

tests/
└── test_inventory_parser.py

deploy/                      # примеры systemd для VDS
```

## Данные

Рабочие данные по-прежнему лежат в корне проекта в `timers.db` и не коммитятся в Git.

Таблица `inventory` хранит:

```text
chat_id
thread_id
username
name
quantity
description
```

При обнаружении старой структуры таблицы миграция выполняется автоматически при запуске.

## Настройка

Скопировать `.env.example` в `.env` и заполнить токен:

```env
BOT_TOKEN=...
BOT_TIMEZONE=Europe/Moscow
```

Персонажи и администраторы тем настраиваются в:

```text
app/topics.py
```

## Запуск

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Проверка

Проверка синтаксиса всего проекта:

```bash
python -m compileall -q bot.py app tests
```

Тест парсера инвентаря:

```bash
python -m unittest
```

## VDS

Точка входа по-прежнему `bot.py`, поэтому существующий `systemd`-сервис с:

```text
ExecStart=/opt/shpakdnd-bot/.venv/bin/python /opt/shpakdnd-bot/bot.py
```

может остаться прежним.

Важно: старый watcher, который следил только за `/opt/shpakdnd-bot/bot.py`, теперь недостаточен. После разнесения кода изменения чаще будут происходить внутри `app/`. В папке `deploy/` лежит обновлённый вариант watcher-а.

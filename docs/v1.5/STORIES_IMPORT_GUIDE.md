# Подготовка и проверка авторских Историй

STORY_AUTHOR_TEMPLATE.json — **TEMPLATE / NOT PRODUCTION**, только пример схемы.
Он находится в docs, не копируется автоматически, не участвует в discover() или
strict-content и не считается настоящим сюжетом. Шаблон не содержит игровой экономики.

1. Автор присылает уникальный code, title, description, start_node и все тексты/выборы/
   окончания nodes по STORIES_AUTHOR_GUIDE.md. Заменить демонстрационный сценарий целиком.
2. Создать отдельный **внешний тестовый каталог**, вне любого Git/watcher. Не использовать
   текущий рабочий STORIES_DIR. Положить туда только предоставленные автором JSON.
3. Выполнить read-only проверку: `python tools/validate_story_content.py --directory <внешний-тестовый-каталог>`.
   Exit 0 — валидные файлы; 2 — пустая поставка; 1 — схема/ссылки/путь/дубликаты невалидны.
   Это валидация данных, а не разрешение публиковать сценарии на сервер.
4. В изолированной среде указать STORIES_DIR на тот же тестовый каталог и временную
   SQLite через scripts/run_isolated_checks.py. Проверить list/open/join/start/vote,
   kick/cancel, восстановление, неизменность frozen JSON после изменения исходника.
5. После реального полного контента повторить все тесты и strict-content, затем отдельные
   независимое ревью, production DB copy и staging. Сейчас серверы/рабочие директории не трогать.

Schema_version=1: DAG без циклов. Schema_version=2: обязательный visit_policy="once",
циклы разрешены, при игре исключены повторы и переходы в тупики. До 1000 достижимых
узлов, у каждого есть путь к концовке; только известные поля.
До 20 выборов, уникальные id, существующие next. Тексты: title 150, description 1000,
узел 3500, выбор 120 UTF-16 units. Файлы локальные, не symlink, не более 2 MB.
Текст отправляется с parse_mode=None. Кнопки содержат server session/step/index.
Команды: /story list, /story CODE, /story start, /story status, /story kick USER_ID,
/story cancel. Не нужны авторские формулы наград: Истории не меняют деньги/инвентарь.

## Настоящий неопубликованный пакет — 26.10.2026

`content/stories/unpublished/tayna_propavshey_ovtsy/tayna_propavshey_ovtsy.json` —
авторский schema_version 2, 46 сцен и 7 концовок, 113 выборов. Соседние AUTHOR_SOURCE.md,
GRAPH_AUDIT.* и MANIFEST.json позволяют сверить оригинал/контрольные суммы. Не копировать
MANIFEST/audit/prompt JSON в рабочий STORIES_DIR: туда поступает только файл сценария.
Этот каталог Git намеренно никогда не подхватывается discover(). Тесты копируют payload
в отдельный временный внешний каталог и проверяют настоящий JSON через общий loader.

Проверка без серверов и настоящей SQLite:

```
python scripts/run_isolated_checks.py --label story-candidate -- tools/validate_story_candidate.py
python scripts/run_isolated_checks.py --label story-once -- -m unittest tests.v1_5.test_story_once -q
```

Первый инструмент создаёт внешний временный каталог, проверяет MANIFEST, 10 000
воспроизводимых прогонов с seed=20261026, все 7 концовок, read-only Story CLI и
strict-content с этим настоящим JSON. Итог и exit codes — в story-candidate.json
в sandbox. Ожидается PASS0. При пустом внешнем STORIES_DIR прежний strict-content
честно остаётся BLOCKED_BY_INPUT/exit2. Ни один из результатов не разрешает публикацию.

Для отдельной ручной проверки скопировать только payload в новый внешний каталог и
выполнить `python tools/validate_story_content.py --directory <external-test-directory>`.
Не указывать рабочий серверный каталог. После отдельного разрешения нужны независимое
ревью, offline production DB copy и настоящий Telegram staging с двумя игроками,
семью концовками, повторными/скрытыми кнопками, kick/cancel и рестартом. См. также
STORY_ONCE_VERIFICATION.md и RELEASE_AND_ROLLBACK.md. В разработке / Релиз заблокирован.

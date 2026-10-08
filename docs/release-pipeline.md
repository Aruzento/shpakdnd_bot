# V1.4.1: release checks (этап A)

Stable baseline: `f06c0d129fdad0fef4fde0889915faac26b94f4a` (V1.4).
Workflow: `.github/workflows/release-checks.yml`, job **Linux release checks**.
Он запускается при push в `codex/**` и при pull request в `main` на
`ubuntu-latest` / Python 3.13. Checkout и setup-python закреплены полными SHA
официальных релизов v7.0.1 и v7.0.0; permissions только `contents: read`.

## Порядок работы

1. Продолжать существующую `codex/v1.4.1`, сохраняя stage A.
   Не пересоздавать её от main и не использовать shared/live checkout для проверок.
2. Установить зависимости из `requirements.txt`. На disposable checkout:

   ```bash
   export BOT_TOKEN=ci-test-token
   python -m pip install -r requirements.txt
   python scripts/release_checks.py --report /tmp/release-checks.json
   ```

3. Runner проверяет committed `HEAD`: guard, `git diff --check <BASE> HEAD`,
   `bash -n` всех tracked `deploy/*.sh`. Затем `git archive HEAD` извлекается
   в новый TemporaryDirectory. Tracked `.env` и SQLite файлы запрещены.
   До `python check_bot.py` проверяется, что `app.config.BASE_DIR` совпадает
   с временным checkout, `DB_PATH` — его новый `shpakdnd.db`, legacy DB —
   его новый `timers.db`; ни один файл ещё не существует. Проверяется импорт
   `check_bot.DB_PATH == app.config.DB_PATH`. Production config не меняется.
4. В этой копии выполняются `python -m compileall -q bot.py app`,
   `python check_bot.py`, три `python -m ...validate` (hero abilities,
   boss abilities, tower), JSON parse всех JSON и штатные валидаторы
   shop/heroes/combat safety/village. `check_bot` дополнительно валидирует
   boss/items/equipment/tower balances, SQLite integrity и FK.
5. Перед запуском полных tests отдельно сравнивается фактический unittest
   discovery V1.4 и HEAD: IDs и кратность запусков каждого baseline test.
   Исчезновение обнаруженного test требует того же точного approved-removal.
   Итоговое число выполненных tests обязано совпасть с HEAD discovery.
   Полный `python -m unittest discover -s tests -q` запускается без урезания.
   Во время unittest реальные `app.topics.TOPIC_SETTINGS` обнулены только
   в отдельном процессе: каждый зависимый тест обязан использовать
   `tests/topic_fixtures.py::isolated_topics()` и временную SQLite.
   Внешние TCP-соединения запрещены в check_bot/validators/tests через
   временный `sitecustomize`; loopback разрешён для Windows asyncio.
   Telegram polling не запускается. Fixture deploy-тестов исполняет только
   поддельные git/systemctl/Telegram commands на TemporaryDirectory.
6. Любой ненулевой exit делает job красным. Отсутствующий test count,
   нулевой набор, expected failures/unexpected successes, любой skip на Linux
   также делают job красным. На Windows единственный известный baseline skip —
   `ShellPathTests.test_real_posix_executable_symlink_keeps_venv_entry_point`
   (POSIX symlink semantics); Windows результат не заменяет Linux.
7. Job Summary всегда показывает stable baseline и checkout HEAD, результаты
   каждого валидатора/guard, фактическое число tests/failures/errors/skips.
   `NOT RUN` и `RUNNING` отличны от `OK`; после раннего сбоя оставшиеся
   проверки не объявляются успешными. Нет `continue-on-error`, `|| true`
   и автоматического повторения проваленных тестов.
8. Push ветки запускает независимую Linux проверку. Требуется зелёный run
   именно на final HEAD; после любого нового commit нужен новый run.
   Открыть PR и провести review. В settings `main` обязателен статус
   **Linux release checks**, strict/up-to-date branch, минимум одно одобрение,
   dismiss stale approvals и защита администраторов. Проверить эти настройки
   отдельно: workflow не может менять settings с `contents: read`.

Этап A не выполняет merge, создание tag/GitHub Release или deployment.
Наличие зелёного локального unittest не означает «готово к main».
Этап B/production требуют выполнения всех exit criteria и отдельного review.

## Release guard и reviewable удаления

`scripts/release_guard.py` использует постоянный `BASELINE_SHA`, полный stable
release SHA, `git ls-tree` и `git show`. Он проверяет ancestor baseline,
сохранение `bot.py`, каждого Python файла `app/` и `tests/`, JSON файлов
(включая каталоги и test data), точных qualified `test_*` функций/методов
через AST. Новый тест не компенсирует исчезновение старого. В
`app/handlers/__init__.py` сохраняются все прежние импорты и каждый элемент
явного списка `ROUTERS`, включая `mini_events_router`. Динамическое построение
ROUTERS требует изменения и review guard, а не молчаливого обхода.

Allowlist `docs/release/approved-removals.json` обязателен, сейчас пуст:

```json
{"removals": []}
```

Для намеренного удаления отдельная запись указывает `kind`, точный `path`,
`symbol` (для test/import/router) и осмысленный `reason` с объяснением,
ссылкой на review/замену покрытия. Пример формата, не действующее разрешение:

```json
{
  "removals": [{
    "kind": "test",
    "path": "tests/example.py",
    "symbol": "ExampleTests.test_old_case",
    "reason": "Coverage moved to reviewed replacement test in PR 42."
  }]
}
```

`kind=file` разрешает только удаление данного файла вместе с его символами.
Другие точные kinds: `test`, `import`, `router`. Для импорта symbol имеет вид
`from app.mini.events.handlers import router as mini_events_router`; для
router — `mini_events_router`. Переименование тоже является удалением
baseline path/symbol и требует review.

Отсутствующий baseline/allowlist, malformed JSON, неизвестные поля,
дубликаты, пустые/короткие причины, wildcard, traversal, stale и
несуществующие записи отклоняются. Произвольного массового выключателя нет.
Ревьюер должен проверить **каждую** запись: наличие причины не доказывает,
что удаление безопасно. AST guard защищает наличие символов; он не доказывает
эквивалентность тестовых assertions или отсутствие злоумышленного изменения
самого workflow. Это обязанность review, защищённого `main` и Linux suite.

Baseline обновляется вручную **только после завершения стабильного релиза
и подтверждения ревьюера** отдельным reviewable изменением guard и этой
документации. `HEAD~1` и автоматическое смещение baseline запрещены.
`--baseline <full SHA>` существует для synthetic Git fixture тестов;
CI не передаёт override и всегда использует stable константу.
Когда baseline обновлён, утратившие актуальность allowlist записи удаляются
тем же reviewed изменением. До этого удаление из раннего dev commit
продолжает проверяться в каждом следующем commit.

## Проверка самой защиты

```bash
python -m unittest tests.v1_4_1.test_release_guard -q
```

Тесты создают независимые Git-репозитории с synthetic V1.4 baseline,
делают настоящие commits, вызывают CLI отдельным процессом и проверяют exit
code/диагностику. Покрываются Events service/test file/test method/router/import,
JSON и entry point, удаление в предыдущем dev commit, корректное точное
разрешение, malformed/missing allowlist, wildcards, причины/дубликаты/stale,
невалидный AST и отсутствующий baseline. Никаких live БД/Telegram вызовов.

## Этап B: production preflight и infrastructure guard

Работа продолжается в существующей `codex/v1.4.1` и draft PR #1; stage A
`49cd326dc7283f0d29b26a5f9307f4dfa6dec3c1` не сбрасывается. Baseline game checks,
Events/routers и все старые test IDs остаются обязательными.

Guard дополнительно сравнивает infrastructure с **отдельным неизменяемым stage A
SHA**, проверяя его ancestor и inventory. Защищены deploy shell/Python helpers,
service/service.example/path, release_checks/release_guard/test_inventory и
workflow. Для новых B helpers существует explicit required HEAD inventory.
Удаление этих файлов нельзя разрешить game-removal allowlist. Workflow обязан
сохранять Linux job, triggers codex push/main PR, Python 3.13, fetch-depth=0,
read-only permissions и вызов общего runner; отключающие if, continue-on-error
и `|| true` блокируются. Guard не заменяет независимый review изменения
содержимого infrastructure или обязательную branch protection в GitHub.

`deploy/release_preflight.py` использует **тот же** TARGET release runner, а не
второй набор validators/tests. До release checks проверяется отдельный snapshot
реальной DB: Online Backup API через существующий deploy_helpers включает WAL;
штатный check_bot применяет миграции дважды, generic row/schema inventory
доказывает сохранность всех существующих таблиц/данных. Служебные изменения
ограничены явно перечисленными presentation/config fields SERVICE_METADATA;
экономические значения, ownership и state JSON сравниваются строго. Повторная
миграция и OLD compatibility сравнивают также service metadata строго.

Temporary TARGET/OLD clones, отдельный target venv, migration snapshot,
rollback copy и обычная тестовая DB физически отделены от production. Перед
writer сверяются Git SHA и actual app.config DB_PATH; symlinks/hardlink aliases
запрещены. Настоящий token/env не копируются. Root evidence содержит hashes,
SHA, counts, phases, schema и rollback true/false/unknown; неполный report FAIL.
Обычный deploy всегда запускает новый preflight и валидирует evidence перед
maintenance/checkout. Live rows могут изменяться во время работающего бота;
финальный backup и короткие runtime checks остаются обязательными.

После остановки отсутствуют full unittest/compileall/catalog validators,
включая rollback. Bootstrap installed executable выполняется из отдельной
проверенной копии и атомарно обновляет полный versioned tooling bundle.
Фактические пути, порядок команд и recovery описаны в
[deployment.md](deployment.md). Этап B не обновляет watcher units, не внедряет
last-known-good stage C и не делает merge/production deployment.

Для stage B source общего release_checks.py, test_inventory.py и workflow
обязана точно совпадать с reviewed stage A. No-op executor или изменение
семантики discovery/CI требует отдельного явного изменения guard и review;
одного сохранения имени файла недостаточно.

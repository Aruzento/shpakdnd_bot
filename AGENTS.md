# Codex: постоянный регламент репозитория

## «Протестируй и выложи на TEST»

Использовать только `.github/workflows/staging-deploy.yml`, `workflow_dispatch`
на **main**, GitHub Environment **staging**, inputs `pr` и точный lowercase
40-символьный `sha` актуального HEAD открытого PR `Aruzento/shpakdnd_bot` в main.
Сначала прочитать `docs/operations/STAGING-AUTOMATION.md`, затем проверить HEAD и
успешный Linux CI обеих конфигураций Python 3.11/3.13 именно на этом SHA.
Красный, пропущенный, отсутствующий или незавершённый CI запрещает выкладку.
Не подменять её собственной SSH-процедурой, паролями, self-hosted runner,
`sudo deploy-shpakdnd` или кодом feature-ветки в workflow с секретами.

Workflow доступен после review/merge инфраструктуры и однократной установки
оператором на TEST. Если Actions недоступен из Codex, честно сообщить причину,
ссылку https://github.com/Aruzento/shpakdnd_bot/actions/workflows/staging-deploy.yml
и точные параметры `branch=main`, `pr=<номер>`, `sha=<40 символов>`, а также команду:

```bash
gh workflow run staging-deploy.yml --repo Aruzento/shpakdnd_bot --ref main -f pr=NUMBER -f sha=FULL_SHA
```

После dispatch дождаться run, скачать staging artifacts, прочитать `report.json`
и `report.md`, передать результаты и ручные Telegram-сценарии пользователю.
`acceptance_ready=PASS` означает готовность к ручной приёмке, а не её прохождение.
FAIL не разрешает автоматического rollback/DB restore или повторной установки
без изучения отчёта. Неизвестный результат SSH считать FAIL до проверки состояния.

## Границы

TEST — VM-338333, production — другая физическая VM. Тестовые `.env`, topics.py,
SQLite и внешний editorial никогда не переносить в production. Production
выполняется отдельно из origin/main через `sudo deploy-shpakdnd`, только после
явного разрешения. Изменения серверов, GitHub Secrets/Environment и installed
production bundle требуют отдельного разрешения пользователя.

Перед реализацией читать docs/deployment.md и действующие release gates.
Не удалять/отключать тесты и не менять игровую механику ради зелёных проверок.
Проверки: полный unittest discover, check_bot на изолированной SQLite,
compileall, все validators, миграции и rollback на копиях, git diff --check.
Сохранять пользовательские незакоммиченные изменения; инфраструктуру готовить
в отдельной ветке от актуального origin/main.

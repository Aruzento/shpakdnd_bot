# D&D Mini V1.4.1: release manager и безопасный observer

`sudo deploy-shpakdnd --preflight` выполняет общий release gate на exact TARGET
checkout и SQLite snapshot, пока bot продолжает работать. Обычная команда
всегда требует новый успешный preflight до maintenance. SOURCE/TARGET journal,
final backup, stable startup и operator confirmation защищают запуск и LKG.

Watcher только читает HEAD/status и пишет диагностический journal. Он не
перезапускает bot, не применяет код/миграции, не исправляет dirty files и не
вызывает deploy. Загрузка через WinSCP требует контролируемого релиза.
PathModified явно перечисляет app Python/JSON directories; архитектурный тест
проверяет покрытие. Control files/DB/env/venv/caches не отслеживаются.

Установленные scripts/units не обновляются от Git checkout автоматически.
После ревью и CI отдельного полного SHA оператор последовательно выполняет
`install-deploy-shpakdnd.sh <FULL_REVIEWED_SHA>` и, отдельно разрешив изменение
watcher configuration, `install-systemd-units.sh <FULL_REVIEWED_SHA>` из root-owned
чистого checkout вне production. Прямое копирование units/legacy helper не
заменяет installer, manifest и проверку фактически загруженной конфигурации.

Installer блокирует legacy update устойчивым к reboot карантином, сохраняет
forensic backups, проверяет syntax, атомарно устанавливает versioned observer,
проверяет effective units и активирует observer. Здоровый bot не останавливается.
Ошибка сохраняет карантин/отключённый watcher. Старый unsafe helper не включается.

LKG root-only вне Git, обновляется после полного health/smoke и ввода full SHA.
Нет LKG — нет rollback к произвольному OLD HEAD. Для pinned V1.4 baseline есть
строгий `--initialize-lkg` с полным baseline gate и отдельно согласованным startup.

Полная инструкция, locations/state/rollback/ошибки:
[deployment.md](../docs/deployment.md).
CI, независимый Linux стенд и пошаговый production rollout:
[release-pipeline.md](../docs/release-pipeline.md).

В рамках задачи Codex production deployment, units, LKG и main не изменяются.


Этап D: final gate перед maintenance, JSON/Markdown release report, automatic readonly smoke отдельно от реального Telegram, LKG version=2 только с semantic/operator proof. Старые LKG сохраняются при отказе/NOT_RUN. Full staging checklist: [staging-v1.4.1.md](../docs/staging-v1.4.1.md). Required CI фактически проверяется через API; недостающие checks блокируют merge. После squash main SHA обязательно повторить gate. Изолированный --staging-sha требует approved runtime/отдельный project path и не разрешает production. Полные команды и блокеры описаны в docs/deployment.md.

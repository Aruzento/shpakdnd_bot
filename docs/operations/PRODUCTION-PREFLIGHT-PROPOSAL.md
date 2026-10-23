# Проект следующего улучшения production preflight

Статус: проект, не установлен и не включён в production deployment.
Текущий sudo deploy-shpakdnd и обязательные проверки/подтверждения остаются.

Проблема: сейчас долгие compileall/unittest/validators выполняются после stop.
Предложение: зафиксировать origin/main SHA при fetch, до maintenance notice/stop
создать отдельный immutable checkout и online SQLite Backup API snapshot. Запустить
полный pipeline в offline sandbox без production token, config и DB writers;
сохранить evidence SHA/tree/requirements/config hashes и результаты migrations/
old-code startup на disposable copies. Любая ошибка завершает preflight при
продолжающем работать production.

После успешного preflight сохранить существующие согласия, notices и порядок
watcher/update/bot stop. Создать **новый quiescent verified backup**; проверить
неизменность выбранного SHA/tree/approved dependencies/config и отсутствие других
writers. Повторить обязательные startup/migration/SQLite/config/HEAD/clean checks
на актуальной базе с разрешёнными миграциями, затем существующее подтверждение
запуска, PID/NRestarts/journal и notices. Долгие тесты не удалять: evidence привязано
к immutable SHA и среде; изменение SHA/dependencies/config или неполный preflight
означает новый полный pipeline. Какие проверки допустимо не повторять после stop,
определить отдельным review; исходный обязательный набор сохраняется до решения.

Экономический rollback не разрешается одной совместимостью старого startup.
Все три frozen PvP rollback-check, pause-new/drain, reconciliation, запрет DB restore
и поведение отказов сохраняются. Никаких feature commits на production.

Критерии отдельного PR: before/after ordering tests с реальным Bash/fake systemd,
отказ до stop при красных тестах, SHA/config/dependency drift и повторные операции,
WAL snapshot/backup/FK, новые записи между snapshot и stop, signals/EOF, concurrent
installer/deployer, watcher races и три PvP gates. Лишь после review — установка
frozen bundle оператором в согласованное окно, dry-run и реальный smoke с evidence.

# План следующего TEST-кандидата с сохранением прогресса

Только после независимого ревью и отдельного разрешения владельца.
Здесь не выполняются остановка, запуск или смена TEST. Не использовать main, production,
sudo deploy-shpakdnd и второй polling с тем же токеном.

1. Зафиксировать согласованный полный SHA из отчёта codex/v1.5-test-fixes.
   Проверить результаты полного unittest, валидаторов и Linux 3.11/3.13 именно этого SHA.
   Проверить hostname VM-338333, IP 88.214.54.194, unit/PID/WorkingDirectory, HEAD,
   локальные изменения, bot identity, watcher и отсутствие второго polling.
2. Подготовить отдельный чистый checkout точного SHA и venv. Не делать checkout/reset
   поверх работающего /srv/shpakdnd-v15-staging/code.
   Сохранить отдельные TEST .env/topics/CSV и внешний STORIES_DIR.
   Утверждённый SHA-256 tayna_propavshey_ovtsy.json:
   f185595d8c89fc1644e331a4ff1ae34c6df61920b4a69c9948654cd60903cce2.
3. Сделать SQLite online backup текущего V1.5 TEST через backup API, не обычный cp при WAL.
   Новый private root файл 0600 с timestamp, SHA-256, integrity_check=ok и foreign_key_check=[].
   Сохранить unit/policy/launcher/config hashes приватно, не публиковать токен.
   Миграции/init/check_bot выполнять на одноразовой копии, сверяя schema и исторические строки.
4. Повторить read-only аудит pending duels/locks/PvP members/escrow/payout/publications.
   Неоднозначные операции блокируют переключение до согласованного завершения.
   Не вызывать maintenance/drain ради очистки.
5. Внешний staging launcher жёстко содержит APP_SHA и APP_TREE; policy содержит candidate_sha/tooling_sha.
   Нужна отдельно проверенная копия launcher с точным новым APP_SHA и новым git tree NEW_SHA:app.
   Остальные host/root/scopes/bot/Story/trust проверки сохраняются.
   Это обновление утверждённого allowlist, не снятие guards. Пересчитать launcher_sha256,
   candidate_sha, tooling_sha; env/topics hashes менять только при изменении соответствующих файлов.
   Manifest RELEASE_HARDENING_BUNDLE_SHA256.json сохраняет exact hashes существующего tooling.
6. Выполнить preflight без polling на копии БД; проверить права shpak-stage, физическую SQLite,
   отсутствие production scopes и singleton lock. Новый checkout получает прогресс из последнего
   остановленного TEST backup, а не пустую SQLite. Проверить все 79 героев/четыре босса/45 PNG.
7. Только после разрешения остановить shpakdnd-v15-staging.service, проверить отсутствие polling,
   сделать финальный stopped backup/checksum. Сохранить прежний code каталог целиком.
   Установить заранее подготовленный code через атомарный rename; последнюю остановленную SQLite
   перенести backup API в новый physical файл с прежними данными и правильным владельцем.
8. Штатные staging-launch.py --verify и --check. Root обновляет inventory, получает --describe,
   создаёт exact fingerprint start-approval с TTL 60 секунд. Запуск через штатный unit,
   без обхода доверенных owner/hash/no-symlink/per-bot flock проверок.
9. Проверить systemd active, один PID/cwd/SHA, polling без Conflict/Traceback, меню, регистрацию,
   сохранность игроков, инвентаря и ledger. Повторить игровые сценарии с участниками.
   Автоматические результаты не заменяют ручную приёмку.

## Откат TEST

Остановить только новый TEST и убедиться, что polling прекратился.
Сохранить его новую SQLite и логи консистентным backup. Вернуть сохранённый checkout/launcher/policy.
Схемных изменений нет, но старый код запускать только на проверенной совместимой копии последней
новой SQLite: восстановление старого backup после новых игровых операций потеряет прогресс.
При несовместимости ledger оставить TEST остановленным до согласованного восстановления;
деньги/ставки вручную не исправлять. Снова нужны верные SHA/hashes, inventory и свежее root approval.
Production rollback не применяется.

Исторический test-before-v15.db не использовать для отката текущего прогресса V1.5.
Удаление старых TEST-копий не входит в исправления.

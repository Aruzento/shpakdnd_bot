# V1.4.3.1 — deployment confirmation runbook

Статус: разработка / независимое ревью. PR #4. Основа:
5e4d4495a189380542cc643c5c8d723b9351b36b. Все команды ниже — план для оператора
после отдельного разрешения владельца; они не выполнены на VM-436996/VM-338333.

## Что известно

В инциденте новая версия прошла 1658 tests и проверки БД, но явное y не было
принято при запуске. Администратор затем запустил bot/watcher вручную. Это
сообщённые оператором обстоятельства; игровой сбой не установлен.

До изменения production script в Linux CI использован настоящий controlling PTY
(pty.fork), Bash, реальный stdout redactor с process substitution и tee,
трёхсекундная подставная проверка, три prompt/ответа по очереди. Старый скрипт
из 5e4d449 с обычными y работает; при y+CRLF на финальном prompt и выключенном
ICRNL возвращает 2, оставляет службы inactive. Наличие CR в реальном инциденте
не подтверждено. Смена/потребление stdin — также отдельный класс риска, а не
доказанная причина конкретного production отказа.

## Что исправлено

До preflight открывается выделенный descriptor: /dev/tty, если он доступен,
иначе копия исходного stdin. Дочерние команды получают /dev/null вместо ввода
оператора. Это защищает и интерактивный TTY, и pipe с заранее подготовленными
ответами. Redactor продолжает читать собственный stdout pipe и писать console/log.

confirm нормализует только внешние ASCII space/tab/CR и принимает прежний набор
y/Y/yes/д/Д. Пустая успешно прочитанная строка использует default; EOF,
незавершённое y без newline при EOF и read error не являются согласием.
Неизвестный ответ и n/N отказывают. Никакого автоматического запуска после отказа.

Prompts сохраняют newline и своевременный вывод через redactor. Произвольный
ответ не печатается; допустимы только события confirm accepted/declined/EOF.
Секреты .env и Telegram token pattern по-прежнему редактируются существующим
deploy_helpers.py. Передача stdout redactor сохраняется; его process-substitution
ветка игнорирует INT/TERM и завершается по EOF, когда основной shell закрывает
pipe. Иначе реальный Ctrl+C мог прервать Python redactor до interrupt summary,
потеряв ожидаемый exit 130. Основной shell продолжает обрабатывать INT/TERM
с кодами 130/143; это отдельно проверено настоящими control bytes в PTY.

failure_choice использует тот же descriptor, но прежние варианты и default 1
сохранены. rollback(), три rollback_preflight(), frozen helpers, installer,
pvp_ops.py, lock, backup и SQLite не менялись. Hotfix не разрешает обход
pause-new / escrow / Wallet / shards / publication checks.

## Проверки

Новые portable tests: pipe, все утвердительные варианты, CRLF, whitespace,
EOF до первого/финального prompt, частичная строка, n/N/неизвестный ответ,
пустой Enter, попытка дочерней команды читать stdin, read error, redaction,
изменившийся PID до watcher. Старые expectations не ослаблялись.

Linux PTY tests: исходное воспроизведение и контроль; три последовательных
явных ответа после видимого prompt с долгой проверкой; CRLF при raw CR;
controlling terminal с отдельным/EOF stdin; stdin-stealing child; Enter/EOF/n/
unknown; SIGTERM; failed startup; notice failure; failure_choice и три реальных
rollback gate на disposable SQLite. Shell/Git/systemd/Telegram замещены, Bash
и redactor настоящие. Полный старый rollback suite проверяет active escrow,
исчезновение frozen helper и изменение БД между gate.

Linux PTY tests требуют Linux; Windows выполняет portable Bash/pipe tests,
а PTY сценарии обязательно исполняются в обеих Linux CI jobs (3.11/3.13).
Платформенный skip Windows не заменяет Linux результат. Точное финальное SHA,
число тестов, CI и доказательства приведены в описании PR и отчёте Codex.

## Восстановление после отказа запуска без нового deployment

1. Зафиксировать installed HEAD, deployment log, backup path, unit states,
   PID/NRestarts. Не повторять deployment ради повторения prompt.
2. Убедиться, что установлен именно согласованный SHA и все release checks
   для этой версии прошли. Если проверки не прошли — сохранять остановку,
   разбирать отказ/штатный guarded rollback отдельно.
3. Получить явное разрешение владельца запустить уже проверенную установленную
   версию. Отказ/EOF в deploy не является таким разрешением.
4. В согласованном окне вручную запустить только bot:
       sudo systemctl start shpakdnd-bot.service
   Проверить active, дважды с интервалом не меньше штатных 5 секунд —
   неизменные MainPID/NRestarts, свежий journal без startup errors/restart loop.
   При ошибке остановить bot; watcher не запускать.
5. Только после стабильного bot:
       sudo systemctl start shpakdnd-bot-watch.path
   Проверить watcher active, bot остаётся active с тем же PID/NRestarts.
   Зафиксировать результат. Не запускать update service или новый deploy.
   Экономику и SQLite не исправлять вручную.

Это ручная процедура с согласованием версии, не новый автоматический fallback.

## Установка deployment tooling после разрешения

1. Независимое ревью ChatGPT и CI точного финального SHA; отдельно согласовать
   TEST smoke на VM-338333 с собственными token/world/DB. Не чистить app/topics.py,
   .env и untracked-файлы на стенде. Проверить копию установленного bundle в
   изолированном fake Git/systemd/Telegram контуре, затем согласованный smoke.
2. До production установки сохранить SHA/manifest и root ownership/modes
   текущего /usr/local/lib/shpakdnd-deploy, цель /usr/local/bin/deploy-shpakdnd.
   Сохранить целиком текущий V1.4.3 bundle с тремя PvP guards в отдельном
   root-only backup. Не подменять его более старой версией без PvP защиты.
3. Подготовить root-owned каталог проверенного артефакта вне production checkout,
   например /root/shpakdnd-tooling/V1.4.3.1. Сверить пять deploy helper/script файлов
   и installer с одобренным Git SHA. Git pull приложения сам root bundle не обновит.
4. При отсутствии другого deploy (installer использует прежний flock) выполнить
   только installer из проверенного каталога:
       sudo bash /root/shpakdnd-tooling/V1.4.3.1/deploy/install-deploy-shpakdnd.sh
   Он устанавливает root tooling, не запускает bot и не меняет игровую SQLite.
5. Записать active bot PID/NRestarts и watcher state. Выполнить только:
       sudo deploy-shpakdnd --dry-run
   После проверить те же PID/NRestarts и unit states, installed SHA256/modes,
   наличие pvp_ops.py и всех трёх gate. Dry-run выполняет fetch, но не checkout,
   notice, migration, backup, tests или restart. Сам полный deploy не нужен.
6. Сохранить install/dry-run evidence. Настоящий deployment приложения требует
   отдельного решения; hotfix сам не даёт такого разрешения.

## Откат только tooling

При отсутствии активного deployment, под тем же deployment lock и после явного
решения восстановить сохранённые root-owned файлы bundle и прежнюю symlink target,
сверить manifest/права и выполнить dry-run. Backup должен быть текущим V1.4.3
bundle с pvp_ops.py и 3x gate; не использовать bundle до введения PvP safety.

Это не git checkout приложения, не восстановление SQLite и не restart бота.
Проверить неизменность PID/NRestarts. Возврат старого confirm возвращает и его
дефект CRLF/stdin: до исправления использовать только согласованную ручную процедуру
запуска проверенной версии. Нельзя использовать tooling rollback для обхода PvP gate.

## Остаточные ограничения

Production root cause без исходного tty/stdin/байтов ответа не доказана.
Реальные TEST/production sudo, termios, runuser и systemd условия требуют
согласованного smoke. EOF/ошибка ввода намеренно оставляют стек остановленным.
Проверки по-прежнему выполняются в maintenance; перенос долгого suite до остановки
может обсуждаться отдельно и в этот hotfix не входит.

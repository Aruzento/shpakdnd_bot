# STAGING automation: установка и эксплуатация

Статус: исходники для review. Установка на TEST, Environment/Secrets и реальная
выкладка ещё не выполнялись. Production остаётся отдельным физическим сервером,
`sudo deploy-shpakdnd` из origin/main; существующий production script не меняется.
Инфраструктуру сначала объединить в main после review. Workflow на feature ref
не получит Environment secrets, если оператор настроил обязательное ограничение ниже.

## Доверие и границы

1. GitHub-hosted runner запускает staging-deploy.yml только с refs/heads/main.
   Checkout закреплён на github.sha диспетчера. Ни checkout, ни Python imports,
   pip install, action или shell script из PR в этом job не выполняются.
2. Gate принимает только текущий HEAD открытого same-repository PR в main,
   точный lowercase SHA, последний Linux release run этого PR/SHA и обе матрицы
   Python 3.11/3.13. Все обязательные steps должны быть completed/success.
   CI workflow blob feature-коммита должен совпадать с trusted main. CI явно
   checkout-ит HEAD PR, а не merge ref. При изменении CI infrastructure сначала
   review/merge в main; старый CI без exact-HEAD step не удовлетворяет gate.
3. Environment staging ограничить **только main**, включить required reviewers
   по политике владельца и запрет обхода администраторами, если тариф позволяет.
   Сам `if` в YAML не заменяет environment branch protection: feature workflow
   может менять свой YAML. Не выдавать secrets любым веткам/tags.
4. Отдельный ключ SSH позволяет только `deploy PR SHA RUN-ATTEMPT` и `report RUN-ATTEMPT`.
   Нет shell/SFTP/SCP/PTY/forwarding, произвольного sudo, пользовательского rc,
   env forwarding. Root-owned bundle вне checkout обновляется только оператором.
5. Controller повторно проверяет GitHub независимо от runner собственным read-only
   credential. Root не импортирует app и не запускает feature helpers. Feature
   tests/validators выполняются непривилегированно в одноразовом offline Docker
   container: source/repo/input/trusted mounts read-only, нет host token/.env,
   Docker socket, capabilities, host PID/network; ограничены CPU/RAM/PID/tmpfs.
   Это изоляция общего Linux kernel, а не защита от kernel exploit. Нужны
   обновлённые kernel/Docker/seccomp/AppArmor; permanent self-hosted runner отсутствует.
6. Runtime TEST также непривилегирован: root-owned read-only releases/venv,
   systemd ProtectSystem=strict, единственная writable host область — TEST data.
   Приложение по назначению видит только TEST token/DB. shpakbot/staging-deploy
   не должны иметь sudo/docker group или доступ к production credentials.

Документация: [exact PR HEAD](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
[environment protection](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments),
[OpenSSH restrictions](https://man.openbsd.org/sshd_config),
[container isolation flags](https://docs.docker.com/engine/containers/run/).

## Однократная установка оператором — только после отдельного разрешения

Все команды этого раздела — проект инструкции, Codex их на сервере не выполняет.
Войти своим административным каналом; пароль Codex не передавать. Проверить
`hostname` == **VM-338333**, адрес TEST **88.214.54.194:22**, token отдельного
бота, SQLite и текущую identity/paths через systemctl show. Сохранить status,
старый SHA, `.env`, `app/topics.py`, unit/drop-ins, venv/dependency freeze,
external editorial и внешние настройки. Другие службы/VM не трогать.

Требования: Python >=3.11 для controller, systemd/systemd-run, Git, Docker,
Bash/util-linux в preflight image, SQLite в Python, ≥4 GiB RAM/tmpfs и свободное
место для Git history, checkout и нескольких SQLite копий. На TEST hostname
не совпал — немедленно остановиться. Настроить время/NTP и TLS GitHub API.

1. Получить отдельный **проверенный merged main SHA** в admin checkout. Запустить
   `sudo bash deploy/staging/install.sh`. Он устанавливает только frozen tooling
   в `/usr/local/lib/shpakdnd-staging`, директории root-owned; не меняет units,
   SSH, current release, venv или БД. При будущих обновлениях bundle сначала
   дождаться отсутствия deployment и взять тот же `/var/lib/shpakdnd-staging/deploy.lock`
   через `flock -n` на всё обновление. Не устанавливать bundle из feature SHA.
2. Сохранить действующий TEST `.env` в `/etc/shpakdnd-staging/.env`
   (root:shpakbot, 0640). Сверить внешний topics.py с установленным. Сохранить
   `/etc/shpakdnd-staging/topics.py` (root:shpakbot, 0640): ровно один literal
   TOPIC_SETTINGS, только Mini TEST `-1003883723333:2` с mini=True и обычный
   D&D TEST `-5320347021:0` без mini. Без imports/functions/production scope.
   Сохранить внешний editorial в `/etc/shpakdnd-staging/editorial` root-owned,
   dirs 0755/files 0644. Динамический редактор не получает запись в control paths:
   оператор применяет проверенные изменения каталога отдельно.
3. Создать `/etc/shpakdnd-staging/runtime.env` (root:shpakbot 0640):

   ```ini
   MINI_EDITORIAL_DIR=/etc/shpakdnd-staging/editorial
   MINI_CUSTOM_EMOJI_ENABLED=false
   PYTHONDONTWRITEBYTECODE=1
   ```

   Если TEST использует custom emoji, сохранить его проверенное значение.
   Иные необходимые внешние настройки сначала инвентаризировать, перенести в
   root-owned config и явно разрешить; нет универсального wildcard copying
   production paths. Отсутствующая конфигурация блокирует активацию.
4. Подготовить root-owned virtualenv `/opt/shpakdnd-staging/venv` с текущими
   проверенными requirements и теми же Python/dependency versions, что preflight
   image. Не symlink-ить app-writable старый .venv. Установку dependencies делает
   оператор из reviewed main в отдельном maintenance window, никогда controller.
   Сохранить точный requirements.txt в `/etc/shpakdnd-staging/requirements.txt`
   root-owned 0644, `pip freeze --all` в `/etc/shpakdnd-staging/pip-freeze.txt` (root-owned 0644).
   Сверить freeze image и host venv; baseline входит в backup/report. Расхождение
   requirements feature с approved блокирует deployment до подготовки оператором.
5. Собрать `deploy/staging/Dockerfile` только из reviewed main с verified pinned
   Python base digest (build arg PYTHON_BASE). Например, из этого checkout:

   ```bash
   sudo docker build -f deploy/staging/Dockerfile \
     --build-arg PYTHON_BASE='python:3.13-slim@sha256:<проверенный digest>' \
     -t shpakdnd-preflight .
   ```

   Публиковать image только в доверенный registry либо загрузить уже проверенный
   image digest; controller использует --pull=never. Записать реальный RepoDigest
   в `/etc/shpakdnd-staging/settings.json` по settings.example.json. Image обязан
   содержать Git/Bash/flock, текущие dependencies и tzdata. Никаких feature builds
   с root/Docker socket. Проверить offline smoke и лимиты до включения dispatch.
6. Создать root-owned bare repo `/opt/shpakdnd-staging/repo.git`, remote ровно
   `https://github.com/Aruzento/shpakdnd_bot.git` без credentials в URL/config.
   Полная история нужна migration/rollback validators, включая исторические refs.
   Его objects/refs/config доступны контейнеру read-only (dirs 0755/files 0644),
   hooks выключены; не хранить secrets в этом repo. Для private repo root Git
   credential helper читает отдельный root-only read credential вне repo.
   `/etc/shpakdnd-staging/github-read-token`: root:root 0600, fine-grained read-only
   Contents/Pull requests/Actions только этого repo. Credential не в image,
   mounted Git config, config snapshots или reports. Ротация — оператором.
7. В согласованное TEST maintenance window **перед переносом DB**: зафиксировать
   активность watcher/update, остановить watch → update → bot и исключить других
   writers. Создать проверенный SQLite Backup API backup старой TEST DB, включая
   WAL; скопировать его в `/var/lib/shpakdnd-staging/data/shpakdnd.db`, владелец
   shpakbot:shpakbot, 0600, data dir 0700 shpakbot. Если нужен timers.db, перенести
   его таким же способом; исходники/backup оставить. Не делать cp живой WAL DB.
   Для backup можно использовать installed frozen controller.backup под python -I
   с явными source/destination; он проверяет Mini TEST scope/integrity/FK.
8. Подготовить начальный release из **текущего проверенного TEST SHA**, а не
   обновлять стенд попутно. Из root-owned repo выполнить git archive, проверить
   отсутствие .env/DB/venv/symlinks, распаковать в новый `/opt/shpakdnd-staging/releases/bootstrap`.
   Все dirs root:root 0755, файлы 0644. Записать SHA в `.revision`.
   Подменить только app/topics.py проверенной TEST копией и создать symlinks:

   ```text
   .env          -> /etc/shpakdnd-staging/.env
   shpakdnd.db   -> /var/lib/shpakdnd-staging/data/shpakdnd.db
   timers.db    -> /var/lib/shpakdnd-staging/data/timers.db
   /opt/shpakdnd-staging/current -> /opt/shpakdnd-staging/releases/bootstrap
   ```

   `current` и родитель root-owned; shpakbot не может их менять. Старый checkout
   `/opt/shpakdnd-bot` и старые backups сохранить для ручного recovery.
9. Установить `test-service.conf` как drop-in существующего TEST bot service,
   проверить systemd-analyze verify. Очистить конфликтующие drop-ins/Environment/
   ExecStartPre/Post, supplementary groups, writable paths/privileged hooks после
   ручного review: никаких feature shell hooks с root. Unit/drop-ins root-owned.
   **Маскировать legacy watcher/update** на TEST (`systemctl mask` иногда требует
   предварительно переместить локальные unit-файлы в сохранённый root backup).
   Они переключают код на main и несовместимы с managed feature releases.
   Зафиксировать active/enabled state; после каждого staging они остаются masked.
   Production watcher не меняется. `daemon-reload`, startup probe как hardened
   service user, запуск TEST bot и две проверки PID/NRestarts/journal/SQLite.
   NRestarts должен быть 0; оператор расследует предыдущие рестарты до reset-failed.
10. Создать отдельного пользователя `staging-deploy` с home root-owned, без
    привилегированных groups; разрешённый login shell нужен sshd для ForceCommand,
    пароль заблокирован, password auth запрещён. Установить sshd.conf в root-owned
    `/etc/ssh/sshd_config.d/90-shpakdnd-staging.conf` и sudoers в
    `/etc/sudoers.d/shpakdnd-staging` 0440; `visudo -cf`, `sshd -t` до reload SSH.
    Провести проверку с сохранённой admin-сессией. Проверить effective config
    `sshd -T -C user=staging-deploy,host=VM-338333,addr=<runner address>`.
11. Создать отдельную Ed25519 key pair у оператора. Публичный ключ — root-owned
    `/etc/ssh/authorized_keys/staging-deploy` (dir 0755, file 0644), строка:

    ```text
    restrict,command="/usr/bin/python3 -I /usr/local/lib/shpakdnd-staging/ssh_entry.py" ssh-ed25519 <PUBLIC_KEY> github-staging
    ```

    Private key — только GitHub Environment staging secret **STAGING_SSH_KEY**.
    Через независимый admin/console канал проверить host key fingerprint TEST;
    exact known_hosts line для 88.214.54.194 сохранить в environment secret
    **STAGING_KNOWN_HOSTS**. Не получать доверие через ssh-keyscan во время workflow.
    Host/IP/port/user фиксированы кодом; их изменение требует review.
12. Создать environment staging, restriction только main, reviewers/approval по
    политике владельца; добавить два secrets выше. Обязательно проверить отказ
    feature ref в доступе к secrets, shell/SFTP/PTY/forwarding/SSH env и sudo id,
    неверные hostname/SHA/PR/CI и concurrent request на изолированном TEST smoke.
    Проверить owner/modes всей цепочки bundle/config/releases/venv/repo и отсутствие
    sudo/docker у shpakbot и staging-deploy. Никакого runner service на TEST.

Пункты 7–9 — разовая миграция layout TEST с остановкой, отдельным backup и
явным разрешением оператора. Это не автоматический production deployment.
Если условия и scope существующего TEST отличаются, остановить setup и уточнить
инвентарь, не применять команды механически.

## Запуск и отчёт

[Ручной запуск](https://github.com/Aruzento/shpakdnd_bot/actions/workflows/staging-deploy.yml):
branch **main**, inputs **pr** и **sha** (ровно текущий HEAD, 40 lowercase hex).

```bash
gh workflow run staging-deploy.yml --repo Aruzento/shpakdnd_bot --ref main \
  -f pr=NUMBER -f sha=FULL_40_CHARACTER_SHA
```

Недостаточные права Codex/Actions — сообщить фактическую ошибку и эту ссылку с
точными pr/sha, не изображать deployment выполненным и не обходить SSH-паролем.
В этом инфраструктурном PR staging workflow пока отсутствует в main; его нельзя
валидно запустить до merge/setup. Открытый инфраструктурный PR не выкладывать
через feature copy workflow с секретами.

Контроллер до остановки: hostname, independent CI evidence, root ownership,
unit hardening/masked watcher, TEST scope, fetch точного PR SHA, новый release,
изоляция DB/config, полные тесты/JSON/images/relations/validators, миграция TEST
snapshot с сохранностью старых строк, repeated migration и old-code startup
на отдельной копии. Затем повторный CI/HEAD gate, проверенный online backup,
backup TEST config, stop TEST only, дополнительный quiescent Backup API snapshot,
atomic current switch, offline hardened startup/migration, bot start, две проверки
PID/NRestarts, journal и SQLite integrity/FK/scope. Изменение HEAD после финальной
проверки не меняет зафиксированный target: он остаётся тем же проверенным SHA.

Concurrency GitHub и root nonblocking OS lock независимы. RUN-ATTEMPT — ключ
идемпотентности; повтор с тем же target возвращает прежний результат без mutation,
смена target с прежним ключом запрещена. После crash без final report — отказ,
ручная проверка перед новым run. Новый Actions attempt — новая операция.
При отказе до stop текущий bot продолжает работать. После stop любой failure
останавливает TEST stack; code/DB автоматически не возвращаются. SIGKILL/падение
VM/SSH с неизвестным результатом требуют оператора; отсутствие final report — FAIL.

Artifacts `staging-RUN-ATTEMPT` (14 дней): admission.json, controller-response.json,
server-reports.json, **report.json**, **report.md**, preflight.log, startup.log,
journal.log. Если admission/SSH недоступны — отдельный FAIL report. Report содержит
SHA/PR/version, timestamps/CI run, inventory/tests count, validators, backup hashes,
SQLite, systemd и ручные сценарии. Live SQLite читается с effective uid/gid
shpakbot без supplementary groups, чтобы symlink из app-writable data не давал
доступ к root-only SQLite. Новые backup targets резервируются root эксклюзивно;
на копиях проверяются integrity/FK/scope до объявления backup успешным. PASS = **готовность к ручной приёмке**;
manual_acceptance всегда NOT_RUN до реального Telegram протокола пользователя.
Сырые TEST DB, .env/token, saved configs никогда не возвращаются по SSH/artifacts.
Feature preflight log не является доверенной командой или разрешением на recovery.

Root evidence/backups: `/var/lib/shpakdnd-staging/reports/RUN-ATTEMPT`, 0700;
preflight input с TEST данными монтируется только в одноразовый offline container.
Серверный retention/диск контролирует оператор; удаление только проверенных старых
report/release директорий после сохранения нужных backups и проверки current,
под тем же lock. Автоматического удаления/DB восстановления нет.

## Recovery и production readiness

Сначала отчёт/phase/installed SHA/current/systemd/journal. Сохранить текущую DB
через Backup API и конфигурацию до ручного восстановления. Старый код сначала
проверить **на новой копии текущей DB**, rollback gate frozen pvp_ops обязателен.
Активные PvP, неопределённые send/edit, escrow/ledger или несовместимая схема
запрещают rollback. Не обходить gate и не накатывать before.db поверх новых данных.
Для согласованного code recovery использовать сохранённый root-owned release,
повторить проверки до подготовки, перед сменой current и после startup перед start.
DB restore — отдельное разрешение, writers stopped, сохранение последнего состояния,
разбор потери данных, проверенные integrity/FK/scope и sidecars. SSH allowlist
специально не предоставляет recovery/restore operation.

`python deploy/staging/manifest.py --sha FULL_SHA --output release-manifest.json`
формирует проверяемый manifest по Git tree: SHA/tree, полный список test files и
commands, migrations, requirements/hash, rollback и production preflight. Сохранить
рядом с фактическими CI/staging artifacts. Сам manifest не доказывает прохождение
тестов; это проверяемый план плюс ссылки на evidence. Отдельный план следующего
production улучшения — [PRODUCTION-PREFLIGHT-PROPOSAL.md](PRODUCTION-PREFLIGHT-PROPOSAL.md).

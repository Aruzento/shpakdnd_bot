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
4. Отдельный ключ SSH позволяет только `deploy-v2 PR SHA RUN-ATTEMPT` и `report-v2 RUN-ATTEMPT`.
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
   Разрешены ровно три переменные выше. `.env` допускает только BOT_TOKEN и
   необязательный BOT_TIMEZONE=Europe/Moscow. Другие переменные, дубликаты,
   interpolation и дополнительные EnvironmentFile запрещены; расширение policy
   требует отдельного review. В settings.json задать test_bot_id — заранее
   проверенный числовой ID именно TEST-бота (пример 0 намеренно не проходит gate).
   Токен должен соответствовать этому ID; его значение не выводить. Отсутствующая конфигурация блокирует активацию.
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

   Локального build достаточно: получить immutable image ID командой
   `sudo docker image inspect shpakdnd-preflight --format '{{.Id}}'` и записать
   `sha256:<64 hex>` в `/etc/shpakdnd-staging/settings.json` по settings.example.json.
   Альтернатива — verified RepoDigest `registry/path@sha256:<64 hex>` из доверенного
   registry, заранее загруженный на TEST. Mutable tag запрещён; controller использует
   --pull=never. Публикация в registry для локального image ID не требуется. Image обязан
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
9. Установить `test-service.conf` строго как
   `/etc/systemd/system/shpakdnd-bot.service.d/90-staging.conf` для root-owned
   `/etc/systemd/system/shpakdnd-bot.service`,
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
останавливает TEST stack, включая transient startup unit; code/DB автоматически не возвращаются.
Startup имеет RuntimeMaxSec/TasksMax/MemoryMax и ограниченный log; при timeout
не оставляется отдельный migration writer. Runtime bot ограничен CPU/RAM/tasks. SIGKILL/падение
VM/SSH с неизвестным результатом требуют оператора; отсутствие final report — FAIL.

Artifacts `staging-RUN-ATTEMPT` (14 дней): только **admission.json**, **report.json**
и **report.md**. Ни сырые transport responses, ни preflight/startup/journal logs
не включаются в upload. SSH deploy-v2/report-v2 возвращают только public schema v2:
проверенные SHA/PR/request ID, enum phase/status, CI run ID, фиксированный inventory,
backup fingerprints, SQLite flags, ограниченные PID/restart counters и capacity
metadata. Произвольные строки ошибок, аргументы, Environment, EnvironmentFiles,
journal и feature stdout никогда не копируются в публичный отчёт. Markdown создаётся
на runner заново из отфильтрованного JSON, полученный Markdown не используется.
Версионированные SSH verbs не принимаются старым bundle: соединение с ним
не запрашивает raw report даже временно. Старые deploy/report verbs новым
bundle отвергаются. Старая schema v1 и неполные ответы дают FAIL; повтор старого request не запускает
deployment заново и не становится разрешением обойти новые проверки.

Полные **preflight.log**, **startup.raw.log**, **startup.log**, **journal.log** и
внутренний report.json/report.md остаются только в root-owned 0700 каталоге
`/var/lib/shpakdnd-staging/reports/RUN-ATTEMPT` с 0600 файлами. Это приватные данные:
они могут содержать строки TEST SQLite, аргументы и конфигурацию. Не прикладывать
их к PR/Actions/чату, не скачивать через forced SSH command. Regex-redaction не
является границей конфиденциальности и для экспорта не применяется. Контроллер
вообще не разбирает feature stdout как протокол; результат preflight определяется
exit status frozen driver, который последовательно исполняет фиксированный набор.

Если admission/SSH недоступны — отдельный ограниченный FAIL report. PASS означает
**готовность к ручной приёмке**; manual_acceptance остаётся NOT_RUN до Telegram
протокола пользователя. Подробности отказа разбирает оператор локально на TEST.
Live SQLite читается с effective uid/gid shpakbot без supplementary groups;
backup targets резервируются root эксклюзивно и проходят integrity/FK/scope.

## Эффективный runtime и пропуски тестов

Проверяется не подстрока ExecStart, а единственный точный executable/argv и пустые
privilege flags ExecStartEx. Shell wrappers, дополнительные аргументы, второй
command и +/!/ignore-failure flags запрещены. User/Group ровно shpakbot, uid/gid
ненулевые и без supplementary groups, WorkingDirectory и ReadWritePaths точные.
Environment/PassEnvironment/UnsetEnvironment/PAM/credentials/hooks пустые; только
один обязательный runtime.env. Manager environment допускает лишь проверенные
locale/PATH/TERM; BOT_TOKEN/PYTHONPATH/LD_PRELOAD и другие overrides блокируются.

Контроллер сверяет root-owned FragmentPath/единственный DropInPaths,
ProtectSystem/Home/Kernel*, PrivateTmp/Devices, NoNewPrivileges, capability sets,
RestrictSUIDSGID/AddressFamilies, cgroup/resource/kill policy. Другие RootImage,
bind mounts, временные mount overrides и автоматические writable directories
запрещены. Неизвестные/отсутствующие свойства — отказ, а не ослабление policy.
После настройки нужен daemon-reload; NeedDaemonReload=yes блокирует deploy.
Пустые Exec*/credential arrays дополнительно сверяются typed D-Bus через
busctl: только точная сигнатура с нулевой длиной. Отсутствие свойства или
[unprintable] не считается доказательством пустоты. Нужен systemd/busctl с
перечисленными свойствами; native CI probe проверяет фактическую сериализацию.
Файлы .env/runtime.env/topics.py закрепляются hashes: drift до stop или после
startup прекращает deployment. Frozen startup также проверяет ENV_PATH, BASE_DIR,
DB paths, offline token, timezone/editorial и TEST topics; после старта — scope БД.

`unittest_policy.py` исполняет весь discover -s tests -q и запрещает пустой suite,
новые expected failures и новые/необъяснённые skips. На Linux allowlist пуст:
**ноль skips**. Для разработки на Windows сохранены ровно 19 прежних PTY/POSIX
пропусков с точными test ID и reason; новая причина/ID не разрешены. Изменение
allowlist требует review, её нельзя автоматически учить на выводе feature tests.
На TEST применяется frozen runner из /trusted, а не версия PR. В Linux CI это тот
же full suite с явным skip gate; остальные validators/migration checks сохранены.
Native systemd probe создаёт только временный уникальный unit на одноразовом
GitHub-hosted runner и удаляет его после проверки. TEST/production он не вызывает.

## Свободное место и сохранение старых данных

До fetch/нового release/snapshot/Docker preflight и повторно перед backup/stop
проверяется свободное место **каждой** задействованной файловой системы. Объёмы
одного device суммируются, его free space не считается несколько раз:

- BASE: 1 GiB на временный release и Git preparation;
- STATE: три размера SQLite с учётом WAL/SHM (не менее 1 MiB каждый) + 128 MiB на logs/config/reports;
- DockerRootDir из docker info: 4 GiB рабочего headroom;
- дополнительно неприкосновенный floor 1 GiB на каждом device.

Docker /tmp остаётся ограниченным 4 GiB tmpfs: его RAM нужно подготовить отдельно,
как и memory limits. Disk check не является физическим резервированием и не
защищает от постороннего writer между проверками: оператор исключает внешние
bulk copies/GC и наблюдает за диском. Нехватка места до stop сохраняет работающий
TEST; ошибка записи после stop сохраняет существующие backups/DB и оставляет
стек остановленным для оператора. Автоматической очистки или восстановления нет.

Локальный read-only inventory под тем же nonblocking deploy.lock:

```bash
sudo /usr/bin/python3 -I /usr/local/lib/shpakdnd-staging/storage_policy.py
```

Он выводит пути/размер/возраст только оператору, помечает current/KEEP и сохраняет
все entries до явного review. `KEEP` — root-owned marker в нужном report/release,
например для неразобранного FAIL или кандидата rollback. Инвентарь не передаётся
в Actions и не является SSH operation. При capacity refusal: оператор сверяет
inventory/df, current и pending recovery, выбирает внешнее защищённое хранилище,
копирует/проверяет checksum и фиксирует ссылку на архив. Только затем отдельно
решает, что можно удалить локально. Ни возраст, ни успешный следующий run не дают
автоматического разрешения удалять potentially needed backup, current или KEEP.

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

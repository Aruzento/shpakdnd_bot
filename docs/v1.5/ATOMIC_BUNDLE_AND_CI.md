# V1.5: атомарная установка bundle и стабилизация Linux CI

Актуальный capability/guard пакет PR #9 и порядок установки/возврата комплекта:
[ROLLBACK_COMPATIBILITY_FEEDBACK.md](ROLLBACK_COMPATIBILITY_FEEDBACK.md).
Далее сохранено историческое свидетельство предыдущей installer/CI итерации.

Статус: **В разработке; кандидат на независимое ревью перед отдельным стендом**.
Реальный installer, deployment, rollback, systemd и Telegram не запускались.
Исходный аудит: `fc1c65a6bd7a9dd7a9bbd9f23ae71ad0849bc1fd`.
Точный проверенный SHA кандидата и результаты — в финальном отчёте Draft PR #8.

## P2-01: протокол установки

`sudo bash deploy/install-deploy-shpakdnd.sh --sha <полный проверенный SHA>`
требует Linux/root, явного SHA=HEAD и точного совпадения всех 9 runtime-файлов,
installer shell и Python с Git blobs этого SHA. Symlink source-файлы запрещены.
Рабочая SQLite, `.env`, Stories, bot token и systemd не читаются installer-ом.
Git objects и отдельно проверенный source/SHA являются источником доверия.
Установка из произвольной папки или с изменённым helper блокируется.

1. Получить тот же `/run/lock/shpakdnd-deploy.lock`, который использует deployment.
   Реальный `flock(LOCK_EX|LOCK_NB)` действует до завершения installer.
2. Проверить абсолютные пути, родителей, права/owner и старую ссылку. Не следовать
   symlink в каталогах/lock/helpers. Допускаются только известный legacy target
   и `releases/<40 hex>/launch.sh`. Физический entrypoint не перезаписывается.
3. Подготовить `/usr/local/lib/shpakdnd-deploy/releases/.staging-<random>` на том же
   filesystem, копируя только Git-проверенные bytes, а не меняющиеся source-файлы.
4. После каждого файла flush/fsync; сгенерировать полный manifest SHA-256 и launcher.
   Helpers/schema/manifest имеют `0444`, shell/launcher `0555`, version-dir `0555`.
   Полный набор: 9 runtime-файлов + `bundle.json` + `launch.sh`, никаких лишних файлов.
   Все файлы обычные, с одним hardlink, root-owned. Проверить bytes, modes и состав.
5. fsync каталога, rename stage → `releases/<SHA>`, fsync releases, повторная проверка.
   Существующий SHA никогда не перезаписывается: только byte-identical reuse.
6. Создать временную ссылку рядом с `/usr/local/bin/deploy-shpakdnd`; проверить, что
   entrypoint не изменился; один `os.replace()` атомарно переключает ссылку; fsync bin.
   Непустой legacy-каталог остаётся физическим каталогом. Старые bundles сохраняются.

До переключения любая ошибка оставляет прежний entrypoint. После переключения
доступна только полная новая версия; отказ fsync/авария после rename не создаёт смеси.
Незавершённые staging-каталоги остаются для диагностики, никогда не переиспользуются.
Завершённый, но ещё не активированный SHA безопасно проверяется при следующем запуске.
Нет автоматического удаления staging/предыдущих releases и нет скрытой GC-политики.
Заброшенные диагностические каталоги нужно учитывать при планировании свободного места.

Launcher разрешает путь один раз, запускает встроенный stdlib verifier через
`/usr/bin/python3 -I -B`, проверяет pinned SHA-256 manifest, owner/modes, parents,
все helper-хеши, hardlinks и точный состав до исполнения deploy shell. До импорта
Python helpers код из bundle не исполняется. Это обнаруживает случайную порчу и
непривилегированную подмену. Злонамеренный root может переписать launcher/диск;
защита от root или физического повреждения хранилища не заявляется.
Read-only modes — защита от обычной записи, не Linux immutable attribute/chattr.

Старые legacy helpers не удаляются и не перезаписываются. Безопасный переход выполняется
без остановки бота. Вызовы `sudo deploy-shpakdnd`, `--dry-run`, `--preflight-only`
сохраняются. Launcher exec сохраняет TTY/сигналы/аргументы. Все прежние подтверждения,
redaction, frozen PvP/V15 guards, exact-SHA preflight и Story gate остаются прежними.
Дополнительно shell теперь копируется в frozen TOOLS: он нужен существующей
`validate_frozen_bundle()`, но отсутствовал в прежнем цикле копирования helpers.

## Installer rollback отдельно от отката игры

Только после отдельного разрешения оператора, на хосте с доверенным checkout:

```bash
# CURRENT — проверенный SHA installer source, PREVIOUS — существующий release SHA.
# Эти значения брать из независимого отчёта/инвентаря, не из случайной ссылки.
sudo bash deploy/install-deploy-shpakdnd.sh --sha "$CURRENT" --activate-existing "$PREVIOUS"
sudo deploy-shpakdnd --dry-run
```

Операция берёт общий lock, проверяет текущий installer source, сверяет bytes старого
комплекта с Git blobs PREVIOUS, его manifest/launcher/modes и атомарно переключает
только entrypoint. Отсутствующий или несовместимый формат старого bundle блокируется.
Она не переключает app Git HEAD, не откатывает SQLite и не перезапускает сервисы.
Legacy target остаётся для ручного forensic-восстановления, но без manifest не считается
проверенным versioned release: автоматический возврат к нему не предлагается.
Никогда не делать `rm -rf` прежнего каталога или `ln -sfn` поверх live physical dir.
Откат игрового приложения требует отдельного frozen guard/drain/согласования:
[RELEASE_HARDENING.md](RELEASE_HARDENING.md), [RELEASE_AND_ROLLBACK.md](RELEASE_AND_ROLLBACK.md).

## P2-02: измеренная причина timeout

Run `40390325075`, attempt 1, Python 3.13: full unittest 1030.347s;
повторные protected PTY 95.025s и shop 217.392s, D&D compatibility ~106s,
content diagnostics ~110s, затем Story candidate отменён на 30-минутном лимите.
Это сумма полных проверок и повторных entrypoint regressions на медленном runner,
а не зависший один тест. После timeout всегда выполняемые migration/rollback gates
продолжили работу; hardening gate был skipped из-за отмены — первая попытка не PASS.
Attempt 2: full unittest 3.11=752.679s, 3.13=845.605s; обе jobs завершились успешно.
Предыдущий успех не используется как доказательство нового SHA.

Full discovery уже включает PTY и shop. Отдельные повторы оставлены: это существующие
обязательные gates, удаление не используется как ускорение. Read-only Hero/Boss/Tower/
policy validators можно параллелить; проверки, использующие общий startup.db, publish
CSV и benchmark, нельзя запускать совместно без отдельных sandbox. Независимые
матрицы Python уже идут параллельно. В этом изменении сохранены два прежних required
job context, последовательность и fail-closed поведение, без риска незамеченного
изменения branch protection. Нет continue-on-error и новых skips.

Лимит увеличен до 60 минут, добавлен отдельный вызов полного `check_v15.py` через
изолированный launcher. BASH_ENV hook записывает per-step timestamps, exit code,
seconds, SHA и stdout/stderr; снимает BASH_ENV перед дочерними shell/PTY. Артефакты
release-diagnostics загружаются `always()`, старый loot benchmark artifact сохранён.
Hook не включает shell xtrace и не печатает env. При жёстком убийстве runner artifact
может не выгрузиться; уже записанные partial logs и GitHub job logs остаются основой
диагностики. Ни один конечный timeout не гарантирует PASS при отказе runner: требуется
полный успешный прогон именно кандидата, а не принятие предыдущего SHA.

## Проверки и неизменность

`tests/v1_5/test_atomic_bundle.py`: подготовка/повторная установка, полный состав,
все критические файлы/фазы, процессный exit после publish/activate, старый entrypoint,
corruption/missing/extra/manifest/launcher, disk/permissions, symlink/path, общий flock,
повторная активация старого versioned bundle, freeze, installed dry/preflight, TTY и
unsafe rollback. Только временные caller-owned filesystem fixtures.

Windows без права symlink выполняет native fail-closed и portable branches;
настоящие symlink/fork/flock/installed launcher/controlling PTY исполняются на Linux
3.11 и 3.13, без новых skip. Полные результаты и длительности — в финальном отчёте.

Приложение `app` должно сохранять tree `64823c903daa7e3cea5d12613f364da67f630a54`;
авторские JSON, 79 героев, 4 рейда, все 45 PNG и игровая схема не меняются.
MOON_TEXT_PROPOSAL не применён. Контрпик при actual=0 не изменён.
Обязательны независимое повторное ревью, проверка предоставленной offline-копии
production SQLite и реальный [Telegram staging](STAGING_ACCEPTANCE.md).

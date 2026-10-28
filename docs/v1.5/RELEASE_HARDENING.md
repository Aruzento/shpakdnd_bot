# V1.5 release hardening

Актуальное дополнение PR #9: [Исторические SQLite contracts](HISTORICAL_SQLITE_CONTRACTS.md). Поддержаны только переданные владельцем DDL и проверенные migration descendants; отсутствие исторических CHECK компенсируется read-only диагностикой. Production-copy Codex не проверял. Найденный runtime integer-overflow blocker описан отдельно; app не изменён.

Текущая задача PR #9: [capability feedback / безопасный rollback](ROLLBACK_COMPATIBILITY_FEEDBACK.md).
Игровой кандидат 046f7dc / app tree cb678fe9f0978bb115a12e27c56a7160d0c94e4c;
capability v15-feedback-ui-stories. Финальный tooling SHA/CI — в отчёте PR #9.
Main, production, SQLite, Stories и сервисы этой задачей не меняются.
Результат требует независимого ревью; production deployment не разрешён.

## Историческое свидетельство — 26.10.2026

Следующий раздел сохранён как свидетельство прежнего аудита cef77cc/PR #8.
Его NOT_RUN/NOT_PUBLISHED и указатели не обозначают текущее состояние приёмки владельцем.
Актуальные ограничения и матрица приведены в документе PR #9 выше.

Статус: **В разработке / Релиз заблокирован**. Основа — независимый аудит
[cef77cc87f6a2286284f5cf8fdb851c775b04dd1](INDEPENDENT_AUDIT_CEF77CC.md),
[утверждённое ТЗ](RELEASE_HARDENING_APPROVED_TZ.md). Последний независимо проверенный
срез — cef77cc; финальный SHA/CI нового исправления фиксируются в отчёте PR #8 после
последнего commit. Этот файл не выдаёт pending проверки за PASS и не разрешает выкладку.

| Функциональность | Код | Unit+integration | CI финального SHA | Реальная БД | Telegram staging | Откат | Release verdict |
|---|---|---|---|---|---|---|---|
| Normal/Raid, O01–O10/R01–R20 | прежнее audited app tree, без правок | полная регрессия, итог в PR | см. exact-SHA report | NOT_RUN | NOT_RUN | capability guard | BLOCKED |
| 79 героев,4Raid,45PNG | REAL_CONTENT_IN_REPO=PASS | hashes/контракты | см. exact-SHA report | NOT_RUN | NOT_RUN | ledger/ownership guard | BLOCKED |
| История V2,53/113/7 | REAL_CONTENT_IN_REPO=PASS | seven endings/10k/once | см. exact-SHA report | NOT_RUN | NOT_RUN | active once guard | BLOCKED |
| Двухфазный deploy | реализован, офлайн mocks | intentional failures/PTY | см. exact-SHA report | NOT_RUN | NOT_RUN | no automatic DB restore | BLOCKED |
| Read-only rollback matrix | реализован | mode=ro/query_only/negative | см. exact-SHA report | NOT_RUN | NOT_RUN | unknown/unsafe BLOCKED | BLOCKED |
| Внешний target Story | STORY_EXTERNAL_TARGET=NOT_PUBLISHED | STRICT_SANDBOX=PASS на cef77cc; повтор в финальном отчёте | отдельный sandbox gate | STRICT_PRODUCTION=NOT_CHECKED/BLOCKED | NOT_RUN | no automatic copy | BLOCKED |

Единые статусы: REAL_CONTENT_IN_REPO=PASS, STORY_EXTERNAL_TARGET=NOT_PUBLISHED,
STRICT_SANDBOX=PASS (независимо проверенный cef77cc, новый SHA — отдельный отчёт),
STRICT_PRODUCTION=NOT_CHECKED/BLOCKED, PROD_DB_COPY=NOT_RUN, STAGING=NOT_RUN.
Контент больше не отсутствует: не выполнены внешняя публикация и эксплуатационная приёмка.

## Две фазы

1. Deploy lock, clean live main/upstream origin/main, права/Python/systemd, ранний fetch;
   OLD_SHA и immutable TARGET_SHA. Dry-run показывает gates без остановок/SQLite writes.
2. Вне Git/watcher создан защищённый mktemp workspace (минимум8GiB свободного места). Detached local shared clone
   TARGET_SHA, без .env/DB; live checkout не переключается. Полный unittest,12 validators,
   Hero/Boss/Tower/Editorial/Mythic/Story/PvP, migration3x, D&D/shop/loot/PTY и diff-check.
   Все nested tmp/архивы ограничены собственным scratch и удаляются вместе с workspace;
   reports/logs сохраняются private evidence.zip. Fake token, sandbox DB/Stories,
   audit hook запрещает Python open/sqlite live DB/.env
   и socket.connect. Отдельный explicit content gate читает только целевой Stories.
3. Все команды/exit/time/Python/SHA/sandbox paths в release-gates.json; receipt содержит
   run ID, TARGET_SHA, OLD_SHA, fingerprint всех target JSON до/после полного preflight,
   hash .env и статических systemd settings (без записи секретов). Конфликт runtime
   STORIES_DIR/другой EnvironmentFile или смена конфигурации блокирует stop/start.
   Любой nonzero gate оставляет live bot/watcher/HEAD прежними. После operator consent
   fetch/recheck HEAD/origin/main/clean/candidate/receipt/content, только затем stop.
4. После stop: финальный консистентный backup+integrity/FK+SHA256; явное подтверждение
   checkout ровно TARGET_SHA; повторный recheck. Короткие compile/config/startup/content
   fingerprint/SQLite/HEAD/clean gates; полный unittest после stop больше не запускается.
   Отдельное подтверждение start и немедленная проверка receipt перед запуском; PID,
   restarts, fresh journal и watcher проверяются прежним защищённым порядком.
5. Любой провал после stop сохраняет fail-closed. Оба failure-choice rollback пути
   проверяются frozen PvP + V1.5 guards трижды вокруг checkout/startup. DB backup никогда
   автоматически не восстанавливается. Receipts/логи копируются в private deploy logs,
   удаляется только собственный mktemp workspace. Нет --force bypass.

## Capability / SQLite matrix

Whole app trees:64823c903daa7e3cea5d12613f364da67f630a54 (audited cef77cc, V1.5/once),
908868852fc3b1e132a65e325ca546025b4034de (audited d077dd2a, pre-V1.5). Новые/неизвестные
app trees требуют независимого review/явного обновления whitelist; название ветки,
startup PASS или файл self-declared capabilities недостаточны. Guard не исполняет target.

| Состояние | Проверка | Старый несовместимый target |
|---|---|---|
| Raid active/rewarded/cancelled, attacks/rewards/audit/cancel requests | schema/status/counts, сохранённые операции | BLOCKED, даже после drain из-за истории |
| Story active/registration/terminal v1/v2, steps/votes/operations | frozen schema/once; известная регистрация овцы=v2; другая неоднозначная registration BLOCKED | BLOCKED |
| Story/raid pending publication | count; совместимый код умеет claim | BLOCKED для старого |
| sending/uncertain publication | manual delivery review | BLOCKED для любого target до отдельного плана |
| Pet inventory/active/reserve/adult/evolution_pending, часы/CD/days | все сохранённые строки, stage/location | BLOCKED; reserve не делает downgrade безопасным |
| Universal balances/ledger/operations/Star/lunar/Arise, owned Mythic | ненулевые балансы и любые сохранённые receipts/ownership | BLOCKED; нулевой net ledger тоже сохраняет смысл |
| PvP escrow/controls/publications | существующий frozen economic guard | BLOCKED до pause/drain/разрешения receipt |
| Boss announced/ready/fighting и Tower active snapshots | реальные статусы/runtime | BLOCKED несовместимому target |
| Unknown target/schema/status/corrupt DB/partial schema | mode=ro/query_only, integrity/FK и inventory | BLOCKED с reason code, без старого executable |

Отсутствующие V1.5 таблицы могут быть исторической pre-V1.5 схемой; частичная/неясная
схема и пустая неизвестная БД не считаются безопасными. Guard не меняет schema/state,
не делает drain, refunds или review и не начинает старый код. Агрегированные counts,
reason codes, current/target SHA без PII. Schema inventory не является миграцией.

## Target content gate

Явный абсолютный внешний STORIES_DIR; нельзя Git/watcher, symlink/junction/file вне
каталога. Необходим точный tayna_propavshey_ovtsy.json approved SHA256 из авторской
поставки, code/schema2/once/53nodes/113choices/7endings. Дополнительные валидные авторские
JSON разрешены; любые broken/duplicate/nonstory JSON блокируют. Ничего не копируется
автоматически из Git. CODE_CONTENT_PASS, TARGET_STORIES_DIR_READY и
STRICT_CONTENT_TARGET_PASS различаются. Missing=2, invalid/hash/symlink=1, full=0.
При раннем missing gate ещё не выполненные дорогие code checks остаются NOT_VERIFIED.

## Следующие действия — только по отдельным разрешениям

Независимое повторное review → offline production backup copy+hash → migration3x/FK/
integrity/ownership/wallet/legacy/active runtime на копиях → отдельное разрешение
реального Telegram staging (2players+admin, bosses/pets/tower/all7Story endings/crash) →
новый exact-SHA CI → отдельное разрешение merge→main → отдельное разрешение production.

В будущем, только по отдельному разрешению на соответствующем стенде:

```
sudo deploy-shpakdnd --dry-run
sudo deploy-shpakdnd --preflight-only
sudo deploy-shpakdnd
```

Frozen helpers должны совпадать побайтово с reviewed target bundle. Их обновление
через install-deploy-shpakdnd.sh — только после отдельного разрешения из независимого
проверенного checkout, без предварительного переключения live checkout. Несовпадение
BLOCKED_FROZEN_BUNDLE_TARGET_MISMATCH требует review/обновления bundle, не bypass.
Целевой каталог обязательно объявлен literal absolute STORIES_DIR в .env.
SHPAKDND_STORIES_DIR — дополнительная проверка равенства, не override runtime; никакого
неявного выбора директории и никакой установки сюжета скриптом. Для владельца staging:
создать отдельный внешний каталог, вручную перенести ТОЛЬКО approved JSON после
разрешения, сравнить SHA256, запустить isolated validate_release_content с --directory.
Manifest/audit JSON туда не переносить. Production публикация — отдельное решение.
Онлайн read-only snapshot/предоставленная offline копия для предварительной миграции
не заменяет финальный backup после stop. Сейчас production SQLite/Stories NOT CHECKED.

Rollback decision: compatible reviewed app+resolved state → frozen guards+smoke;
incompatible/unknown app или unresolved state → сохранить SHA/backup, оставить стоп,
отдельный drain совместимым кодом/новое промежуточное исправление. Drain активных сессий
не стирает экономику/ownership/history, поэтому сам по себе не разрешает downgrade.
Тексты Moon — только [proposal](MOON_TEXT_PROPOSAL.md); uncertainty — [runbook](TELEGRAM_UNCERTAIN_RUNBOOK.md).
Первая подтверждённая counter атака активирует эффект и при actual HP damage=0 (O02).
Moon arcana выбирается до фактического урона и сохраняется без повторного RNG.

Windows offline checks допускают только внутреннее соединение stdlib socketpair
с собственным временным listener для asyncio; обычный localhost и внешний network
остаются запрещены. Это Python audit/path isolation, не OS sandbox для недоверенного
нативного кода. Regression запускает настоящий asyncio и отдельно запрещённый connect.
PvP rollback сохраняет подробный raw report только в private chmod600 JSON. Публичный
вывод сохраняет pause_new_required/maintenance_control_missing и агрегированный
pvp_session_review_required, без session/player ID и свободного текста. Неполный report
даёт pvp_report_unavailable, не разрешение rollback. Старый frozen-helper regression
получает полный современный bundle; все его исходные защитные assertions сохранены.

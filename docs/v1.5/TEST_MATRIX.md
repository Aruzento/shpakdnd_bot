# Проверки утверждённых O01–O10

> Актуальный срез 26.10.2026: [RELEASE_HARDENING.md](RELEASE_HARDENING.md).
> REAL_CONTENT_IN_REPO=PASS (79 heroes/4Raid/45PNG и Story53/113/7),
> STORY_EXTERNAL_TARGET=NOT_PUBLISHED, STRICT_SANDBOX=PASS на independently audited
> cef77cc87f6a2286284f5cf8fdb851c775b04dd1; новый final SHA/CI — в отчёте PR #8.
> STRICT_PRODUCTION=NOT_CHECKED/BLOCKED, PROD_DB_COPY=NOT_RUN, STAGING=NOT_RUN.
> **В разработке / Релиз заблокирован.** Упоминания отсутствующего контента ниже —
> исторические срезы до импорта, не текущий статус O03/O09.

## Исторические сведения (срезы до текущего hardening)


Все app checks через `python scripts/run_isolated_checks.py --label NAME -- <args>`: временные DB/legacy/STORIES_DIR, фиктивныйtoken, безpolling. Telegram tests запрещают неожиданныйHTTP, transport mocked, сервисы/SQLite реальные.

| Команда / набор | Доказательство |
|---|---|
| `-m unittest discover -s tests -q` | Полный набор наfinalcommit, без скрытых failures/newskips |
| `-m unittest discover -s tests/v1_5 -q` | Все прежниеV1.5 и новыеO01–O10; список методов в test_approved_o01_o10.py |
| ApprovedRaid/CompensationArithmetic | P/tie/D0/P0/D>M/Earth-attribution conservation,no extra bonuses/egg clawback,two admins,final hit race,rollback,revision,legacycounter,newzerocounter,overflow,Eclipseallstars |
| ApprovedNormal/ApprovedTower | Per-playerStar/highestfrozen/eligible/no-hit/timeout,oldactivepolicy,forbiddenoutcomes,blessingindependence,chestonce,Goldenbaseonly,firstentry/hero-switch,noReroll,16shieldcombos,validShadow/zero |
| ApprovedContracts/ApprovedTelegram | Typedno-leak,4Boss/schema/PNG/refs,strictCLIcomplete0/missing2/invalid1,preview/reason/scope/confirm/history/pages,paid confirmation/replay/manual reserve |
| `tools/check_v15.py` |12offline checks:compileall,check_bot,protectedbundle,APPROVEDpolicy,hero/bossabilities,Tower,V142,Editorial,contentdiagnostic,Editorialrelease,PvPmigration |
| `tools/validate_v15_migration.py` | Old-code populated fixture/nonzerolegacy,3migrations,integrity/FK,oldcodeonothercopy |
| ApprovedRaid populatednewjournals + tests.test_mini_migrations |3replays with nonempty Star/cancel requests/rewards/ledger/legacy7, old historical tables/values retained |
| D&D shop/testB/coins/loot + benchmark | OrdinaryD&D compatibility,real catalog4450/twoscopes,disposableCLI/accurate reconciliation |
| `-m app.mini.mythic.content --strict-content` | Real content intentionally BLOCKED_BY_INPUT/exit2 until nextauthor stage; not a PASS |
| `tools/import_v15_s5.py --help` and dry-run | Import interface and safe refusal without real17PNG; noapply now |
| GitHub Linux3.11/3.13 | Checkout explicit PRheadSHA orworkflow_dispatchSHA, equalityassertionbeforetests, fullsuite+original35PTY/input+allvalidators/migrations/old-codecopy |
| Both git diff --check commands | Clean feature and entirePRdiff |

Baseline dfe7df7:100V1.5tests PASS16.690s; previousfull2001/Windows19platformskips and Linuxboth0skips. Firstnewregression100cases showed4obsoleteO01expectations; updated solely perapprovedcontract. One oldmigrationfixture needed exact2newtable metadata; added populated3replay test, did not hide user-data assertions. Original protecteddeploy tests unchanged. Intermediate focused/validators results in WORK_LOG; final SHA/count/skip/exit/log/CI are recorded after lastcommit in the final report/PR.

Current live content/staging/productioncopy/realTelegram NOTRUN. Author pack arrival requires full repeat at nextfinalSHA and live isolated acceptance.

## Четыре механики рейдов

tests/v1_5/test_raid_mechanics.py добавляет pure/schema, service/SQLite/replay/concurrency,
а также реальные Telegram handler→service→photo/edit tests на четырёх temporary Boss/PNG.
Покрыты exact cycle indices, HP±1, Sun timing, healing325/clamp/fatal, Eclipse/original fund,
all8Mythic, 10/day/hero-day, ledger attribution, INSERT rollback, cancel/fatal race и migration3x.
Фото transport в прежнем TelegramAudit теперь mock-ается, HTTP остаётся запрещён.

Дополнение после полного Linux-прогона кандидата 93d7fa1: старый V1.4.2 guard
защищал целиком hero_images от любых новых файлов и отклонил прежнюю разрешённую
поставку S5/Mythic. Guard сохранён и уточнён: каждое изменение сравнивается с тем же
историческим SHA 293693d8; допустим только статус A для точных 17 S5 + 24 Mythic путей.
Любое M/D исходных артов/Shadow JSON/баланса и любое постороннее добавление по-прежнему
роняет тест. Тест не удалён/не skip'нут; он продолжает защищать все исходные байты.
Финальный полный прогон повторяется на новом коммите после этого исправления.

## Согласованный допуск белого фона — 26.10.2026

AssetMatrix: пять новых тестов проверяют RGB 252–255, отклонение 251 в каждом канале
и углу, все пять поддерживаемых PNG color types, точный alpha 255 и отклонение
отсутствующего palette entry. Чёрный фон остаётся строго 0/0/0 (1 и 3 отклоняются).
Read-only S5 dry-run проверяет все 17 настоящих PNG без импорта записей.
Полные Windows/Linux проверки выполняются на итоговом SHA; результаты — в PR/отчёте.

## Content finalization 26.10.2026

Новый tests/v1_5/test_content_finalization.py: 26 тестов. Проверяются все реальные 45 PNG
по исходным SHA-256, RGBA и размерам независимо от author records; black 0..4/5,
white 252..255/251, все четыре угла, alpha, tRNS, CRC/scanlines/filter/shape/missing.
Importer: same path/external identical/different bytes, partial/conflicting records,
missing PNG, staged disk failure, rollback, lock, stale concurrent plan, повторный no-op.
Реальные 17 S5: гача/SQLite/дубликаты/shards/ranks/passives/replay, pool exclusions,
конкурентная операция, Telegram handler→service→SQLite→настоящие PNG, Raid ledger/equipment.
Внеигровые Mythic/Raid templates проверяются на временных полных карточках, Story template
— schema/DAG/UTF-16/external CLI и handler list/open/start→vote/restart/kick/cancel без экономики.
Прежние black-boundary и strict-complete fixtures обновлены по новому approved ТЗ;
тесты не удалены и не skip-нуты. Ранее белый допуск не расширен.
Полный exact-SHA Windows/Linux, 12 validators, D&D и migration/PTY — в итоговом отчёте.

Content-finalization full-suite correction: дополнительно test_release_preservation
проверяет исторический source fixture на том же pinned old ref и отдельно новый S5
snapshot на копии. Только active=0 для точных 17 новых кодов допускается старым каталогом;
остальные typed columns/rows, кошельки, ownership, copies/stars проверяются прежним guard.
Итого 27 добавленных тестов: 26 в новом V1.5 модуле + один в compatibility suite.
Динамическая UI-матрица включает новый trait=demonic из настоящих S5.


Диагностика 85 предшествующих gacha cases установила точную причину membership failure:
старый Gacha142Tests.concurrent запускал self.pull(), внутри которого каждый поток
создавал patch одного общего _choose_hero_code. При пересечении cleanup оставлял
MagicMock в модуле после теста. Patch перенесён наружу ThreadPoolExecutor, workers
вызывают настоящий perform_gacha_pull; добавлена проверка восстановления исходной функции.
Исходный новый weighted-pool тест сохранён без подмены каталога/весов; проверка не ослаблена.


Полный a1a1d43: Linux3.13 прошёл suite, Linux3.11 выявил ту же гонку ещё в старом
BatchTests.test_replay_restart_and_parallel_requests. Расширенная диагностика всех
модулей, подменяющих _choose_hero_code, нашла первую утечку именно там. Общий patch
также перенесён за пределы batch workers; проверяется восстановление исходной функции.
Исходный S5 weighted-pool test не меняется. Полные exact-SHA проверки повторяются.


## Настоящий авторский контент 8+4

`tests/v1_5/test_author_content.py`: manifest byte identity, preservation71/count79,
8 mode registry links and invalid author fields, four strict boss profiles,
real pet evolution/pending/restart/one-time grant/stars6, real normal service
battles and raid cycles/heal/HP phases, first counter including zero, R09 funds,
concurrent receipt replay, SQLite sync3x, Telegram cards/photos/raid callbacks.
Readiness accepts Mythic/Raid and leaves only external Stories blocked (strict2).
Все прежние отрицательные тесты сохраняются. Полная временная фикстура strict0
заменяет настоящие Mythic временными, не дублирует их. Исторический old-code
copy guard разрешает только active=0 для 17 S5 + 8 Mythic, остальные поля и
владение/деньги/звёзды строго сохраняются. Финальные exact-SHA проверки и логи —
в отчёте и draft PR #8, результаты предыдущего SHA не считаются текущими.

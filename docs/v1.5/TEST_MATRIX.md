# Проверки утверждённых O01–O10

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

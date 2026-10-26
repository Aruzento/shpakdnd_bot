# Будущая приёмка после авторского контента — NOT RUN

> Актуальный срез 26.10.2026: [RELEASE_HARDENING.md](RELEASE_HARDENING.md).
> REAL_CONTENT_IN_REPO=PASS (79 heroes/4Raid/45PNG и Story53/113/7),
> STORY_EXTERNAL_TARGET=NOT_PUBLISHED, STRICT_SANDBOX=PASS на independently audited
> cef77cc87f6a2286284f5cf8fdb851c775b04dd1; новый final SHA/CI — в отчёте PR #8.
> STRICT_PRODUCTION=NOT_CHECKED/BLOCKED, PROD_DB_COPY=NOT_RUN, STAGING=NOT_RUN.
> **В разработке / Релиз заблокирован.** Упоминания отсутствующего контента ниже —
> исторические срезы до импорта, не текущий статус O03/O09.

## Исторические сведения (срезы до текущего hardening)


Статус «В разработке». Этот документ не разрешает deployment. В данной итерации staging/production/systemd/polling/рабочаяSQLite не затрагивались. Сначала полный45PNG/8Mythic/4Boss/Storyкомплект и strict0, полныйтестнановомSHA+Linux3.11/3.13. До остановки любого рабочего бота выполнить изолированныеpreflight. Раздельные .env/token/SQLite/STORIES_DIR, productioncopy только явно предоставленная владельцем.

Протокол:2игрока A/B, админC. Для каждого сценария записать exactSHA,chat/thread,userIDs,messageIDs,DBbackuphash,таблицыдо/после,логи безсекретов.

| Действия | До | Ожидаемое после |
|---|---|---|
| O01:A/B положительныйRaidвклад,Ccancelaskwithreward,всеforecastpages,reasonreply,confirm/replay | active/revisionR,knownM/D_i | Pточнопоформуле,oneledger/rewardpositive,allcontributionsinresult,statuscancelled/noArise/no+1/top3/Lunar,egg/attempts сохранены; UI/history отражаютcancel |
| O01:P0/D0,500/501reason,чужойuser/topic,дваадмина,last-hitrace | active | отказбезmoney/status;authorizedzeroaudit;staleRтребуетновыйpreview;одинфиналR09илиcancel,необа |
| O02:Bобычный10%,Aконтр-геройперваяподтверждённаяатакаactual0 | counterinactive | Aуже100%доshield;counteractive/source/auditодинраз;Bдальше100%,restart/dayнеreset;failedSQLнеактивирует |
| O05:MoonEclipse1–6stars | originalM | max/current+floor(M*p/100)once,savedp/gain/stars,originalR09F/O01P |
| O06:Star+дваeligible,разныеRNGoutcomes,adminnohit/timeout,forbiddenends | noStarrecords | отдельныеdecisions/amount300persuccess,oneledger,notshared;noadmin/banish/loss/Raid/PvP |
| O07:Golden/Thief/Blessing+Star | standardrewards | standardonlymodifiers,independentStarunchanged;Blessing5mythic+successfulStar300ordinary;Towerfloor5chestonce,firstdrawnorerollafterloss/heroswitch/restart |
| O08:starter/buyask/cancel/confirm/replay/all8/no9th | active+inventory | costs75once,newinventorydoesnotswapactive;manualreservefreezeclocks/mood/CD/protection;activate resumes;growth/cares/equip5/day/adultclaim1/day |
| O10:3standard+Ice+Lunar+SoulSeal/Sun,zero/extra/Shadow | savedsnapshot | cancelbeforeIcebeforeLunarbeforestandard;oneconsumption,eventclamp,nozeroonhit/Arise,extraallowednorecursion,explicitvalidtargetonly |
| R01–R20ordinarycompatibility/Stories | olddeadlines,oldbalances,realD&D/Mini | 900newonly,olddeadlinespreserved,10started/day/Moscow,adminskipnotimeout,history/scopes/PvPnoMythic,Storyrealrows/DAG/immutablevotes/replay/restart |
| Pending/sending/sent/uncertain/networkrestart | durablepublication | pendingone-send,uncertainnoautomaticresend,noeconomyrepeat,correctworld_id |

SQLtestevidence: integrity_check=ok,foreign_key_check=[];mini_star_rewards,cancel_requests,raid_audit/rewards/damage/ledger/result,petdays,Storyvotes/steps,normalbossaudit/timeline. Сохранитьконкретныедо/послезначения; данныйплан сам по себе неявляетсяPASS. Проверкаподписей/45реальныхизображений/живыхcallbackбудетпослеконтента.

## Дополнительная будущая приёмка четырёх рейдовых механик — NOT RUN

После реальных Boss records: A/B чередуют любые подтверждённые атаки, включая нулевые.
C сверяет armour10/5 и flight8/2 по attack ledger, Sun incoming suppression на15/30,
HP±1 и переход нескольких порогов с оригинальной базой, отсутствие возврата фаз после Eclipse.
Проверить фото/счётчики/остатки, двойной callback/restart, отдельный Earth-вклад, O01/R09.
Синтетические автоматические PASS не заменяют эту будущую живую приёмку.


Stories V2 — после отдельного разрешения: отдельные bot token/SQLite/STORIES_DIR,
два настоящих Telegram игрока, семь авторских концовок, route21→14 hidden,
39↔40 возврат hidden, исходные индексы после фильтрации, последняя гонка голосов,
переголосование/старый callback/чужая тема, kick/cancel, рестарт до/после commit и
неопределённой доставки. Сверить story_steps без дублей node_id, снимки JSON и
неизменность кошельков. Не загружать пакет из Git прямо в рабочий серверный каталог.

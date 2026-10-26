# Контент V1.5: текущая поставка и оставшиеся поля

Состояние после технического прохода 26.10.2026 от HEAD 9ef98aa.

| Компонент | Результат |
|---|---|
| PNG_PASS | Все 45 реальных PNG присутствуют и проходят отдельную проверку, независимо от карточек |
| S5_IMPORTED | 17/17 импортированы в common/uncommon/rare/legendary (5/6/4/2); повторный apply — no-op |
| MYTHIC_RECORDS_READY | BLOCKED_BY_INPUT: нет авторских полей восьми героев |
| RAID_RECORDS_READY | BLOCKED_BY_INPUT: для четырёх записей нужны code/name |
| STORIES_READY | BLOCKED_BY_INPUT: нет настоящих авторских сюжетов |
| STRICT_CONTENT_PASS | BLOCKED_BY_INPUT; диагностический exit 0 не означает приёмку, strict exit 2 |

## Графика

17 белых S5: каждый RGB-канал 252–255, alpha 255. Для 24 Mythic и четырёх Raid:
каждый RGB-канал 0–4, alpha 255, только четыре угла. RGB 5 или alpha 254 отклоняются.
Оттенки вне углов не ограничиваются; художественная композиция остаётся авторской.
PNG signature, CRC, квадратность, декодирование scanlines и прозрачность проверяются.
У Raid цвет проверяется также через catalog.validate(require_images=True) и strict-content.
Прочие изображения без явно указанного background не получают новый цветовой контракт.

45 SHA-256 до изменений зафиксированы в CONTENT_ASSET_SHA256.json; автоматический тест
сверяет реальные файлы с ним. Полный отчёт с RGBA/размерами и до/после хешами — в evidence.
Все байты изображений сохранены. Boss 1..4: app/mini/mythic/raid/images/raid_boss_1.png…4.png.
Mythic: app/mini/content/hero_images/{ice,lightning,dark,fire,star,earth,moon,sun}_{egg,young,adult}.png.
S5: точные image и code — S5_IMPORT_MANIFEST.json, игровые поля манифеста не изменяются.

## Безопасный S5 importer

`python tools/import_v15_s5.py --source docs/v1.5/S5_IMPORT_MANIFEST.json --images app/mini/content/hero_images`
— read-only preflight. Та же команда с --apply разрешена только в codex/v1.5.
Источник — именно утверждённый manifest. --images явно выбирает доверенный входной каталог;
читаются только PNG-basename манифеста, symlink/выход из каталога запрещён.
Существующий destination с равными байтами переиспользуется, в том числе source==destination;
его mtime/PNG не меняются. Различные байты — отказ до записи, даже если оба PNG валидны.
Полный идентичный набор записей — no-op; частичный/конфликтный — отказ, не автозамена.

Все JSON/PNG и общий каталог проверяются до записи. Lock исключает параллельный импорт;
все входы повторно сверяются с preflight. Новые файлы и backups fsync-ятся во временном
каталоге на целевой файловой системе; os.replace заменяет каждый файл целиком.
При ошибке выполненные замены откатываются. Это не межпроцессная транзакция всех файлов:
импорт выполняется только в feature-checkout без live bot. При аварии rollback backups
сохраняются с точным путём в ошибке, lock после kill требует проверки оператором.

Старые 54 записи сохранены; текущий итог — 71, из них 17 новых S5. Гача продолжает
использовать только common/uncommon/rare/legendary; Mythic/Shadow исключены. Веса, pity,
формула дубликатов, ранги, оборудование и legacy fragments не меняются.

## Один пакет оставшихся авторских данных

- MYTHIC_AUTHOR_INPUT_REQUIRED.md: восемь строк и ready-to-complete JSON; известные
  attack/image/элемент/normal+raid свойства заполнены, отсутствующие поля явно null.
- RAID_BOSSES_AUTHOR_INPUT_REQUIRED.md: четыре записи с точными SPECS/порядком PNG;
  нужны только code/name, Shadow=false. Ни одного placeholder-кода в игровом каталоге.
- STORY_AUTHOR_TEMPLATE.json: TEMPLATE / NOT PRODUCTION. STORIES_IMPORT_GUIDE.md содержит
  read-only CLI и внешний тестовый путь. Шаблон не копируется в рабочий STORIES_DIR.

Mythic каталог остаётся пустым; Boss каталог корректно отсутствует. Интеграционные fixtures
доказывают механику, но не заменяют авторскую поставку и строгую приёмку.

До следующей приёмки нужны весь настоящий контент, независимое финальное ревью,
production DB copy и реальный staging. Сейчас рабочая SQLite, main и серверы не затронуты.
Вердикт: **Релиз заблокирован**.

Предыдущий checklist со всеми историческими фактами сохранён без изменений в
CONTENT_IMPORT_CHECKLIST_BEFORE_FINALIZATION.md; его старые слова об отсутствии PNG
или ещё не утверждённом чёрном допуске описывают прежнее состояние.

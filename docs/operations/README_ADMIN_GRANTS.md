# Управление D&D и Mini в V1.3

Обычный D&D: `/create`, `/char`, `/charset`, `/lvlup`, `/inv @user`, `/inv all`.
Remote admin: `/admadd CHAT:THEME @user предмет`, `/admdel CHAT:THEME @user предмет`,
`/admdel CHAT:THEME @user ALL`, `/adminv CHAT:THEME @user`, `/admcharset`.
Target Mini-тема отклоняется до обращения к D&D данным.
Команды `/add`, `/del`, `/clean`, `/admclean` удалены.

Единственный Mini superadmin — владелец @arukozento, проверяемый только по numeric ID.
В `app/mini/superadmin/access.py` закреплён подтверждённый владельцем
`SUPERADMIN_USER_ID = 694384548`. Username и обычные topic-admin права доступа
не дают. Передать роль командой нельзя.

```text
/superchars -1003376315265:2684
/superlook -1003376315265:2684 @user
/superlook -1003376315265:2684 123456789 -c -s
/superadd -1003376315265:2684 @user -c 500
/superadd -1003376315265:2684 @user -s 100
/superadd -1003376315265:2684 @user -p Villager
/superadd -1003376315265:2684 @user -i summon_ticket
/superadd -1003376315265:2684 @user -i eq_helmet_020
/superdel -1003376315265:2684 @user -i eq_helmet_020
/superluck -1003376315265:2684 @user
```

Scope строго `chatid:themeid`; target `@username` или numeric Telegram ID.
Отсутствие флагов у superlook показывает всё; мутация принимает один флаг и значение.
Coins/shards не уходят ниже нуля. Повторная hero grant не создаёт duplicate shards.
Удаление используемого в активной Tower/Boss героя отклоняется. Удаление последнего
экземпляра экипированного предмета атомарно очищает слот.
Каждая superadd/superdel/superluck операция и audit фиксируются одной транзакцией.
Повтор Telegram message ID не выполняет экономическую операцию ещё раз.

`/superluck` сохраняет одну гарантию следующего успешного Legendary pull.
Повтор не накапливает гарантии. Failed pull, меню и restart её не расходуют.
Следующий успешный coin/ticket pull проходит обычное разрешение нового/duplicate героя
и снимает гарантию в той же транзакции, что платёж и pull record.

`/admitems` остаётся read-only каталогом usable Mini items. Equipment codes:
`eq_helmet_001..100`, `eq_ring_001..100`, `eq_cloak_001..100`;
имена и бонусы в `app/mini/equipment/items.json`. Mini через обычный `/admadd` не меняется.

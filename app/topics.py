# Конфигурация персонажей, администраторов и специальных тем Telegram.
#
# Формат:
# CHAT_ID -> TOPIC_ID -> admin / characters / mini
#
# Для обычного чата без тем TOPIC_ID = 0.

TOPIC_SETTINGS = {
    -1003376315265: {
        4: {
            "admin": "@arukozento",
            "characters": {
                "@arukozento": "Мастер",
                "@remark1997": "Фредо",
                "@favion_nikita": "Лазарь",
                "@sivakozov": "Громм",
                "@valeriya_2304": "Азраэль",
                "@daniil_savenko": "Ренкай",
                "@daria_osta": "Марфа",
            },
        },
        3: {
            "admin": "@favion_nikita",
            "characters": {
                "@arukozento": "Олаф",
                "@favion_nikita": "Мастер",
                "@sivakozov": "Ульф",
                "@valeriya_2304": "Дейнерис",
                "@daniil_savenko": "Кайден",
                "@kovesha_lu": "Брунгильда",
                "@romorosnya": "Зая",
            },
        },
        2684: {
            "admin": "@arukozento",
            "characters": {},
            "mini": True,
            "mini_name": "D&D Mini",
        },
    },
    694384548: {
        0: {
            "admin": "@arukozento",
            "characters": {
                "@arukozento": "Мастер",
            },
        },
    },
}

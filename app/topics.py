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
            "shop_currency_units": {
                "master": {
                    "Медная монета": "dnd_ab285f702d8f305754c900e0f7578f156d910641d9d87f7729aec6bf763b49d3",
                    "Серебряная монета": "dnd_77d108b86e67cf12cdf342a2d45e8d4aa09128c12c5d69e01e303fc40e67aeed",
                    "Золотая монета": "dnd_0f211e6c8eddfdfd520d5a0f85b6b3ac2f098018c39fba530133518575fee8b9",
                },
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

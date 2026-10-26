# Mythic: точный запрос авторских полей

BLOCKED_BY_INPUT. В доступной ветке, Downloads и вложениях нет восьми авторских карточек.
`app/mini/content/heroes/mythic.json` остаётся пустым; 24 настоящих PNG уже предоставлены.
Ниже данные для заполнения, **не игровой каталог**. `null` означает ровно отсутствующее
авторское значение, а не допустимое значение для импорта. Не импортировать этот шаблон.

| Стихия | Базовый ATK | Изображение | normal.key = raid.key | Отсутствует |
|---|---:|---|---|---|
| ice | 1 | `ice_adult.png` | `mythic_ice` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |
| lightning | 2 | `lightning_adult.png` | `mythic_lightning` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |
| dark | 2 | `dark_adult.png` | `mythic_dark` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |
| fire | 3 | `fire_adult.png` | `mythic_fire` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |
| star | 2 | `star_adult.png` | `mythic_star` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |
| earth | 3 | `earth_adult.png` | `mythic_earth` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |
| moon | 1 | `moon_adult.png` | `mythic_moon` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |
| sun | 1 | `sun_adult.png` | `mythic_sun` | `code`, `name`, `race`, `class_name`, `faction`, `class_tag`, `damage_type`, `attack_range`, `special_trait`, `active`, `description`, `passive_text` |

`rarity=mythic`, `mythic_element`, `passive_key`, обе независимые passives, attack/image известны.
Нужны уникальные code/name; `active` — bool, остальные отсутствующие поля — непустые строки.
Допустимые faction: commoners, beasts, monsters, warriors, dark, neutral;
damage_type: slashing, piercing, bludgeoning, magic; attack_range: melee, ranged.
class_tag/special_trait — открытые непустые теги; действующие боевые теги см. app/mini/combat/tags.py.
Не заполнять биографии, класс или фракцию на основании внешнего вида арта.

Готовый комплект проходит основной load_hero_catalog, ability registry и validate_mythics.
Stars 1..6, стоимость повышения 20/35/55/80/110, базовый ATK не растёт; Mythic не входят
в обычную гачу. Без реального hero code взросление сохраняет evolution_pending и не
создаёт вымышленную запись. Старые персональные fragments не конвертируются.

```json
{
  "heroes": [
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "ice",
      "attack": 1,
      "image": "ice_adult.png",
      "passive_key": "mythic_ice",
      "passives": {
        "normal": {
          "key": "mythic_ice"
        },
        "raid": {
          "key": "mythic_ice"
        }
      }
    },
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "lightning",
      "attack": 2,
      "image": "lightning_adult.png",
      "passive_key": "mythic_lightning",
      "passives": {
        "normal": {
          "key": "mythic_lightning"
        },
        "raid": {
          "key": "mythic_lightning"
        }
      }
    },
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "dark",
      "attack": 2,
      "image": "dark_adult.png",
      "passive_key": "mythic_dark",
      "passives": {
        "normal": {
          "key": "mythic_dark"
        },
        "raid": {
          "key": "mythic_dark"
        }
      }
    },
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "fire",
      "attack": 3,
      "image": "fire_adult.png",
      "passive_key": "mythic_fire",
      "passives": {
        "normal": {
          "key": "mythic_fire"
        },
        "raid": {
          "key": "mythic_fire"
        }
      }
    },
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "star",
      "attack": 2,
      "image": "star_adult.png",
      "passive_key": "mythic_star",
      "passives": {
        "normal": {
          "key": "mythic_star"
        },
        "raid": {
          "key": "mythic_star"
        }
      }
    },
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "earth",
      "attack": 3,
      "image": "earth_adult.png",
      "passive_key": "mythic_earth",
      "passives": {
        "normal": {
          "key": "mythic_earth"
        },
        "raid": {
          "key": "mythic_earth"
        }
      }
    },
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "moon",
      "attack": 1,
      "image": "moon_adult.png",
      "passive_key": "mythic_moon",
      "passives": {
        "normal": {
          "key": "mythic_moon"
        },
        "raid": {
          "key": "mythic_moon"
        }
      }
    },
    {
      "code": null,
      "name": null,
      "race": null,
      "class_name": null,
      "faction": null,
      "class_tag": null,
      "damage_type": null,
      "attack_range": null,
      "special_trait": null,
      "active": null,
      "description": null,
      "passive_text": null,
      "rarity": "mythic",
      "mythic_element": "sun",
      "attack": 1,
      "image": "sun_adult.png",
      "passive_key": "mythic_sun",
      "passives": {
        "normal": {
          "key": "mythic_sun"
        },
        "raid": {
          "key": "mythic_sun"
        }
      }
    }
  ]
}
```

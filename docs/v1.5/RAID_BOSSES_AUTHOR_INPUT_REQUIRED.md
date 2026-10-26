# Рейдовые боссы: точный запрос авторских полей

BLOCKED_BY_INPUT. Для каждой из четырёх позиций отсутствуют **только code и name**.
Коды должны быть уникальными непустыми строками, имена — авторскими непустыми строками.
Порядок соответствует четырём предоставленным изображениям; PNG не изменяются.
`raid_boss_1` и т. п. — basename арта, их нельзя автоматически превращать в code/name.

| № | Изображение | HP | Стихия / контрстихия | Механика | Отсутствует |
|---|---|---:|---|---|---|
| 1 | `raid_boss_1.png` | 100000 | fire / ice | `armor_cycle` | code, name |
| 2 | `raid_boss_2.png` | 115000 | lightning / star | `flight_cycle` | code, name |
| 3 | `raid_boss_3.png` | 130000 | dark / sun | `periodic_heal` | code, name |
| 4 | `raid_boss_4.png` | 150000 | star / moon | `hp_defense_phases` | code, name |

Ниже ready-to-complete контракт вне активного каталога. `null` — место для авторских
значений; такой JSON нельзя загружать в игру. Либо четыре полные реальные записи в
app/mini/mythic/raid/bosses.json, либо корректно пустой/отсутствующий каталог.
Shadow-цель не нужна: утверждено shadow_extractable=false. Авторский лор можно прислать
отдельно, он не является обязательным полем этой схемы. Не добавляйте неизвестный
`description` в текущий JSON: catalog.validate отклоняет неизвестные поля.

```json
{
  "schema_version": 1,
  "bosses": [
    {
      "code": null,
      "name": null,
      "image": "raid_boss_1.png",
      "max_hp": 100000,
      "element": "fire",
      "counter_elements": [
        "ice"
      ],
      "features": [
        "armored"
      ],
      "regeneration_percent": 0,
      "shadow_extractable": false,
      "counter_policy": "first_confirmed_counter_hero_attack_on_this_raid",
      "mechanic": {
        "type": "armor_cycle",
        "armored_attacks": 10,
        "exposed_attacks": 5
      }
    },
    {
      "code": null,
      "name": null,
      "image": "raid_boss_2.png",
      "max_hp": 115000,
      "element": "lightning",
      "counter_elements": [
        "star"
      ],
      "features": [
        "flying"
      ],
      "regeneration_percent": 0,
      "shadow_extractable": false,
      "counter_policy": "first_confirmed_counter_hero_attack_on_this_raid",
      "mechanic": {
        "type": "flight_cycle",
        "flying_attacks": 8,
        "grounded_attacks": 2
      }
    },
    {
      "code": null,
      "name": null,
      "image": "raid_boss_3.png",
      "max_hp": 130000,
      "element": "dark",
      "counter_elements": [
        "sun"
      ],
      "features": [],
      "regeneration_percent": 0,
      "shadow_extractable": false,
      "counter_policy": "first_confirmed_counter_hero_attack_on_this_raid",
      "mechanic": {
        "type": "periodic_heal",
        "every_attacks": 15,
        "heal_bps_of_original_hp": 25,
        "sun_suppresses": true
      }
    },
    {
      "code": null,
      "name": null,
      "image": "raid_boss_4.png",
      "max_hp": 150000,
      "element": "star",
      "counter_elements": [
        "moon"
      ],
      "features": [
        "armored",
        "flying"
      ],
      "regeneration_percent": 0,
      "shadow_extractable": false,
      "counter_policy": "first_confirmed_counter_hero_attack_on_this_raid",
      "mechanic": {
        "type": "hp_defense_phases",
        "threshold_percent": [
          75,
          50,
          25
        ],
        "phase_features": [
          [
            "armored",
            "flying"
          ],
          [
            "armored"
          ],
          [
            "flying"
          ],
          []
        ],
        "threshold_base": "original_max_hp",
        "monotonic": true
      }
    }
  ]
}
```

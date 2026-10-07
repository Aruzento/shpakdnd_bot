import copy
import unittest
from app.mini.tower.catalog import load_catalog,validate_catalog
from app.mini.equipment.catalog import load_catalog as equipment,validate_catalog as validate_equipment
from app.mini.tower.balance import load_balance,reward_band


class ContentTests(unittest.TestCase):
    def test_exact_200_increasing_single_enemy_records(self):
        floors=load_catalog()['floors']
        self.assertEqual([f['floor'] for f in floors],list(range(1,201)))
        self.assertEqual(len({f['code'] for f in floors}),200)
        self.assertTrue(all(f['max_hp']>0 for f in floors))
        self.assertTrue(all(a['max_hp']<b['max_hp'] for a,b in zip(floors,floors[1:])))
        self.assertTrue(all('enemies' not in f and 'waves' not in f for f in floors))
        self.assertEqual(floors[0]['max_hp'],6)
        self.assertEqual(floors[-1]['max_hp'],3005)

    def test_equipment_counts_bonuses_and_unique_names(self):
        items=equipment()['items']
        self.assertEqual(len(items),300)
        self.assertEqual(len({x['code'] for x in items}),300)
        self.assertEqual(len({x['name'] for x in items}),300)
        for slot in ('helmet','ring','cloak'):
            self.assertEqual(sorted(x['attack_bonus'] for x in items if x['slot']==slot),list(range(1,101)))

    def test_tower_invalid_values_rejected(self):
        changes={'floor':0,'code':'','max_hp':0,'faction':'bogus','damage_type':'bogus','class_tag':'bogus',
                 'attack_range':'bogus','special_trait':'bogus','features':['bogus'],'response':'bogus'}
        for key,value in changes.items():
            with self.subTest(key=key):
                data=copy.deepcopy(load_catalog());data['floors'][0][key]=value
                with self.assertRaises(ValueError):validate_catalog(data)

    def test_tower_duplicates_and_nonprogression_rejected(self):
        for key in ('floor','code','max_hp'):
            data=copy.deepcopy(load_catalog());data['floors'][1][key]=data['floors'][0][key]
            with self.subTest(key=key),self.assertRaises(ValueError):validate_catalog(data)
        data=copy.deepcopy(load_catalog());data['floors'].pop()
        with self.assertRaises(ValueError):validate_catalog(data)
        data=copy.deepcopy(load_catalog());data['floors'][0]['enemies']=[]
        with self.assertRaises(ValueError):validate_catalog(data)

    def test_equipment_invalid_values_rejected(self):
        for key,value in [('slot','boots'),('attack_bonus',0),('attack_bonus',101),('attack_bonus',True),('code',''),('name','')]:
            data=copy.deepcopy(equipment());data['items'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate_equipment(data)

    def test_equipment_duplicates_and_extra_stats_rejected(self):
        for key in ('code','name','attack_bonus'):
            data=copy.deepcopy(equipment());data['items'][1][key]=data['items'][0][key]
            with self.subTest(key=key),self.assertRaises(ValueError):validate_equipment(data)
        data=copy.deepcopy(equipment());data['items'][0]['rarity']='mythic'
        with self.assertRaises(ValueError):validate_equipment(data)
        data=copy.deepcopy(equipment());data['items'].pop()
        with self.assertRaises(ValueError):validate_equipment(data)

    def test_reward_bands_cover_all_floors_and_40_milestones(self):
        self.assertEqual(len(load_balance()['bands']),8)
        self.assertEqual(sum(f%5==0 for f in range(1,201)),40)
        for f in range(1,201): self.assertGreater(reward_band(f)['shards'],0)
        self.assertEqual(reward_band(5)['max_bonus'],12)
        self.assertEqual(reward_band(200)['max_bonus'],100)

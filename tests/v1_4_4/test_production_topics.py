"""Only the explicitly reviewed three-coin declaration may change the baseline topics."""
import ast
import copy
from pathlib import Path
import subprocess
import unittest

from app.topics import TOPIC_SETTINGS

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '94669086821fcb41c8349748a30724e24f88d893'
SCOPE = (-1003376315265, 4)
EXPECTED = {
    'Медная монета': 'dnd_ab285f702d8f305754c900e0f7578f156d910641d9d87f7729aec6bf763b49d3',
    'Серебряная монета': 'dnd_77d108b86e67cf12cdf342a2d45e8d4aa09128c12c5d69e01e303fc40e67aeed',
    'Золотая монета': 'dnd_0f211e6c8eddfdfd520d5a0f85b6b3ac2f098018c39fba530133518575fee8b9',
}


class ProductionTopicCurrencyTests(unittest.TestCase):
    def test_topic_four_declares_only_three_distinct_physical_codes(self):
        declaration = TOPIC_SETTINGS[SCOPE[0]][SCOPE[1]]['shop_currency_units']
        self.assertEqual(declaration, {'master': EXPECTED})
        self.assertEqual(len(set(declaration['master'].values())), 3)

    def test_other_topic_data_matches_tested_baseline_exactly(self):
        raw = subprocess.check_output(
            ['git', 'show', BASELINE + ':app/topics.py'], cwd=ROOT
        ).decode('utf-8')
        tree = ast.parse(raw)
        assignment = next(n for n in tree.body if isinstance(n, ast.Assign))
        baseline = ast.literal_eval(assignment.value)
        current = copy.deepcopy(TOPIC_SETTINGS)
        del current[SCOPE[0]][SCOPE[1]]['shop_currency_units']
        self.assertEqual(current, baseline)
        self.assertNotIn('shop_currency_units', TOPIC_SETTINGS[SCOPE[0]][3])
        self.assertNotIn('shop_currency_units', TOPIC_SETTINGS[SCOPE[0]][2684])
        self.assertNotIn(-5320347021, TOPIC_SETTINGS)


if __name__ == '__main__':
    unittest.main()

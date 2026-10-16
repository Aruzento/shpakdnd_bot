import itertools
import random
import unittest
from fractions import Fraction
from unittest.mock import patch

from app.dnd_shop.money import CalculationLimit, MoneyError, exact_text, payment


class MoneyTests(unittest.TestCase):
    def test_base_secondary_mixed_and_minimum_coins(self):
        for price, denominations, holdings, expected in [
            (5, {1: Fraction(1)}, {1: 8}, ({1: 5}, {})),
            (1, {1: Fraction(1), 2: Fraction(1, 10)}, {2: 10}, ({2: 10}, {})),
            (Fraction(3, 2), {1: Fraction(1), 2: Fraction(1, 2)}, {1: 1, 2: 1}, ({1: 1, 2: 1}, {})),
            (6, {1: Fraction(4), 2: Fraction(3), 3: Fraction(1)}, {1: 1, 2: 2, 3: 6}, ({2: 2}, {})),
        ]:
            with self.subTest(price=price):
                self.assertEqual(payment(Fraction(price), denominations, holdings), expected)

    def test_exact_payment_precedes_overpayment(self):
        self.assertEqual(payment(Fraction(6), {1: Fraction(7), 2: Fraction(1)}, {1: 1, 2: 6}), ({2: 6}, {}))

    def test_change_noncanonical_and_minimal_overpayment(self):
        self.assertEqual(payment(Fraction(6), {1: Fraction(4), 2: Fraction(3), 3: Fraction(1)}, {1: 2}), ({1: 2}, {3: 2}))
        self.assertEqual(payment(Fraction(6), {1: Fraction(8), 2: Fraction(7), 3: Fraction(1)}, {1: 1, 2: 1}),
                         ({2: 1}, {3: 1}))

    def test_small_exact_purchase_with_sqlite_maximum_wallet(self):
        self.assertEqual(payment(Fraction(1), {1: Fraction(1)}, {1: 2**63 - 1}), ({1: 1}, {}))

    def test_decimal_change_example(self):
        denominations = {1: Fraction(1), 2: Fraction(1, 10), 3: Fraction(1, 100)}
        self.assertEqual(payment(Fraction('1.25'), denominations, {1: 2}), ({1: 2}, {2: 7, 3: 5}))

    def test_repeating_denomination_and_payout(self):
        self.assertEqual(payment(Fraction(1), {1: Fraction(1, 3)}, {1: 3}), ({1: 3}, {}))
        self.assertEqual(payment(Fraction(1, 3), {1: Fraction(1, 3)}, {}, payout=True), ({}, {1: 1}))

    def test_no_change_insufficient_and_exact_payout_impossible(self):
        for args in [
            (Fraction(3), {1: Fraction(2)}, {1: 2}),
            (Fraction(3), {1: Fraction(1)}, {1: 2}),
        ]:
            with self.assertRaises(MoneyError):
                payment(*args)
        with self.assertRaisesRegex(MoneyError, 'Не найду столько мелочи'):
            payment(Fraction('0.03'), {1: Fraction(1)}, {}, payout=True)

    def test_limits_are_not_reported_as_impossibility(self):
        with patch('app.dnd_shop.money.MAX_WORK', 1), self.assertRaises(CalculationLimit):
            payment(Fraction(2), {1: Fraction(1)}, {1: 10})
        with self.assertRaises(CalculationLimit):
            payment(Fraction(1, 2**150), {1: Fraction(1)}, {}, payout=True)

    def test_exact_formatting(self):
        for value, expected in [('1.25', '1.25'), ('0.03', '0.03'), ('100', '100'), ('1/3', '1/3')]:
            self.assertEqual(exact_text(Fraction(value)), expected)

    def test_against_exhaustive_small_wallet_oracle(self):
        rng = random.Random(144)
        for _ in range(100):
            coins = [rng.randint(1, 8) for _ in range(3)]
            counts = [rng.randint(0, 3) for _ in coins]
            target = rng.randint(1, 15)
            options = []
            for vector in itertools.product(*(range(n + 1) for n in counts)):
                total = sum(n * coin for n, coin in zip(vector, coins))
                if total < target:
                    continue
                change = total - target
                payouts = [v for v in itertools.product(range(change + 1), repeat=3)
                           if sum(n * coin for n, coin in zip(v, coins)) == change]
                if payouts:
                    payout = min(payouts, key=lambda v: (sum(v), v))
                    options.append((change, sum(vector) + sum(payout), vector, payout))
            denominations = {i: Fraction(coin) for i, coin in enumerate(coins)}
            holdings = dict(enumerate(counts))
            if not options:
                with self.assertRaises(MoneyError):
                    payment(Fraction(target), denominations, holdings)
            else:
                _, _, debit, credit = min(options)
                actual = payment(Fraction(target), denominations, holdings)
                self.assertEqual(actual, ({i: n for i, n in enumerate(debit) if n}, {i: n for i, n in enumerate(credit) if n}))

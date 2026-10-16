"""Exact bounded payment / unbounded change with explicit resource limits."""
from fractions import Fraction
from math import gcd, lcm

MAX_STATES = 200_000
MAX_WORK = 2_000_000


class MoneyError(ValueError):
    pass


class CalculationLimit(MoneyError):
    pass


def integer_units(values):
    scale = 1
    for value in values:
        scale = lcm(scale, value.denominator)
        if scale.bit_length() > 128:
            raise CalculationLimit('Слишком сложные номиналы: превышен предел расчёта.')
    units = [int(value * scale) for value in values]
    common = gcd(*units)
    return [value // common for value in units]


def payment(price, denominations, holdings, *, payout=False):
    """Return (debits, credits) indexed by sorted registry IDs, never floats.

    Enumerate bounded subset sums using binary decomposition. Each sum retains
    the fewest coins and a deterministic vector. Unbounded DP gives optimal
    change; the first feasible payment minimizes overpayment.
    """
    ids = sorted(denominations)
    if not ids or price <= 0 or any(denominations[i] <= 0 for i in ids):
        raise MoneyError('Нет разрешённых номиналов для расчёта.')
    values = integer_units([Fraction(price)] + [Fraction(denominations[i]) for i in ids])
    target, coins = values[0], values[1:]
    zero = (0,) * len(ids)
    work = 0

    def budget(amount=1):
        nonlocal work
        work += amount
        if work > MAX_WORK:
            raise CalculationLimit('Превышен безопасный предел расчёта размена. Попробуйте другой состав монет.')

    def rank(vector):
        return sum(vector), vector

    changes = [zero]
    def change(amount):
        if amount > MAX_STATES:
            raise CalculationLimit('Превышен безопасный предел расчёта сдачи.')
        while len(changes) <= amount:
            value = len(changes)
            best = None
            for index, coin in enumerate(coins):
                budget()
                if coin <= value and changes[value - coin] is not None:
                    vector = list(changes[value - coin])
                    vector[index] += 1
                    vector = tuple(vector)
                    if best is None or rank(vector) < rank(best):
                        best = vector
            changes.append(best)
        return changes[amount]

    def as_dict(vector):
        return {item_id: qty for item_id, qty in zip(ids, vector) if qty}

    if payout:
        result = change(target)
        if result is None:
            raise MoneyError('Торговец: «Не найду столько мелочи. Не могу рассчитаться!»')
        return {}, as_dict(result)
    counts = [holdings.get(item_id, 0) for item_id in ids]
    if any(type(qty) is not int or qty < 0 for qty in counts):
        raise MoneyError('Некорректное количество монет в инвентаре.')
    if sum(c * n for c, n in zip(coins, counts)) < target:
        raise MoneyError('Недостаточно средств.')
    reachable = {0: zero}
    for index, count in enumerate(counts):
        group = 1
        while count:
            take = min(group, count)
            delta = coins[index] * take
            for amount, previous in list(reachable.items()):
                budget()
                vector = list(previous)
                vector[index] += take
                vector = tuple(vector)
                total = amount + delta
                existing = reachable.get(total)
                if existing is None or rank(vector) < rank(existing):
                    reachable[total] = vector
            if len(reachable) > MAX_STATES:
                raise CalculationLimit('Превышен безопасный предел расчёта оплаты.')
            count -= take
            group *= 2
    for total in sorted(amount for amount in reachable if amount >= target):
        result = change(total - target)
        if result is not None:
            return as_dict(reachable[total]), as_dict(result)
    raise MoneyError('Невозможно точно оплатить товар и выдать сдачу существующими монетами.')


def exact_text(value):
    value = Fraction(value)
    denominator = value.denominator
    for prime in (2, 5):
        while denominator % prime == 0:
            denominator //= prime
    if denominator != 1:
        return f'{value.numerator}/{value.denominator}'
    digits = 0
    scale = 1
    while scale % value.denominator:
        scale *= 10
        digits += 1
    number = value.numerator * (scale // value.denominator)
    if not digits:
        return str(number)
    text = str(number).zfill(digits + 1)
    return (text[:-digits] + '.' + text[-digits:]).rstrip('0').rstrip('.')

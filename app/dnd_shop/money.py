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


def payment(price, denominations, holdings, *, payout=False, _counter=None, change_denominations=None):
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
    output = denominations if change_denominations is None else change_denominations
    if not output or any(i not in denominations or denominations[i]!=v for i,v in output.items()):
        raise MoneyError('Несогласованные номиналы выдачи.')
    out_ids = sorted(output)
    out_coins = [coins[ids.index(i)] for i in out_ids]
    zero = (0,) * len(ids)
    counter = [0] if _counter is None else _counter

    def budget(amount=1):
        counter[0] += amount
        if counter[0] > MAX_WORK:
            raise CalculationLimit('Превышен безопасный предел расчёта размена. Попробуйте другой состав монет.')

    def rank(vector):
        return sum(vector), vector

    # For exactly three distinct divisibility-chain coins, the bounded ceil
    # problem has two branches per level. This is not used for arbitrary coins.
    if len(ids)==3 and len(set(coins))==3 and ids==out_ids:
        order=sorted(range(3),key=lambda i:coins[i],reverse=True)
        if all(coins[order[i]] % coins[order[i+1]]==0 for i in (0,1)):
            budget(3)
            def unbounded(amount):
                vector=[0]*3
                for i in order:
                    vector[i],amount=divmod(amount,coins[i])
                return tuple(vector) if amount==0 else None
            if payout:
                vector=unbounded(target)
                if vector is None:
                    raise MoneyError('Торговец: «Не найду столько мелочи. Не могу рассчитаться!»')
                return {},{i:n for i,n in zip(ids,vector) if n}
            counts=[holdings.get(i,0) for i in ids]
            if any(type(n) is not int or n<0 for n in counts):
                raise MoneyError('Некорректное количество монет в инвентаре.')
            if sum(c*n for c,n in zip(coins,counts))<target:
                raise MoneyError('Недостаточно средств.')
            def bounded_ceil(needed,depth):
                if needed<=0:
                    return [(0,(0,)*3)]
                if depth==3:
                    return []
                i=order[depth]
                floor=min(counts[i],needed//coins[i])
                ceil=min(counts[i],-(-needed//coins[i]))
                options=[]
                for count in sorted({floor,ceil}):
                    for total,tail in bounded_ceil(needed-count*coins[i],depth+1):
                        vector=list(tail)
                        vector[i]=count
                        options.append((total+count*coins[i],tuple(vector)))
                return options
            candidates=[]
            for total,vector in bounded_ceil(target,0):
                change_vector=unbounded(total-target)
                if change_vector is not None:
                    candidates.append((total-target,sum(vector)+sum(change_vector),vector,change_vector))
            if not candidates:
                raise MoneyError('Невозможно точно оплатить товар и выдать сдачу существующими монетами.')
            _,_,debit_vector,credit_vector=min(candidates)
            return ({i:n for i,n in zip(ids,debit_vector) if n},
                    {i:n for i,n in zip(ids,credit_vector) if n})
    changes = [(0,)*len(out_ids)]
    def change(amount):
        if amount > MAX_STATES:
            return large_change(amount, out_coins, out_ids, _counter=counter)
        while len(changes) <= amount:
            value = len(changes)
            best = None
            for index, coin in enumerate(out_coins):
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
        return {}, {i:n for i,n in zip(out_ids,result) if n}
    counts = [holdings.get(item_id, 0) for item_id in ids]
    if any(type(qty) is not int or qty < 0 for qty in counts):
        raise MoneyError('Некорректное количество монет в инвентаре.')
    if sum(c * n for c, n in zip(coins, counts)) < target:
        raise MoneyError('Недостаточно средств.')
    def bounded(cap=None):
        reachable = {0: zero}
        for index, available in enumerate(counts):
            count = available if cap is None else min(available, cap // coins[index])
            group = 1
            while count:
                take = min(group, count)
                delta = coins[index] * take
                for amount, previous in list(reachable.items()):
                    budget()
                    total = amount + delta
                    if cap is not None and total > cap:
                        continue
                    vector = list(previous)
                    vector[index] += take
                    vector = tuple(vector)
                    existing = reachable.get(total)
                    if existing is None or rank(vector) < rank(existing):
                        reachable[total] = vector
                if len(reachable) > MAX_STATES:
                    raise CalculationLimit('Превышен безопасный предел расчёта оплаты.')
                count -= take
                group *= 2
        return reachable

    # Exact payment has priority. Bound its search by the price so a wealthy
    # wallet does not make a tiny exact purchase exhaust the resource budget.
    # A quotient in the largest denomination reaches the absolute coin-count
    # lower bound, so it is safe to bypass DP even for very large catalogs.
    maximum = max(coins)
    for index in reversed(range(len(coins))) if target > MAX_STATES else ():
        coin = coins[index]
        if coin == maximum and target % coin == 0 and counts[index] >= target // coin:
            return {ids[index]: target // coin}, {}
    exact = bounded(target).get(target)
    if exact is not None:
        return as_dict(exact), {}
    reachable = bounded()
    for total in sorted(amount for amount in reachable if amount >= target):
        result = change(total - target)
        if result is not None:
            return as_dict(reachable[total]), {i:n for i,n in zip(out_ids,result) if n}
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


def large_change(target, coins, ids, *, _counter=None):
    """Min-coin unbounded change via shortest deficits modulo largest coin.

    For a fixed residue minimize M*count-value. If that witness fits target,
    filling with M coins is globally optimal. If it does not fit, signal the
    computational limit rather than claiming mathematical impossibility.
    """
    import heapq
    maximum = max(coins)
    if maximum > MAX_STATES:
        raise CalculationLimit('Превышен безопасный предел расчёта крупной выплаты.')
    largest = len(coins) - 1 - coins[::-1].index(maximum)
    zero = (0,) * len(ids)
    best = {0: (0, 0, zero)}
    heap = [(0, 0, 0)]
    counter = [0] if _counter is None else _counter
    while heap:
        loss, value, residue = heapq.heappop(heap)
        if best[residue][:2] != (loss, value):
            continue
        if residue == target % maximum:
            vector = list(best[residue][2])
            if value > target:
                raise CalculationLimit('Выплата требует расширенного расчёта.')
            vector[largest] += (target - value) // maximum
            return tuple(vector)
        for index, coin in enumerate(coins):
            if coin == maximum:
                continue
            counter[0] += 1
            if counter[0] > MAX_WORK:
                raise CalculationLimit('Превышен безопасный предел расчёта выплаты.')
            next_value = value + coin
            next_loss = loss + maximum - coin
            next_residue = next_value % maximum
            previous = best.get(next_residue)
            if previous is None or (next_loss, next_value) < previous[:2]:
                vector = list(best[residue][2])
                vector[index] += 1
                best[next_residue] = next_loss, next_value, tuple(vector)
                heapq.heappush(heap, (next_loss, next_value, next_residue))
    raise MoneyError('Торговец: «Не найду столько мелочи. Не могу рассчитаться!»')


def payment_with_credit(price, denominations, holdings, credit, *, change_denominations=None):
    """Use as much exact credit as feasible; conserve the complete obligation."""
    price, credit = Fraction(price), Fraction(credit)
    if credit < 0 or price <= 0:
        raise MoneyError('Некорректная цена или торговый остаток.')
    if credit >= price:
        return {}, {}, price
    if credit == 0:
        debit, change = payment(price, denominations, holdings, change_denominations=change_denominations)
        return debit, change, Fraction(0)
    if not denominations:
        raise MoneyError('Нет разрешённых номиналов для расчёта.')
    counts = [holdings.get(i, 0) for i in denominations]
    if any(type(qty) is not int or qty < 0 for qty in counts):
        raise MoneyError('Некорректное количество монет в инвентаре.')
    coin_total = sum((Fraction(v) * holdings.get(i, 0) for i, v in denominations.items()), Fraction(0))
    if coin_total + credit < price:
        raise MoneyError('Недостаточно средств.')
    # Every net coin amount lies on the exact denomination lattice.
    from math import lcm, gcd
    scale = 1
    for value in denominations.values():
        scale = lcm(scale, value.denominator)
        if scale.bit_length() > 128:
            raise CalculationLimit('Слишком сложные номиналы.')
    quantum = Fraction(gcd(*(int(v * scale) for v in denominations.values())), scale)
    lower = (price - credit) / quantum
    first = -(-lower.numerator // lower.denominator)
    last = min(price, coin_total) // quantum
    attempts = 0
    counter = [0]
    for units in range(first, last + 1):
        attempts += 1
        if attempts > 128:
            raise CalculationLimit('Превышен безопасный предел комбинированной оплаты.')
        net = quantum * units
        try:
            debit, change = payment(net, denominations, holdings, _counter=counter, change_denominations=change_denominations)
            return debit, change, price - net
        except CalculationLimit:
            raise
        except MoneyError:
            continue
    raise MoneyError('Невозможно точно оплатить товар монетами и торговым остатком.')

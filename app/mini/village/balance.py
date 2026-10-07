"""Exact configurable Mythic production, in eighths of a resource per hour."""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
BALANCE_PATH = Path(__file__).with_name('balance.json')


def load_balance():
    data = json.loads(BALANCE_PATH.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Village balance must be an object.')
    raw = data.get('mythic_hourly_rate', '1.25')
    if isinstance(raw, bool):
        raise ValueError('Invalid Mythic Village rate.')
    try:
        rate = Decimal(str(raw))
    except InvalidOperation as error:
        raise ValueError('Invalid Mythic Village rate.') from error
    if not rate.is_finite() or rate <= 0 or rate * 8 != (rate * 8).to_integral_value():
        raise ValueError('Mythic Village rate must be positive in increments of 0.125.')
    return {'mythic_rate_units': int(rate * 32)}

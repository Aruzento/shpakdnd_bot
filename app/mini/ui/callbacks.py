"""Compact integer fields keep personal Telegram callback payloads under 64 bytes."""
DIGITS='0123456789abcdefghijklmnopqrstuvwxyz'


def number(value):
    value=int(value)
    if value<0: raise ValueError('Negative callback identifier.')
    result=''
    while value:
        value, digit=divmod(value,36)
        result=DIGITS[digit]+result
    return result or '0'


def integer(value):
    if not value or any(c not in DIGITS for c in value): raise ValueError('Invalid callback identifier.')
    return int(value,36)

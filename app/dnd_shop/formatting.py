"""Exact Russian money presentation, independent of receipts and storage."""
from fractions import Fraction
from app.dnd_shop.money import exact_text

FORMS=(('медная','медные','медных'),('серебряная','серебряные','серебряных'),
       ('золотая','золотые','золотых'))


def plural(n, forms):
    return forms[0] if n % 10 == 1 and n % 100 != 11 else (
        forms[1] if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else forms[2])


def amount(value, coin_profile=None, *, base_name='базовой валюты', credit=False):
    value=Fraction(value)
    if value < 0:
        raise ValueError('Денежная сумма не может быть отрицательной.')
    if not coin_profile:
        text=exact_text(value).replace('.',',')+' '+base_name
    elif not value:
        text='0'
    else:
        remaining=value
        parts=[]
        for index in (2,1,0):
            nominal=Fraction(coin_profile['values'][index])
            count=remaining//nominal
            remaining-=count*nominal
            if count:
                adjective=plural(count,FORMS[index])
                universal=plural(count,('универсальная','универсальные','универсальных'))+' ' if coin_profile.get('universal') else ''
                parts.append(f'{count} {universal}{adjective}')
        if remaining:
            fraction=exact_text(remaining/Fraction(coin_profile['values'][0])).replace('.',',')
            parts.append(f'{fraction} '+('универсальной ' if coin_profile.get('universal') else '')+'медной монеты')
        text=', '.join(parts)
    return text+(' (торговый остаток)' if credit else '')

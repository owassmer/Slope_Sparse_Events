"""Python boundary for compiled question records. No financial calculations here."""
import numpy as np

from app.exact import fields as f
from app.exact import fields_record as r


def _band(lo, hi):
    return '-' if lo == f.NONE else f'{lo}-{"top" if hi == f.NONE else hi}'


def record(ints, floats):
    """One draw back to question_row's dict of length-one arrays and scalars."""
    if ints.shape != (r.NI,) or floats.shape != (r.NF,):
        raise ValueError('wrong question record layout')
    out = {'sit': {}, 'marks': {}, 'triggers': {}}
    boolean = {'sit.offering_pending', 'sit.offering_bound_reached'}
    def put(name, value):
        target = out
        bits = name.split('.')
        for bit in bits[:-1]:
            target = target.setdefault(bit, {})
        target[bits[-1]] = value
    for name, value in zip(r.INT_FIELDS[:r.ARREAR0 + len(r.ARREARS)], ints, strict=False):
        if name in r.BASE and '.' not in name and name not in r.BASE[:9]:
            continue
        if name == 'sit.declaration_deadline' and not ints[r.HAS_DECLARATION]:
            continue
        if name == 'groups':
            put(name, np.array([value], dtype=np.int8) if ints[r.HAS_GROUPS] else None)
        elif name in r.VOCABULARIES:
            put(name, np.array([r.VOCABULARIES[name][value]], dtype=object))
        else:
            put(name, np.array([value], dtype=bool if name in boolean else np.int64))
    out['sit']['owed'] = out['owed'].copy()
    lo, hi = int(ints[r.BAND_LO]), int(ints[r.BAND_HI])
    out['sit']['band'] = None if lo == f.NONE else _band(lo, hi)
    out['sit']['band_range'] = None if lo == f.NONE else (lo, None if hi == f.NONE else hi)
    out['sit']['share_price'] = np.array([floats[0]])
    if ints[r.HAS_COUPON_CASH]:
        out['triggers']['coupon_cash'] = np.array([ints[r.COUPON_CASH]])
    out['sit']['offerings'] = [tuple(np.array([ints[r.OFFERS0 + 3*j + q]], dtype=bool if q == 2 else np.int64)
                                       for q in range(3)) for j in range(ints[r.OFFERING_COUNT])]
    if ints[r.HAS_SECURITY]:
        out['security_terms'] = {key: np.array([ints[r.SECURITY0 + j]], dtype=bool if key == 'live' else np.int64)
                                 for j, key in enumerate(('day', 'cash', 'need', 'owed', 'collateral', 'offer', 'live'))}
    if ints[r.HAS_PRICING]:
        out['sit']['settlement_pricing'] = {key: np.array([ints[r.PRICING0 + j]]) for j, key in
                                          enumerate(('day', 'cash', 'operating_need'))}
    return out


def situation_class(code, contexts=('',)):
    """Exact forecast class string; context vocabulary must be supplied by its owner."""
    if code.shape != (r.NC,):
        raise ValueError('wrong class layout')
    live, lo, hi, standing, notes, listing, offer, pay, appeal, stay, context, group = map(int, code)
    text = ''
    if live:
        text = '.'.join(('#' + _band(lo, hi), r.STANDINGS[standing], r.NOTES[notes], r.LISTINGS[listing],
                         r.OFFER[offer], 'pay' if pay else 'nopay'))
        if appeal >= 0:
            text += '.appeal' + str(appeal)
        if stay >= 0:
            text += '.stay' + r.STAY_STATUSES[stay]
        if context:
            text += '.ctx' + contexts[context].encode().hex()
    if group != -2:
        text = (text or '#-') + '.g' + str(group) if group >= 0 else ''
    return text

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
    out['sit']['offerings'] = _offerings(ints[None, :])
    out['sit']['events'] = _events(ints[None, :])
    if ints[r.HAS_SECURITY]:
        out['security_terms'] = {key: np.array([ints[r.SECURITY0 + j]], dtype=bool if key == 'live' else np.int64)
                                 for j, key in enumerate(('day', 'cash', 'need', 'owed', 'collateral', 'offer', 'live'))
                                 if key != 'owed' or ints[r.HAS_SECURITY] != 2}
    if ints[r.HAS_PRICING]:
        out['sit']['settlement_pricing'] = {key: np.array([ints[r.PRICING0 + j]]) for j, key in
                                          enumerate(('day', 'cash', 'operating_need'))}
    _known(out)
    return out


def _known(out):
    """Each trigger's day its date is fixed (events.KNOWN): `record` writes a trigger only once it is fixed, so the
    record's own day."""
    from app.analysis.events import KNOWN

    for name in [k for k in out['triggers'] if k != 'coupon_cash']:
        out['triggers'][KNOWN + name] = np.asarray(out['day'], dtype=np.int64).copy()


def situation_class(code, contexts=('',)):
    """Exact forecast class string; context vocabulary must be supplied by its owner."""
    if code.shape != (r.NC,):
        raise ValueError('wrong class layout')
    live, lo, hi, standing, notes, listing, offer, pay, appeal, stay, context, group, security = map(int, code)
    text = ''
    if live:
        text = '.'.join(('#' + _band(lo, hi), r.STANDINGS[standing], r.NOTES[notes], r.LISTINGS[listing],
                         r.OFFER[offer], 'pay' if pay else 'nopay'))
        if appeal >= 0:
            text += '.appeal' + str(appeal)
        if stay >= 0:
            text += '.stay' + r.STAY_STATUSES[stay]
        if security >= 0:
            text += '.sec' + r.SECURITY_KINDS[security]
        if context:
            text += '.ctx' + contexts[context].encode().hex()
    if group != -2:
        text = (text or '#-') + '.g' + str(group) if group >= 0 else ''
    return text


def _one(values: np.ndarray, name: str):
    """A presence flag the same on every member (a question's records all carry a part, or none does)."""
    if (values != values[0]).any():
        raise ValueError(f'members of one question differ in {name}')
    return int(values[0])


def rows(ints, floats):
    """`record` for M members at once ([M, NI], [M, NF]): one row of M trajectories, as the question state reads them
    (a value the same on every member kept as one value, as `pool._stack` keeps it; the offering attempts padded with
    attempts that did not happen on a member)."""
    ints, floats = np.asarray(ints), np.asarray(floats)
    if ints.ndim != 2 or ints.shape[1] != r.NI or floats.shape != (len(ints), r.NF):
        raise ValueError('wrong question records layout')
    m = len(ints)
    out = {'sit': {}, 'marks': {}, 'triggers': {}}
    boolean = {'sit.offering_pending', 'sit.offering_bound_reached'}

    def put(name, value):
        target = out
        bits = name.split('.')
        for bit in bits[:-1]:
            target = target.setdefault(bit, {})
        target[bits[-1]] = value

    declaration = _one(ints[:, r.HAS_DECLARATION], 'declaration')
    groups = _one(ints[:, r.HAS_GROUPS], 'groups')
    for j, name in enumerate(r.INT_FIELDS[:r.ARREAR0 + len(r.ARREARS)]):
        if name in r.BASE and '.' not in name and name not in r.BASE[:9]:
            continue
        if name == 'sit.declaration_deadline' and not declaration:
            continue
        col = ints[:, j]
        if name == 'groups':
            put(name, col.astype(np.int8) if groups else None)
        elif name in r.VOCABULARIES:
            put(name, np.asarray(r.VOCABULARIES[name], dtype=object)[col])
        else:
            put(name, col.astype(bool if name in boolean else np.int64))
    out['sit']['owed'] = out['owed'].copy()
    bands = {(int(lo), int(hi)) for lo, hi in ints[:, [r.BAND_LO, r.BAND_HI]]}
    if len(bands) == 1:
        lo, hi = bands.pop()
        out['sit']['band'] = None if lo == f.NONE else _band(lo, hi)
        out['sit']['band_range'] = None if lo == f.NONE else (lo, None if hi == f.NONE else hi)
    else:
        band, rng = np.empty(m, dtype=object), np.empty(m, dtype=object)
        for i, (lo, hi) in enumerate(ints[:, [r.BAND_LO, r.BAND_HI]]):
            lo, hi = int(lo), int(hi)
            band[i] = None if lo == f.NONE else _band(lo, hi)
            rng[i] = None if lo == f.NONE else (lo, None if hi == f.NONE else hi)
        out['sit']['band'], out['sit']['band_range'] = band, rng
    out['sit']['share_price'] = floats[:, 0].astype(np.float64)
    if _one(ints[:, r.HAS_COUPON_CASH], 'coupon cash'):
        out['triggers']['coupon_cash'] = ints[:, r.COUPON_CASH].astype(np.int64)
    out['sit']['offerings'] = _offerings(ints)
    out['sit']['events'] = _events(ints)
    security = _one(ints[:, r.HAS_SECURITY], 'security terms')
    if security:
        out['security_terms'] = {key: ints[:, r.SECURITY0 + j].astype(bool if key == 'live' else np.int64)
                                 for j, key in enumerate(('day', 'cash', 'need', 'owed', 'collateral', 'offer', 'live'))
                                 if key != 'owed' or security != 2}
    if _one(ints[:, r.HAS_PRICING], 'settlement pricing'):
        out['sit']['settlement_pricing'] = {key: ints[:, r.PRICING0 + j].astype(np.int64) for j, key in
                                          enumerate(('day', 'cash', 'operating_need'))}
    _known(out)
    return out


def _events(ints) -> np.ndarray:
    """Each member's dated events (`record.events`), as a list of (kind, day, amount, ref) per member (an object
    array, so a group's representative reads its own)."""
    out = np.empty(ints.shape[0], dtype=object)
    for i in range(ints.shape[0]):
        rows = [ints[i, r.EVENTS0 + r.EVENT_COLS * j:r.EVENTS0 + r.EVENT_COLS * (j + 1)]
                for j in range(int(ints[i, r.EVENT_COUNT]))]
        out[i] = [(r.EVENTS[int(x[0])], int(x[1]), int(x[2]), int(x[3])) for x in rows]
    return out


def _offerings(ints) -> np.ndarray:
    """Each member's offerings initiated by its day (`record`), as a list of (init, close, status, net) per member."""
    out = np.empty(ints.shape[0], dtype=object)
    for i in range(ints.shape[0]):
        rows = [ints[i, r.OFFERS0 + r.OFFER_COLS * j:r.OFFERS0 + r.OFFER_COLS * (j + 1)]
                for j in range(int(ints[i, r.OFFERING_COUNT]))]
        out[i] = [(int(x[0]), int(x[1]), r.OFFER_STATUS[int(x[2])], int(x[3])) for x in rows]
    return out

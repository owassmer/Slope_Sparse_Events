"""Fixed one-draw question layout. Dotted names are question_row dictionary paths.

Amounts and days are int64, as in Chain; share_price alone is float64.
Optional fields have explicit presence columns, never a zero-as-unknown convention.
HAS_SECURITY is 0 absent, 1 projected motion terms (including owed), or 2 direct
court terms (owed remains top-level, not repeated inside security_terms).
String columns index the vocabularies below. Band strings are losslessly represented
by BAND_LO/BAND_HI (NONE means absent / open upper endpoint respectively).
"""
from app.analysis.events import MARKS, TRIGGERS
from app.analysis.processor import ARREARS
from app.exact import fields_equity as e
from app.exact.fields_notes import HOW
from app.exact.fields_notes import STANDING as STANDINGS

LISTINGS = ('listed', 'hearing_requested', 'suspended', 'delisted')
# the dated events a question states (`record.events`), each (kind, day, amount): every stay motion and its outcome,
# every levy and payment, in date order, those dated on or before the question's day
# (each stay answer's ref is its motion's day; an approved stay's amount the cash it locks, NONCASH where its security
# is not in cash)
EVENTS = ('stay_moved', 'stay_approved', 'stay_denied', 'stay_moot', 'stay_no_effect', 'levied', 'paid', 'registered')
# a stay motion's proposed security (fields_agenda.T_OFFER_KIND), its amount beside it
OFFER_KINDS = ('full', 'reduced', 'noncash', 'unsized')
MAX_EVENTS = 24
EVENT_COLS = 4  # kind, day, amount, ref
NONCASH = -1
# every offering initiated on or before the question's day: init, close, status, net proceeds
OFFER_COLS = 4
OFFER_STATUS = ('pending', 'closed', 'failed')
STAY_STATUSES = ('not_requested', 'pending', 'denied', 'approved', 'resolved')
NOTES = ('current', 'default', 'due')
OFFER = ('available', 'insufficient', 'nocapacity', 'pending', 'petition', 'delisted')
TERMS = ('gross', 'costs', 'net', 'price_cents_x1e4', 'shares', 'close_days', 'shelf_capacity')
BASE = ('day', 'cash', 'owed', 'collateral', 'petition', 'settle_offer', 'stay_offer', 'raise_offer',
        'groups', 'sit.cash_end', 'sit.entry', 'sit.ruling', 'sit.delisted', 'sit.stayed_from',
        'sit.motions_filed', 'sit.motions_deadline', 'sit.entry_appeal_deadline', 'sit.stay_status',
        'sit.standing', 'sit.entered', 'band_lo', 'band_hi', 'sit.default_available', 'sit.route_days',
        'sit.remitted', 'sit.listing', 'sit.atm', 'sit.ledger', 'sit.offering_pending', 'sit.offer_available',
        'sit.offer_shortfall', 'sit.offering_bound_reached', 'sit.notes_due_day', 'sit.notes_due_how',
        'sit.first_unpaid', 'sit.nonpayment_day', 'sit.declaration_deadline', 'has_declaration',
        'has_groups', 'has_coupon_cash', 'coupon_cash', 'offering_count', 'has_security', 'has_pricing',
        'event_count', 'sit.motion_day', 'sit.motion_offer', 'sit.motion_offer_kind', 'settle_basis',
        'sit.levy_scheduled', 'sit.registered')
INT_FIELDS = (BASE + tuple('marks.' + x for x in MARKS) + tuple('triggers.' + x for x in TRIGGERS)
              + tuple('sit.offering_terms.' + x for x in TERMS) + tuple('sit.arrears.' + x for x in ARREARS)
              + tuple('security_terms.' + x for x in ('day', 'cash', 'need', 'owed', 'collateral', 'offer', 'live'))
              + tuple('sit.settlement_pricing.' + x for x in ('day', 'cash', 'operating_need'))
              + tuple(f'sit.offerings.{j}.{x}' for j in range(e.MAX_OFFERS) for x in ('init', 'close', 'status', 'net'))
              + tuple(f'sit.events.{j}.{x}' for j in range(MAX_EVENTS) for x in ('kind', 'day', 'amount', 'ref')))
FLOAT_FIELDS = ('sit.share_price',)
for _i, _name in enumerate(BASE):
    globals()[_name.removeprefix('sit.').upper()] = _i
MARK0 = len(BASE)
TRIGGER0 = MARK0 + len(MARKS)
TERM0 = TRIGGER0 + len(TRIGGERS)
ARREAR0 = TERM0 + len(TERMS)
SECURITY0 = ARREAR0 + len(ARREARS)
PRICING0 = SECURITY0 + 7
OFFERS0 = PRICING0 + 3
EVENTS0 = OFFERS0 + OFFER_COLS * e.MAX_OFFERS
NI, NF = len(INT_FIELDS), len(FLOAT_FIELDS)
VOCABULARIES = {'sit.standing': STANDINGS, 'sit.listing': LISTINGS, 'sit.stay_status': STAY_STATUSES,
                'sit.notes_due_how': HOW}
# Class tuple: live, band endpoints, standing, notes, listing, offering, pay,
# appeal (-1 omitted), stay (-1 omitted), context vocabulary id, option group (-2 omitted).
CLASS_FIELDS = ('live', 'band_lo', 'band_hi', 'standing', 'notes', 'listing', 'offer', 'pay',
                'appeal', 'stay', 'context', 'group', 'security', 'settle_ratio', 'enforcement')
ENFORCEMENT = ('none', 'levy_scheduled', 'registered')  # what the claimant can do now (QUESTIONS §1 Grouping)
# a settlement question's offer as a share of the amount owed, in bands (QUESTIONS D3 Grouping: groups split where
# offers differ materially in size relative to the amount owed; the bands are ours): below 10%, to 25%, to 50%, above
SETTLE_RATIO_BPS = (1000, 2500, 5000)
SECURITY_KINDS = ('full', 'reduced', 'none', 'not_live')  # a stay question's security type (QUESTIONS D4 Grouping)
NC = len(CLASS_FIELDS)

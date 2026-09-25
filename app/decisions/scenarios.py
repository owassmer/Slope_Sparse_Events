"""Scenario engine: how each Slope financing structure performs, bank-only and along the disputes' paths.

Views (matched pair; common inputs identical, only the dispute paths differ):
  - bank_only: the connected-bank projection and the financing action.
  - event_adjusted: the same, plus one path from each dispute (the generic dispute model gives each dispute its
    root-to-leaf paths with conditional weights; distinct disputes are combined as independent, labelled). Each path's
    cash is placed two ways: `stress` (outflows and locks at the window start at the high amount; inflows at the
    window end at the low amount) and `central` (window midpoint, midpoint amount).

The financing action: Slope pays the supplier up to the financed amount on the funding date; the borrower pays any
remainder of the invoice itself on its due date (all of it if Slope declines), then repays Slope on the structure's
schedule. Collections (declared rule): ACH autopay takes each scheduled payment when available cash covers it;
otherwise it takes the available cash and retries the rest at the next month-end (catch-up).

Weighted (expected) figures use path weights (products of Jev's conditional judgments, labelled model judgment).
Every conditional result is kept beside them. Money is integer cents; rates are Decimal.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.domain.investigation import DisputePath
from app.domain.values import cents_round, usd
from app.finance.bank import BankFeed, projection, risk_features
from app.finance.slope_products import BPS, SlopeOffer, limits, menu, offer, term_spec, tier_for
from app.finance.valuation import present_value_cents

HORIZON_DAYS = 180
AMOUNT_STEP_CENTS = 1_000_000  # supported amounts are sized on a USD 10k grid


@dataclass(frozen=True)
class Request:
    amount_cents: int  # the invoice
    invoice_due: date  # when the borrower would pay the supplier itself
    funding: date  # when Slope pays the supplier
    requested_term_id: str


@dataclass
class Scenario:
    """One combination of dispute paths (empty for bank-only) under one cash placement."""
    view: str
    path_ids: tuple[str, ...]
    labels: tuple[str, ...]
    weight_bps: int | None
    placement: str
    cash: tuple = ()


@dataclass
class Outcome:
    """One structure in one scenario: daily-cash low point, payments and what Slope collects."""
    scenario: Scenario
    structure: str
    min_cash_cents: int = 0
    min_cash_on: date | None = None
    collections: list[tuple[date, int]] = field(default_factory=list)
    shortfall_cents: int = 0
    payment_dates: list[tuple[date, int, int]] = field(default_factory=list)  # (due, scheduled, cash after payment)


def _place(amount, window_start: date, window_end: date, kind: str, placement: str) -> tuple[date, int]:
    lo = amount.value if amount.value is not None else amount.lower
    hi = amount.value if amount.value is not None else amount.upper
    if placement == "stress":
        return (window_start, hi) if kind in ("outflow", "lock") else (window_end, lo)
    mid = window_start + (window_end - window_start) / 2
    return mid, (lo + hi) // 2


def _product(weights: list[int]) -> Decimal:
    p = Decimal(1)
    for w in weights:
        p *= Decimal(w) / BPS
    return p


def _combination_weights(combos: list[tuple[DisputePath, ...]]) -> list[int | None]:
    """Products of the disputes' path weights (combined as independent), in basis points summing to exactly 10,000."""
    if any(p.weight_bps is None for combo in combos for p in combo):
        return [None] * len(combos)
    raw = [_product([p.weight_bps for p in combo]) * BPS for combo in combos]
    out = [int(r.to_integral_value()) for r in raw]
    out[out.index(max(out))] += 10_000 - sum(out)
    return out


def scenarios(disputes: list[list[DisputePath]]) -> list[Scenario]:
    views = [Scenario("bank_only", (), (), 10_000, "central")]
    if not disputes:
        return views
    combos = list(itertools.product(*disputes))
    for combo, w in zip(combos, _combination_weights(combos), strict=True):
        cash = tuple(c for p in combo for c in p.cash)
        for placement in (("stress", "central") if cash else ("central",)):
            views.append(Scenario("event_adjusted", tuple(p.path_id for p in combo),
                                  tuple(" → ".join(p.labels) for p in combo), w, placement, cash))
    return views


def simulate(feed: BankFeed, request: Request, structure: SlopeOffer | None, cash_items, placement: str,
             horizon: date) -> tuple[dict[date, int], list[tuple[date, int]], int, list[tuple[date, int, int]]]:
    """Daily available cash (after restricted cash) and Slope's collections under the declared autopay rule."""
    flows: dict[date, int] = {}

    def add(d: date, cents: int) -> None:
        flows[d] = flows.get(d, 0) + cents

    for d, _, cents in projection(feed, horizon):
        add(d, cents)
    financed = structure.amount_cents if structure else 0
    if request.amount_cents > financed:
        add(request.invoice_due, -(request.amount_cents - financed))  # the borrower pays the rest of its invoice
    for item in cash_items:
        on, amt = _place(item.amount, item.window_start, item.window_end, item.kind, placement)
        add(on, -amt if item.kind in ("outflow", "lock") else amt)
    due: dict[date, int] = {}
    for p in (structure.schedule(request.funding) if structure else []):
        due[p.due] = due.get(p.due, 0) + p.amount_cents
    cash, path, collected, owed, pays = feed.available_cents, {}, [], 0, []
    d = feed.period_end + timedelta(days=1)
    while d <= horizon:
        cash += flows.get(d, 0)
        owed += due.get(d, 0)
        month_end = (d + timedelta(days=1)).month != d.month
        if owed and (d in due or month_end):
            take = min(owed, max(cash, 0))
            if take:
                cash -= take
                owed -= take
                collected.append((d, take))
            if d in due:
                pays.append((d, due[d], cash))
        path[d] = cash
        d += timedelta(days=1)
    return path, collected, owed, pays


@dataclass
class Comparison:
    tier: str
    tier_measures: dict
    base_limits: dict
    structures: dict[str, SlopeOffer | None]
    outcomes: list[Outcome]
    request: Request
    liquidity_floor_cents: int
    scenarios: list[Scenario]
    horizon: date
    feed: BankFeed = field(repr=False, default=None)


def _run(comp: Comparison, name: str) -> list[Outcome]:
    out = []
    for sc in comp.scenarios:
        daily, collected, shortfall, pays = simulate(comp.feed, comp.request, comp.structures[name], sc.cash,
                                                     sc.placement, comp.horizon)
        low = min(daily, key=daily.get)
        out.append(Outcome(sc, name, daily[low], low, collected, shortfall, pays))
    return out


def policy_checks(comp: Comparison, terms: dict, name: str, view: str) -> dict:
    """Reconstructed Slope policy for one structure in one view, on that structure's own cash paths: the limit (lower
    of the share of monthly inflows and the share of the lowest projected cash), the order limit, tenor, and the
    liquidity floor at every payment date in every scenario. Names the binding constraint, scenario and date."""
    s = comp.structures[name]
    pol = terms["credit_policy"]
    outs = [o for o in comp.outcomes if o.structure == name and o.scenario.view == view]
    lowest = min(outs, key=lambda o: o.min_cash_cents)
    cash_limit = cents_round(Decimal(max(lowest.min_cash_cents, 0)) * pol["limit_share_of_min_projected_cash_bps"] / BPS)
    limit = min(comp.base_limits["limit_cents"], cash_limit)
    binding_limit = "lowest projected cash" if cash_limit < comp.base_limits["limit_cents"] else "monthly bank inflows"
    order_limit = cents_round(Decimal(limit) * pol["order_limit_share_of_limit_bps"] / BPS)
    result = {"limit_cents": limit, "order_limit_cents": order_limit, "limit_set_by": binding_limit,
              "lowest_projected_cash_cents": lowest.min_cash_cents, "lowest_cash_on": lowest.min_cash_on.isoformat(),
              "lowest_cash_scenario": list(lowest.scenario.labels), "lowest_cash_placement": lowest.scenario.placement}
    if s is None:
        return {**result, "passes": True, "reasons": []}
    reasons = []
    if s.amount_cents > order_limit:
        reasons.append(f"amount above the order limit of {usd(order_limit)}")
    if s.days > pol["max_tenor_days"]:
        reasons.append("tenor above policy")
    breaches = [(o.scenario, d, after) for o in outs for d, _, after in o.payment_dates if after < comp.liquidity_floor_cents]
    if breaches:
        sc, d, after = min(breaches, key=lambda b: b[2])
        reasons.append(f"cash after the {d.isoformat()} payment falls to {usd(after)}, below one month of outflows "
                       f"({usd(comp.liquidity_floor_cents)}), in {' / '.join(sc.labels) or 'the bank-only view'} "
                       f"({sc.placement})")
    if any(o.shortfall_cents for o in outs):
        reasons.append("a scheduled payment is not fully collected by the horizon in some scenario")
    return {**result, "passes": not reasons, "reasons": reasons}


def _supported_amount(comp: Comparison, terms: dict, view: str) -> tuple[SlopeOffer | None, str | None]:
    """Largest amount on the requested term, on a USD 10k grid below the request, that passes policy on its own cash
    paths: a full downward scan (no monotonicity assumed), so the first amount that passes is the largest. Returns it
    and the next grid amount above it (which fails), both kept in the comparison for the explanation."""
    req = comp.request
    failed, added = None, set()
    for cents in range(req.amount_cents // AMOUNT_STEP_CENTS * AMOUNT_STEP_CENTS, 0, -AMOUNT_STEP_CENTS):
        o = offer(terms, req.requested_term_id, cents, comp.tier)
        if o.offer_id not in comp.structures:
            comp.structures[o.offer_id] = o
            comp.outcomes += _run(comp, o.offer_id)
            added.add(o.offer_id)
        if policy_checks(comp, terms, o.offer_id, view)["passes"]:
            return o, failed
        if failed in added:
            _drop(comp, failed)  # keep only the amount just above the answer
        failed = o.offer_id
    return None, failed


def _drop(comp: Comparison, name: str) -> None:
    comp.structures.pop(name, None)
    comp.outcomes = [o for o in comp.outcomes if o.structure != name]


def compare(feed: BankFeed, terms: dict, request: Request, disputes: list[list[DisputePath]],
            horizon_days: int = HORIZON_DAYS) -> Comparison:
    features = risk_features(feed)
    tier, measures = tier_for(features, terms)
    structures: dict[str, SlopeOffer | None] = {"decline": None}
    for o in menu(terms, request.amount_cents, tier):
        structures[o.offer_id] = o
    floor = cents_round(Decimal(features["monthly_outflows_cents"])
                        * terms["credit_policy"]["liquidity_floor_months_of_outflows_bps"] / BPS)
    comp = Comparison(tier=tier, tier_measures=measures, base_limits=limits(features, terms), structures=structures,
                      outcomes=[], request=request, liquidity_floor_cents=floor, scenarios=scenarios(disputes),
                      horizon=feed.period_end + timedelta(days=horizon_days), feed=feed)
    for name in list(structures):
        comp.outcomes += _run(comp, name)
    return comp


def _principal_dollar_days(o: SlopeOffer, funding: date, collections: list[tuple[date, int]], horizon: date) -> int:
    """Outstanding principal x days, in dollar-days, from funding to actual collection (principal is each collection's
    pro rata share of the total due); principal still outstanding at the horizon counts to the horizon."""
    outstanding, last, total = o.amount_cents, funding, Decimal(0)
    for d, c in collections:
        total += Decimal(outstanding) * (d - last).days
        outstanding -= min(outstanding, cents_round(Decimal(c) * o.amount_cents / o.total_cents))
        last = d
    total += Decimal(outstanding) * (horizon - last).days
    return int(total / 100)


def economics(o: SlopeOffer, comp: Comparison, collections: list[tuple[date, int]], terms: dict) -> dict:
    """Lender economics on one scenario's collections: NPV at cost of funds, dollar-days, expected loss."""
    cap = terms["capital"]
    decision = comp.request.funding - timedelta(days=1)
    cof = Decimal(cap["cost_of_funds_bps_per_year"]) / BPS
    flows = [((comp.request.funding - decision).days, -o.amount_cents)] + [((d - decision).days, c) for d, c in collections]
    years = Decimal(o.days) / Decimal(365)
    pd = Decimal(cap["probability_of_default_bps_per_year"][o.tier]) / BPS
    el = cents_round(Decimal(o.amount_cents) * pd * years * Decimal(cap["loss_given_default_bps"]) / BPS)
    collected = sum(c for _, c in collections)
    return {"fee_cents": o.fee_cents, "fee_bps": o.fee_bps, "apr_equivalent_bps": o.apr_equivalent_bps(comp.request.funding),
            "collected_cents": collected, "uncollected_cents": o.total_cents - collected,
            "npv_at_cost_of_funds_cents": cents_round(present_value_cents(flows, cof)),
            "principal_dollar_days": _principal_dollar_days(o, comp.request.funding, collections, comp.horizon),
            "expected_loss_cents": el, "fee_net_of_expected_loss_cents": o.fee_cents - el}


def _expected_series(outs: list[Outcome]) -> list[dict] | None:
    central = [o for o in outs if o.scenario.placement == "central"]
    if not central or any(o.scenario.weight_bps is None for o in central):
        return None
    by_day: dict[date, Decimal] = {}
    for o in central:
        for d, c in o.collections:
            by_day[d] = by_day.get(d, Decimal(0)) + Decimal(o.scenario.weight_bps) / BPS * c
    return [{"date": d.isoformat(), "amount_cents": cents_round(v)} for d, v in sorted(by_day.items())]


def summarize(comp: Comparison, terms: dict) -> dict:
    """The decision view: recommendation per view, then per structure: policy, and per scenario its dated collections
    and economics; the contractual and expected series."""
    views = sorted({sc.view for sc in comp.scenarios}, key=["bank_only", "event_adjusted"].index)
    recs = {view: recommend(comp, terms, view) for view in views}  # may add sized structures first
    out = {"tier": comp.tier, "tier_measures": comp.tier_measures, "liquidity_floor_cents": comp.liquidity_floor_cents,
           "horizon": comp.horizon.isoformat(), "recommendation": recs, "structures": {}}
    for name, s in comp.structures.items():
        entry = {"label": "Decline (borrower pays its supplier)" if s is None else
                 f"{usd(s.amount_cents)}, {term_spec(terms, s.term_id)['label']}", "views": {}}
        for view in views:
            outs = [o for o in comp.outcomes if o.structure == name and o.scenario.view == view]
            v = {"policy": policy_checks(comp, terms, name, view), "scenarios": []}
            for o in outs:
                row = {"paths": list(o.scenario.path_ids), "labels": list(o.scenario.labels),
                       "weight_bps": o.scenario.weight_bps, "placement": o.scenario.placement,
                       "lowest_cash_cents": o.min_cash_cents, "lowest_cash_on": o.min_cash_on.isoformat()}
                if s is not None:
                    row["collections"] = [{"date": d.isoformat(), "amount_cents": c} for d, c in o.collections]
                    row["economics"] = economics(s, comp, o.collections, terms)
                v["scenarios"].append(row)
            if s is not None:
                v["contractual"] = [{"due": p.due.isoformat(), "amount_cents": p.amount_cents,
                                     "principal_cents": p.principal_cents, "fee_cents": p.fee_cents}
                                    for p in s.schedule(comp.request.funding)]
                v["expected_collections"] = _expected_series(outs)
            entry["views"][view] = v
        out["structures"][name] = entry
    return out


def recommend(comp: Comparison, terms: dict, view: str) -> dict:
    """Declared objective, per view: the requested structure if it passes policy in every scenario and placement;
    otherwise the largest amount on the requested term that passes (sized on its own cash paths); otherwise decline.
    The explanation keeps each amount's own figures (its limit, order limit and lowest cash, which fall as the amount
    rises), and names the next grid amount above the recommendation and why it fails."""
    req = comp.request
    requested = f"{req.requested_term_id}_{req.amount_cents // 100}"
    req_check = policy_checks(comp, terms, requested, view)
    next_up = None
    if req_check["passes"]:
        chosen = requested
    else:
        sized, next_up = _supported_amount(comp, terms, view)
        chosen = sized.offer_id if sized else "decline"

    def figures(name: str) -> dict:
        c = policy_checks(comp, terms, name, view)
        s = comp.structures[name]
        return {"structure": name, "amount_cents": s.amount_cents if s else 0, "passes": c["passes"],
                "reasons": c["reasons"], "limit_cents": c["limit_cents"], "order_limit_cents": c["order_limit_cents"],
                "limit_set_by": c["limit_set_by"], "lowest_projected_cash_cents": c["lowest_projected_cash_cents"],
                "lowest_cash_on": c["lowest_cash_on"], "lowest_cash_scenario": c["lowest_cash_scenario"],
                "lowest_cash_placement": c["lowest_cash_placement"]}

    at_chosen = figures(chosen)
    return {"structure": chosen, "requested": requested, "requested_passes": chosen == requested,
            "limit_cents": at_chosen["limit_cents"], "order_limit_cents": at_chosen["order_limit_cents"],
            "at_recommended": at_chosen, "at_requested": figures(requested),
            "next_amount_up": figures(next_up) if next_up and next_up != requested else None}

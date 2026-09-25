"""Scenario engine: how each Slope financing structure performs, bank-only and with the dispute's branches.

Views (matched pair; common inputs identical, only the dispute nodes differ):
  - bank_only: the connected-bank projection (trailing 13 weeks per category) and the financing action.
  - event_adjusted: the same, plus every combination of the dispute nodes' branches. Each branch's cash is placed
    two ways: `stress` (outflows and locks at the window start at the high amount; inflows at the window end at the
    low amount) and `central` (window midpoint, midpoint amount).

The financing action: Slope pays the supplier invoice on the funding date, so the borrower no longer pays that invoice
itself on its due date, and instead repays Slope on the structure's schedule. Declining leaves the invoice with the
borrower. Collections (declared rule): ACH autopay takes each scheduled payment when available cash covers it;
otherwise it takes the available cash and retries the rest at the next month-end (catch-up).

Weighted (expected) figures use the nodes' branch weights (Jev's model judgment, labelled once) and treat nodes as
independent. Every conditional result is kept beside them. Money is integer cents; rates are Decimal.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.domain.investigation import DisputeNode
from app.domain.values import cents_round, usd
from app.finance.bank import BankFeed, projection, risk_features
from app.finance.slope_products import BPS, SlopeOffer, limits, menu, offer, term_spec, tier_for
from app.finance.valuation import present_value_cents, principal_dollar_days

HORIZON_DAYS = 180


@dataclass(frozen=True)
class Request:
    amount_cents: int
    invoice_due: date  # when the borrower would pay the supplier itself
    funding: date  # when Slope pays the supplier
    requested_term_id: str


@dataclass
class Path:
    """One scenario's daily available cash for one structure, and what Slope collects."""
    view: str
    branch_ids: tuple[str, ...]
    placement: str
    weight_bps: int | None
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


def branch_combinations(nodes: list[DisputeNode]) -> list[tuple[tuple[str, ...], int | None, list]]:
    """Cartesian product of branches across nodes: (branch ids, weight bps or None, cash items)."""
    if not nodes:
        return [((), 10_000, [])]
    out = []
    for combo in itertools.product(*[n.branches for n in nodes]):
        ids = tuple(f"{n.node_id}:{b.branch_id}" for n, b in zip(nodes, combo, strict=True))
        weights = [n.weights_bps.get(b.branch_id) for n, b in zip(nodes, combo, strict=True)]
        w = None if any(x is None for x in weights) else _product_bps(weights)
        out.append((ids, w, [c for b in combo for c in b.cash]))
    return out


def _product_bps(weights: list[int]) -> int:
    p = Decimal(1)
    for w in weights:
        p *= Decimal(w) / BPS
    return int((p * BPS).to_integral_value())


def simulate(feed: BankFeed, request: Request, structure: SlopeOffer | None, cash_items: list, placement: str,
             horizon: date) -> tuple[dict[date, int], list[tuple[date, int]], int, list[tuple[date, int, int]]]:
    """Daily available cash (after restricted cash) and Slope's collections under the declared autopay rule."""
    flows: dict[date, int] = {}

    def add(d: date, cents: int) -> None:
        flows[d] = flows.get(d, 0) + cents

    for d, _, cents in projection(feed, horizon):
        add(d, cents)
    if structure is None:
        add(request.invoice_due, -request.amount_cents)  # the borrower pays its own supplier
    for item in cash_items:
        on, amt = _place(item.amount, item.window_start, item.window_end, item.kind, placement)
        add(on, -amt if item.kind in ("outflow", "lock") else amt)
    schedule = structure.schedule(request.funding) if structure else []
    due: dict[date, int] = {}
    for p in schedule:
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
    limits: dict
    structures: dict[str, SlopeOffer | None]
    paths: list[Path]
    request: Request
    liquidity_floor_cents: int
    nodes: list[DisputeNode]


def compare(feed: BankFeed, terms: dict, request: Request, nodes: list[DisputeNode],
            horizon_days: int = HORIZON_DAYS) -> Comparison:
    features = risk_features(feed)
    tier, measures = tier_for(features, terms)
    horizon = feed.period_end + timedelta(days=horizon_days)
    structures: dict[str, SlopeOffer | None] = {"decline": None}
    for o in menu(terms, request.amount_cents, tier):
        structures[o.offer_id] = o
    floor = cents_round(Decimal(features["monthly_outflows_cents"])
                        * terms["credit_policy"]["liquidity_floor_months_of_outflows_bps"] / BPS)
    views = [("bank_only", [((), 10_000, [])])]
    if nodes:
        views.append(("event_adjusted", branch_combinations(nodes)))
    comp = Comparison(tier=tier, tier_measures=measures, limits=limits(features, terms), structures=structures,
                      paths=[], request=request, liquidity_floor_cents=floor, nodes=nodes)

    def run(names: list[str]) -> None:
        for view, combos in views:
            for ids, weight, items in combos:
                for placement in (("stress", "central") if items else ("central",)):
                    for name in names:
                        daily, collected, shortfall, pays = simulate(feed, request, structures[name], items, placement,
                                                                     horizon)
                        low_day = min(daily, key=daily.get)
                        comp.paths.append(Path(view=view, branch_ids=ids, placement=placement, weight_bps=weight,
                                               structure=name, min_cash_cents=daily[low_day], min_cash_on=low_day,
                                               collections=collected, shortfall_cents=shortfall, payment_dates=pays))
    run(list(structures))
    # The supported amount in each view: the order limit implied by the borrower's own lowest projected cash (the
    # decline paths), rounded down to USD 10k, on the requested term, whenever it is below the request.
    for view, _ in views:
        order_limit = policy_checks(comp, terms, "decline", view)["order_limit_cents"]
        supported = order_limit // 1_000_000 * 1_000_000
        if 0 < supported < request.amount_cents:
            s = offer(terms, request.requested_term_id, supported, tier)
            if s.offer_id not in structures:
                structures[s.offer_id] = s
                run([s.offer_id])
    return comp


def economics(o: SlopeOffer, funding: date, collections: list[tuple[date, int]], terms: dict,
              decision: date) -> dict:
    """Lender economics on this structure's collections: NPV at cost of funds, principal dollar-days, expected loss."""
    cap = terms["capital"]
    cof = Decimal(cap["cost_of_funds_bps_per_year"]) / BPS
    flows = [((funding - decision).days, -o.amount_cents)] + [((d - decision).days, c) for d, c in collections]
    npv = present_value_cents(flows, cof)
    collected = sum(c for _, c in collections)
    principal = [((p.due - decision).days, p.principal_cents) for p in o.schedule(funding)]
    dollar_days = principal_dollar_days([((funding - decision).days, o.amount_cents)], principal) \
        if collected >= o.total_cents else None
    years = Decimal(o.days) / Decimal(365)
    pd = Decimal(cap["probability_of_default_bps_per_year"][o.tier]) / BPS
    el = cents_round(Decimal(o.amount_cents) * pd * years * Decimal(cap["loss_given_default_bps"]) / BPS)
    return {"fee_cents": o.fee_cents, "fee_bps": o.fee_bps, "apr_equivalent_bps": o.apr_equivalent_bps(funding),
            "collected_cents": collected, "npv_at_cost_of_funds_cents": cents_round(npv),
            "principal_dollar_days": dollar_days, "expected_loss_cents": el,
            "fee_net_of_expected_loss_cents": o.fee_cents - el}


def policy_checks(comp: Comparison, terms: dict, name: str, view: str) -> dict:
    """Reconstructed Slope policy for one structure in one view: amount limits, tenor, and the liquidity floor at
    every payment date in every branch and placement. The limit also scales with the lowest projected cash."""
    s = comp.structures[name]
    pol = terms["credit_policy"]
    paths = [p for p in comp.paths if p.structure == name and p.view == view]
    low = min(p.min_cash_cents for p in paths)
    cash_limit = cents_round(Decimal(max(low, 0)) * pol["limit_share_of_min_projected_cash_bps"] / BPS)
    limit = min(comp.limits["limit_cents"], cash_limit)
    order_limit = cents_round(Decimal(limit) * pol["order_limit_share_of_limit_bps"] / BPS)
    if s is None:
        return {"limit_cents": limit, "order_limit_cents": order_limit, "passes": True, "reasons": []}
    reasons = []
    if s.amount_cents > order_limit:
        reasons.append(f"amount above the order limit of {usd(order_limit)}")
    if s.days > pol["max_tenor_days"]:
        reasons.append("tenor above policy")
    floor_breaches = [(p.branch_ids, p.placement, d.isoformat()) for p in paths for d, _, after in p.payment_dates
                      if after < comp.liquidity_floor_cents]
    if floor_breaches:
        reasons.append(f"cash after a payment falls below one month of outflows in {len(floor_breaches)} case(s)")
    if any(p.shortfall_cents for p in paths):
        reasons.append("a scheduled payment is not fully collected by the horizon in some branch")
    return {"limit_cents": limit, "order_limit_cents": order_limit, "passes": not reasons, "reasons": reasons,
            "floor_breaches": floor_breaches[:5], "lowest_projected_cash_cents": low}


def summarize(comp: Comparison, terms: dict) -> dict:
    """The decision view: per structure, bank-only vs event-adjusted policy, economics, collections and branches."""
    decision = comp.request.funding - timedelta(days=1)
    out = {"tier": comp.tier, "tier_measures": comp.tier_measures, "liquidity_floor_cents": comp.liquidity_floor_cents,
           "structures": {}, "nodes": [
               {"node_id": n.node_id, "decision_point": n.decision_point,
                "branches": [{"branch_id": b.branch_id, "label": b.label, "weight_bps": n.weights_bps.get(b.branch_id)}
                             for b in n.branches]} for n in comp.nodes]}
    views = ["bank_only"] + (["event_adjusted"] if comp.nodes else [])
    for name, s in comp.structures.items():
        entry = {"label": "Decline (borrower pays its supplier)" if s is None else
                 f"{usd(s.amount_cents)}, {term_spec(terms, s.term_id)['label']}", "views": {}}
        for view in views:
            checks = policy_checks(comp, terms, name, view)
            paths = [p for p in comp.paths if p.structure == name and p.view == view]
            central = [p for p in paths if p.placement == "central"]
            weighted = None
            if s is not None and all(p.weight_bps is not None for p in central):
                weighted = cents_round(sum((Decimal(p.weight_bps) / BPS) * sum(c for _, c in p.collections)
                                           for p in central))
            v = {"policy": checks, "lowest_cash_cents": min(p.min_cash_cents for p in paths),
                 "lowest_cash_on": min(paths, key=lambda p: p.min_cash_cents).min_cash_on.isoformat(),
                 "branches": [{"branch_ids": list(p.branch_ids), "placement": p.placement, "weight_bps": p.weight_bps,
                               "lowest_cash_cents": p.min_cash_cents, "lowest_cash_on": p.min_cash_on.isoformat(),
                               "collected_cents": sum(c for _, c in p.collections), "shortfall_cents": p.shortfall_cents}
                              for p in paths]}
            if s is not None:
                worst = min(paths, key=lambda p: sum(c for _, c in p.collections))
                v["economics"] = economics(s, comp.request.funding, worst.collections, terms, decision)
                v["expected_collected_cents"] = weighted
                v["contractual"] = [{"due": p.due.isoformat(), "amount_cents": p.amount_cents,
                                     "principal_cents": p.principal_cents, "fee_cents": p.fee_cents}
                                    for p in s.schedule(comp.request.funding)]
            entry["views"][view] = v
        out["structures"][name] = entry
    out["recommendation"] = {view: recommend(comp, out, view) for view in views}
    return out


def recommend(comp: Comparison, summary: dict, view: str) -> dict:
    """Declared objective, applied per view: the requested structure if it passes policy in every branch and
    placement; otherwise the passing structure closest to the request (largest amount, requested term first, then the
    shortest tenor); otherwise decline. The requested structure's failing reasons are kept for the explanation."""
    req = comp.request
    requested = f"{req.requested_term_id}_{req.amount_cents // 100}"
    ranked = sorted((n for n, s in comp.structures.items() if s is not None),
                    key=lambda n: (-comp.structures[n].amount_cents, comp.structures[n].term_id != req.requested_term_id,
                                   comp.structures[n].days))
    ranked.remove(requested)
    ranked.insert(0, requested)
    chosen = next((n for n in ranked if summary["structures"][n]["views"][view]["policy"]["passes"]), "decline")
    pol = summary["structures"]["decline"]["views"][view]["policy"]
    return {"structure": chosen, "requested": requested,
            "limit_cents": pol["limit_cents"], "order_limit_cents": pol["order_limit_cents"],
            "requested_passes": chosen == requested,
            "requested_reasons": summary["structures"][requested]["views"][view]["policy"]["reasons"]}

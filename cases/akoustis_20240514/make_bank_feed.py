"""Generate Akoustis's connected-bank feed (synthetic reconstruction) from 1 Jan to 14 May 2024.

Slope underwrites on connected bank data. Akoustis's is not public, so this feed reconstructs it from what was public
on the review date, 14 May 2024 (Model Extensions Spec §16.2-16.3): daily, counterparty-tagged transactions whose
totals match the company's filed cash-flow figures.

- January to March (fiscal Q3 2024, 10-Q of 13 May 2024), anchored exactly as the 20 Jun feed: cash of USD 12,875k at
  31 Dec 2023 and 15,200k at 31 Mar; customer receipts = revenue + decrease in receivables + increase in deferred
  revenue; operating outflows = receipts less operating cash flow; capital expenditure; the underwritten offering's
  net proceeds on its 29 Jan closing. Same seed and order, so these months' transactions equal the 20 Jun feed's
  (the legal-fee counterparty label aside).
- 1 April to 14 May: each category continues at its March-quarter monthly run rate (the quarter's total / 3 for a
  calendar month; May's first ten business days take 10/22 of a month). No later-reported figure enters: the
  April-June quarterly totals the 20 Jun feed prorates come from the FY2024 10-K (8 Oct 2024), and about half of that
  quarter falls after 14 May. The 24 May offering and the 17 Jun notes coupon are after the cutoff and absent.
- Nothing dated after 14 May enters the feed.

Result: cash of USD 11,281k on 14 May. At the run rate (receipts 2,637k, operating outflows 5,234k and capital 97k a
month: a net 2,694k) it lasts about 4.2 months, to about 19 Sep 2024 (fiscal Q1 2025). The 10-Q of 13 May says cash
"is sufficient to fund its operations into the third quarter of fiscal 2025" (January-March 2025) "with a continued
focus on cash conservation": about 1.7M a month, some 37% below the run rate. The 13 May call's further 30% cut in
operating cash burn (the cost-plan scenario) gives about 1.9M a month, to about 10 Nov 2024; the CHIPS investment tax
credit refund the call estimates at USD 2.8-4.0M, undated, would carry that into early 2025. Management's runway
therefore assumes both; the central case assumes neither.

Within each month, category totals are spread over the category's cadence with seeded noise and conserved exactly.
Seeded and deterministic: `uv run python cases/akoustis_20240514/make_bank_feed.py`.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "bank_feed.json"
SEED = 20240620  # the 20 Jun feed's seed: January to March come out identical
START, END = date(2024, 1, 1), date(2024, 5, 14)
OPENING_CENTS = 1_287_500_000  # 31 Dec 2023, cash and restricted cash (cash-flow statement)
HOLIDAYS = {date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 5, 27), date(2024, 6, 19)}  # Fed

# Assumptions (labelled; each one constant; every source dated on or before 14 May 2024). Payroll: cash labor. The
# 13 May 2024 earnings call gives March-quarter labor costs of USD 6.7M; less USD 0.944M stock-based compensation (10-Q
# of 13 May 2024) = USD 5.756M a quarter, about USD 1.92M a month (design/STAGE3.md, headcount item).
PAYROLL_MONTHLY_CENTS = 191_866_667
# Litigation spend, a PROXY: the year-on-year rise in professional fees and property tax in the March quarter (USD 1.9M
# a quarter, 10-Q of 13 May 2024), spread evenly by month. It is the spend attributed to the dispute, so it stops when
# the dispute ends; the counterparty label says it is a proxy.
LEGAL_MONTHLY_CENTS = 63_333_333
LEGAL_LABEL = "Litigation counsel (professional-fee proxy)"
# The rest of operating outflows are vendor invoices (tagged supplier_invoice), split by these shares.
SUPPLIER_MIX = [  # (counterparty, share, cadence)
    ("Wafer and substrate suppliers (fab materials)", 0.40, "weekly"),
    ("Process gas and metals suppliers (fab materials)", 0.20, "weekly"),
    ("Tai-Saw Technology (contract manufacturing)", 0.25, "biweekly"),
    ("Other vendors (facilities, utilities, services)", 0.15, "weekly"),
]
RECEIPT_MIX = [  # RF Filters is about 63% of revenue (segment disclosure in the 13 May 10-Q, 59.5-66.9%)
    ("RF filter customers", 0.63, "weekly"),
    ("RFMi and foundry services customers", 0.37, "weekly"),
]

# March-quarter totals in cents (10-Q of 13 May 2024; derived in design/CASH_CHECK_20240620.md §3 from its XBRL).
Q3 = {"receipts": 791_000_000, "operating_outflows": 1_570_100_000, "capital": -29_100_000, "employee_stock": 0}
Q3_MONTHS = (1, 2, 3)
RUN_RATE_MONTHS = (4, 5)  # April and 1-14 May at the March quarter's monthly run rate
DATED = [  # flows the filings date, on their own dates (only those on or before 14 May)
    (date(2024, 1, 29), 1_040_700_000, "Underwritten public offering, net proceeds", "equity_proceeds"),
]


def business_days(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


def month_bounds(month: int) -> tuple[date, date]:
    first = date(2024, month, 1)
    return first, (date(2024, month + 1, 1) - timedelta(days=1))


def cadence_days(bdays: list[date], cadence: str) -> list[date]:
    if cadence == "weekly":
        weeks: dict[int, date] = {}
        for d in bdays:  # one payment a week, on the week's Wednesday or the nearest business day before it
            if d.weekday() <= 2:
                weeks[d.isocalendar().week] = d
        return sorted(weeks.values())
    if cadence == "biweekly":
        return [d for d in bdays if d.weekday() == 4 and d.isocalendar().week % 2 == 0] or bdays[-1:]
    return bdays[-1:]


def portion(total: int, done: int, n: int, days: int) -> int:
    """Business days done+1..done+n of a quarter's total, by cumulative rounding: a full quarter sums exactly."""
    return round(total * (done + n) / days) - round(total * done / days)


def spread(total: int, when: list[date], rng: random.Random, noise: float = 0.18) -> list[tuple[date, int]]:
    w = [max(0.2, 1 + rng.uniform(-noise, noise)) for _ in when]
    raw = [int(total * x / sum(w)) for x in w]
    raw[-1] += total - sum(raw)  # conserve the total exactly
    return list(zip(when, raw, strict=True))


def month_amounts(m: int, inside: list[date], full: list[date], done: int, q_days: int) -> dict[str, int]:
    """The month's category totals: a business-day share of the March quarter (January to March), or the quarter's
    monthly run rate x the share of the month's business days in the feed (April, 1-14 May)."""
    keys = ("receipts", "operating_outflows", "capital", "employee_stock")
    if m in Q3_MONTHS:
        return {k: portion(Q3[k], done, len(inside), q_days) for k in keys}
    return {k: round(Q3[k] / 3 * len(inside) / len(full)) for k in keys}


def main() -> None:
    rng = random.Random(SEED)
    txns: list[dict] = []

    def add(d: date, cents: int, counterparty: str, category: str) -> None:
        if cents:
            txns.append({"date": d.isoformat(), "amount_cents": cents, "counterparty": counterparty, "category": category})

    q_days = len(business_days(month_bounds(1)[0], month_bounds(3)[1]))
    done = 0  # business days of the March quarter already allocated
    for m in (*Q3_MONTHS, *RUN_RATE_MONTHS):
        first, last = month_bounds(m)
        full = business_days(first, last)
        inside = business_days(first, min(last, END))  # the month, cut at the review date
        part = len(inside) / len(full)
        a = month_amounts(m, inside, full, done, q_days)
        done += len(inside) if m in Q3_MONTHS else 0
        payroll, legal = round(PAYROLL_MONTHLY_CENTS * part), round(LEGAL_MONTHLY_CENTS * part)
        suppliers = a["operating_outflows"] - payroll - legal
        assert suppliers > 0, (m, suppliers)
        for cp, s, cad in RECEIPT_MIX:
            for d, amt in spread(round(a["receipts"] * s), cadence_days(inside, cad), rng):
                add(d, amt, cp, "customer_receipts")
        for d, amt in spread(payroll, cadence_days(inside, "biweekly"), rng, noise=0.02):
            add(d, -amt, "Payroll", "payroll")
        add(cadence_days(inside, "monthly")[0], -legal, LEGAL_LABEL, "legal_fees")
        for cp, s, cad in SUPPLIER_MIX:
            for d, amt in spread(round(suppliers * s), cadence_days(inside, cad), rng):
                add(d, -amt, cp, "supplier_invoice")
        add(inside[len(inside) // 2], a["capital"], "Capital equipment, net of investment tax credits", "operations_other")
        add(inside[-1], a["employee_stock"], "Employee stock purchase plan", "equity_proceeds")
        # Exact monthly calibration: rounding across the mix lands on the month's last supplier payment.
        target = a["receipts"] - a["operating_outflows"]
        key = first.isoformat()[:7]
        got = sum(t["amount_cents"] for t in txns if t["date"][:7] == key
                  and t["category"] in ("customer_receipts", "payroll", "legal_fees", "supplier_invoice"))
        if target != got:
            last_supplier = max((t for t in txns if t["category"] == "supplier_invoice" and t["date"][:7] == key),
                                key=lambda t: t["date"])
            last_supplier["amount_cents"] += target - got
    for d, cents, cp, cat in DATED:
        add(d, cents, cp, cat)

    txns.sort(key=lambda t: (t["date"], -t["amount_cents"], t["counterparty"]))
    assert all(t["date"] <= END.isoformat() for t in txns)
    assert not any(t["category"] == "debt_service" for t in txns)  # no coupon falls before 15 Jun
    by_day: dict[str, int] = {}
    for t in txns:
        by_day[t["date"]] = by_day.get(t["date"], 0) + t["amount_cents"]
    balances, bal, d = [], OPENING_CENTS, START
    while d <= END:
        bal += by_day.get(d.isoformat(), 0)
        balances.append({"date": d.isoformat(), "closing_cents": bal})
        d += timedelta(days=1)
    for i, t in enumerate(txns, 1):
        t["transaction_id"] = f"txn_{i:05d}"
    feed = {
        "feed_id": "akoustis_connected_bank_20240514_v1",
        "provenance": ("Synthetic connected-bank reconstruction from filings public by 14 May 2024: January to March "
                       "match the 10-Q of 13 May 2024; April to 14 May continue the March quarter's monthly run rate "
                       "per category. Payroll, the litigation-spend proxy and vendor splits are labelled assumptions."),
        "borrower": "Akoustis Technologies, Inc.",
        "accounts": ["Operating account"],
        "restricted_cents": 0,
        "opening": {"date": "2023-12-31", "balance_cents": OPENING_CENTS},
        "period": {"start": START.isoformat(), "end": END.isoformat()},
        "transactions": txns,
        "daily_balances": balances,
    }
    OUT.write_text(json.dumps(feed, indent=1) + "\n")

    closes = {b["date"]: b["closing_cents"] for b in balances}
    assert closes["2024-03-31"] == 1_520_000_000, closes["2024-03-31"]
    for day in ("2024-03-31", "2024-04-30", "2024-05-14"):
        print(day, closes[day] / 100)
    monthly: dict[tuple[str, str], int] = {}
    for t in txns:
        monthly[(t["date"][:7], t["category"])] = monthly.get((t["date"][:7], t["category"]), 0) + t["amount_cents"]
    for (mo, cat), v in sorted(monthly.items()):
        print(mo, cat, v / 100)
    print(len(txns), "transactions")


if __name__ == "__main__":
    main()

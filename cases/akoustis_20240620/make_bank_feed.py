"""Generate Akoustis's connected-bank feed (synthetic reconstruction) from 1 Jan to 20 Jun 2024.

Slope underwrites on connected bank data. Akoustis's is not public, so this feed reconstructs it: daily,
counterparty-tagged transactions whose totals match the company's filed cash-flow figures (Decision D1 as amended; the
anchoring is `research/recent_cases/akoustis/design/CASH_CHECK_20240620.md` in the kit).

- January to March (fiscal Q3 2024, 10-Q of 13 May 2024): cash of USD 12,875k at 31 Dec 2023 and 15,200k at 31 Mar;
  customer receipts = revenue + decrease in receivables + increase in deferred revenue; operating outflows = receipts
  less operating cash flow; capital expenditure; the underwritten offering's net proceeds on its 29 Jan closing.
- April to 20 June: every flow the filings date sits on its own date (registered direct offering, net USD 9,208k on
  24 May; the cash part of the notes coupon, USD 442k, on Monday 17 Jun). The undated remainder of the April-June quarter
  (receipts, operating outflows, capital items net of tax credits, employee stock purchases: -USD 7,519k) is prorated
  by business days, 57 of the quarter's 63 falling on or before 20 Jun. These April-June figures are anchored to
  balances later reported for periods before the decision date; a connected bank feed would have shown these flows then.
- Nothing dated after 20 Jun enters the feed. In particular the USD 8.0M secured note received from a key customer on
  26 Jun 2024 is absent (a receipts classifier could tag it as customer revenue; the builder checks it is not here).

Within each month, category totals are spread over the category's cadence with seeded noise and conserved exactly.
Seeded and deterministic: `uv run python cases/akoustis_20240620/make_bank_feed.py`.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "bank_feed.json"
SEED = 20240620
START, END = date(2024, 1, 1), date(2024, 6, 20)
OPENING_CENTS = 1_287_500_000  # 31 Dec 2023, cash and restricted cash (cash-flow statement)
HOLIDAYS = {date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 5, 27), date(2024, 6, 19)}  # Fed

# Assumptions (labelled; each one constant). Payroll: cash labor on a pre-D basis. The 13 May 2024 earnings call gives
# March-quarter labor costs of USD 6.7M; less USD 0.944M stock-based compensation (10-Q of 13 May 2024) = USD 5.756M a
# quarter, about USD 1.92M a month (design/STAGE3.md, headcount item).
PAYROLL_MONTHLY_CENTS = 191_866_667
# Litigation counsel paid in cash: the year-on-year rise in professional fees and property tax in the March quarter
# (USD 1.9M a quarter, 10-Q of 13 May 2024), spread evenly by month.
LEGAL_MONTHLY_CENTS = 63_333_333
# The rest of operating outflows are vendor invoices (tagged supplier_invoice), split by these shares.
SUPPLIER_MIX = [  # (counterparty, share, cadence)
    ("Wafer and substrate suppliers (fab materials)", 0.40, "weekly"),
    ("Process gas and metals suppliers (fab materials)", 0.20, "weekly"),
    ("Tai-Saw Technology (contract manufacturing)", 0.25, "biweekly"),
    ("Other vendors (facilities, utilities, services)", 0.15, "weekly"),
]
RECEIPT_MIX = [  # RF Filters is about 63% of revenue (segment disclosure, 59.5-66.9%)
    ("RF filter customers", 0.63, "weekly"),
    ("RFMi and foundry services customers", 0.37, "weekly"),
]

# Quarter totals in cents (derived in the cash check from XBRL: 10-Q of 13 May 2024 and, for April-June, the FY2024 10-K).
QUARTERS = {
    "q3": {"months": (1, 2, 3), "receipts": 791_000_000, "operating_outflows": 1_570_100_000, "capital": -29_100_000,
           "employee_stock": 0},
    "q4": {"months": (4, 5, 6), "receipts": 639_200_000, "operating_outflows": 1_405_800_000, "capital": 13_300_000,
           "employee_stock": 1_400_000},
}
DATED = [  # flows the filings date, on their own dates
    (date(2024, 1, 29), 1_040_700_000, "Underwritten public offering, net proceeds", "equity_proceeds"),
    (date(2024, 5, 24), 920_800_000, "Registered direct offering and pre-funded warrants, net proceeds", "equity_proceeds"),
    (date(2024, 6, 17), -44_200_000, "6.0% convertible senior notes due 2027: interest, cash portion", "debt_service"),
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


def main() -> None:
    rng = random.Random(SEED)
    txns: list[dict] = []

    def add(d: date, cents: int, counterparty: str, category: str) -> None:
        if cents:
            txns.append({"date": d.isoformat(), "amount_cents": cents, "counterparty": counterparty, "category": category})

    for q in QUARTERS.values():
        q_first, q_last = month_bounds(q["months"][0])[0], month_bounds(q["months"][-1])[1]
        q_days = len(business_days(q_first, q_last))
        done = 0  # business days of the quarter already allocated
        for m in q["months"]:
            first, last = month_bounds(m)
            full = business_days(first, last)
            inside = business_days(first, min(last, END))  # the month, cut at the decision date
            if not inside:
                continue
            part = len(inside) / len(full)  # the month's own share covered by the feed
            receipts, outflows, capital, employee_stock = (
                portion(q[k], done, len(inside), q_days)
                for k in ("receipts", "operating_outflows", "capital", "employee_stock"))
            done += len(inside)
            payroll, legal = round(PAYROLL_MONTHLY_CENTS * part), round(LEGAL_MONTHLY_CENTS * part)
            suppliers = outflows - payroll - legal
            assert suppliers > 0, (m, suppliers)
            for cp, s, cad in RECEIPT_MIX:
                for d, amt in spread(round(receipts * s), cadence_days(inside, cad), rng):
                    add(d, amt, cp, "customer_receipts")
            for d, amt in spread(payroll, cadence_days(inside, "biweekly"), rng, noise=0.02):
                add(d, -amt, "Payroll", "payroll")
            add(cadence_days(inside, "monthly")[0], -legal, "Litigation counsel", "legal_fees")
            for cp, s, cad in SUPPLIER_MIX:
                for d, amt in spread(round(suppliers * s), cadence_days(inside, cad), rng):
                    add(d, -amt, cp, "supplier_invoice")
            add(inside[len(inside) // 2], capital, "Capital equipment, net of investment tax credits", "operations_other")
            add(inside[-1], employee_stock, "Employee stock purchase plan", "equity_proceeds")
            # Exact monthly calibration: rounding across the mix lands on the month's last supplier payment.
            target = receipts - outflows
            got = sum(t["amount_cents"] for t in txns if t["date"][:7] == first.isoformat()[:7]
                      and t["category"] in ("customer_receipts", "payroll", "legal_fees", "supplier_invoice"))
            if target != got:
                last_supplier = max((t for t in txns if t["category"] == "supplier_invoice"
                                     and t["date"][:7] == first.isoformat()[:7]), key=lambda t: t["date"])
                last_supplier["amount_cents"] += target - got
    for d, cents, cp, cat in DATED:
        add(d, cents, cp, cat)

    txns.sort(key=lambda t: (t["date"], -t["amount_cents"], t["counterparty"]))
    assert all(t["date"] <= END.isoformat() for t in txns)
    assert not any("note" in t["counterparty"].lower() and t["amount_cents"] > 0 for t in txns)  # no customer note
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
        "feed_id": "akoustis_connected_bank_v1",
        "provenance": ("Synthetic connected-bank reconstruction, consistent with Akoustis's filed cash-flow figures; "
                       "April to June flows are anchored to balances later reported for periods before 20 June 2024. "
                       "Payroll, litigation-counsel and vendor splits are labelled assumptions."),
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
    # Roll-forward check: add the undated remainder after 20 Jun and the 26 Jun customer note; the reported 30 Jun
    # balance (USD 24,447k) must come back.
    q4 = QUARTERS["q4"]
    residual = q4["receipts"] - q4["operating_outflows"] + q4["capital"] + q4["employee_stock"]
    in_feed = sum(t["amount_cents"] for t in txns if t["date"] >= "2024-04-01"
                  and t["category"] not in ("debt_service",) and t["counterparty"] not in {x[2] for x in DATED})
    assert closes["2024-06-20"] + (residual - in_feed) + 800_000_000 == 2_444_700_000
    for day in ("2024-03-31", "2024-05-24", "2024-06-20"):
        print(day, closes[day] / 100)
    print(len(txns), "transactions")


if __name__ == "__main__":
    main()

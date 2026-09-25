"""Generate ChromaDex's connected-bank feed (synthetic reconstruction) from 1 Jan to 19 Aug 2024.

Slope underwrites on connected bank data. ChromaDex's is not public, so this feed reconstructs it: daily,
counterparty-tagged transactions whose quarterly totals match the public 10-Q anchors (cash including restricted cash
of USD 27,325k at 31 Dec 2023, 27,565k at 31 Mar 2024, 27,885k at 30 Jun 2024; receipts equal to net sales less the
increase in receivables; the stock-option proceeds in Q2). July and August continue the second-quarter pattern and
use no later information. Seeded and deterministic: `uv run python cases/chromadex_20240819/make_bank_feed.py`.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "bank_feed.json"
SEED = 20240819
START, END = date(2024, 1, 1), date(2024, 8, 19)
OPENING_CENTS = 2_732_500_000  # 31 Dec 2023, including USD 152k restricted
RESTRICTED_CENTS = 15_200_000

# Quarterly anchors in cents: receipts = net sales - increase in trade receivables; net = change in cash.
QUARTERS = {
    1: {"months": (1, 2, 3), "receipts": 2_215_300_000 - 140_500_000, "net": 24_000_000, "other_inflows": 0},
    2: {"months": (4, 5, 6), "receipts": 2_270_000_000 - 121_400_000, "net": 32_000_000,
        "other_inflows": 59_800_000},  # stock option exercise proceeds (Q2 financing)
    3: {"months": (7, 8), "receipts": 1_180_000_000, "net": 25_000_000, "other_inflows": 0},  # Q2 daily rate x 50 days, to 19 Aug
}
RECEIPT_MIX = [  # (counterparty, category, share of receipts, cadence)
    ("Shopify Payments", "ecommerce_payouts", 0.47, "daily"),
    ("Amazon Payments", "marketplace_payouts", 0.21, "biweekly"),
    ("Retail and distributor customers", "wholesale_receipts", 0.13, "weekly"),
    ("Ingredient customers (incl. related party)", "ingredient_receipts", 0.19, "weekly"),
]
OUTFLOW_MIX = [  # (counterparty, category, share of outflows, cadence)
    ("Contract manufacturers", "contract_manufacturing", 0.19, "weekly"),
    ("W.R. Grace (NR ingredient)", "ingredient_supply", 0.10, "monthly"),
    ("Advertising platforms", "marketing", 0.30, "weekly"),
    ("Payroll (ADP)", "payroll", 0.21, "biweekly"),
    ("Landlord", "rent", 0.02, "monthly"),
    ("Litigation counsel", "legal_fees", 0.04, "monthly"),
    ("Other professional services", "professional_fees", 0.03, "monthly"),
    ("Research and clinical vendors", "research", 0.04, "monthly"),
    ("Fulfilment, freight and other", "operations_other", 0.07, "weekly"),
]


def days(month: int, last: date) -> list[date]:
    d = date(2024, month, 1)
    out = []
    while d.month == month and d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def cadence_days(ds: list[date], cadence: str, rng: random.Random) -> list[date]:
    business = [d for d in ds if d.weekday() < 5]
    if cadence == "daily":
        return business
    if cadence == "weekly":
        return [d for d in business if d.weekday() == 2]
    if cadence == "biweekly":
        return [d for d in business if d.weekday() == 4 and (d.isocalendar().week % 2 == 0)]
    return [business[min(len(business) - 1, rng.randint(8, 14))]] if business else []


def spread(total: int, when: list[date], rng: random.Random, noise: float = 0.18) -> list[tuple[date, int]]:
    if not when:
        return []
    w = [max(0.2, 1 + rng.uniform(-noise, noise)) for _ in when]
    raw = [int(total * x / sum(w)) for x in w]
    raw[-1] += total - sum(raw)  # conserve the total exactly
    return list(zip(when, raw, strict=True))


def main() -> None:
    rng = random.Random(SEED)
    txns: list[dict] = []
    for q, spec in QUARTERS.items():
        months = spec["months"]
        last = END if q == 3 else date(2024, months[-1], 28) + timedelta(days=4)
        month_days = {m: days(m, END if q == 3 else last) for m in months}
        n_days = sum(len(v) for v in month_days.values())
        receipts = spec["receipts"]
        outflows = receipts + spec["other_inflows"] - spec["net"]
        for ds in month_days.values():
            share = len(ds) / n_days
            for cp, cat, s, cad in RECEIPT_MIX:
                for d, amt in spread(int(receipts * share * s), cadence_days(ds, cad, rng), rng):
                    txns.append({"date": d.isoformat(), "amount_cents": amt, "counterparty": cp, "category": cat})
            for cp, cat, s, cad in OUTFLOW_MIX:
                for d, amt in spread(int(outflows * share * s), cadence_days(ds, cad, rng), rng):
                    txns.append({"date": d.isoformat(), "amount_cents": -amt, "counterparty": cp, "category": cat})
        if spec["other_inflows"]:
            txns.append({"date": date(2024, 5, 15).isoformat(), "amount_cents": spec["other_inflows"],
                         "counterparty": "Stock option exercises", "category": "equity_proceeds"})
        # Exact quarter-end calibration: one adjusting operating flow on the last business day of the quarter.
        q_net = sum(t["amount_cents"] for t in txns if int(t["date"][5:7]) in months)
        gap = spec["net"] - q_net
        if gap:
            last_day = max(d for ds in month_days.values() for d in ds if d.weekday() < 5)
            txns.append({"date": last_day.isoformat(), "amount_cents": gap, "counterparty": "Fulfilment, freight and other",
                         "category": "operations_other"})
    txns.sort(key=lambda t: (t["date"], -t["amount_cents"]))
    balances, bal, by_day = [], OPENING_CENTS, {}
    for t in txns:
        by_day[t["date"]] = by_day.get(t["date"], 0) + t["amount_cents"]
    d = START
    while d <= END:
        bal += by_day.get(d.isoformat(), 0)
        balances.append({"date": d.isoformat(), "closing_cents": bal})
        d += timedelta(days=1)
    for i, t in enumerate(txns, 1):
        t["transaction_id"] = f"txn_{i:05d}"
    feed = {
        "feed_id": "chromadex_connected_bank_v1",
        "provenance": "Synthetic connected-bank reconstruction, consistent with ChromaDex's public financial statements.",
        "borrower": "ChromaDex Corporation",
        "accounts": ["Operating account", "Restricted (USD 152k)"],
        "restricted_cents": RESTRICTED_CENTS,
        "opening": {"date": "2023-12-31", "balance_cents": OPENING_CENTS},
        "period": {"start": START.isoformat(), "end": END.isoformat()},
        "transactions": txns,
        "daily_balances": balances,
    }
    OUT.write_text(json.dumps(feed, indent=1) + "\n")
    closes = {b["date"]: b["closing_cents"] for b in balances}
    for q_end in ("2024-03-31", "2024-06-30", "2024-08-19"):
        print(q_end, closes[q_end] / 100)
    print(len(txns), "transactions")


if __name__ == "__main__":
    main()

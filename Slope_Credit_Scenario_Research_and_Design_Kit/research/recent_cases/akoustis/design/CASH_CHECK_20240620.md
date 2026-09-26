# Akoustis cash check: 31 Mar to 20 Jun 2024 (builder-side)

Status: builder-side check. It reads post-D filings to decide how the synthetic bank feed is anchored. **No post-D fact may be recorded in any snapshot.** D = 20 Jun 2024. Figures are in $M.

Sources:
- XBRL companyfacts, CIK 1584754.
- 10-Q for Q3 FY24, filed 13 May 2024 (acc. 0001213900-24-041897).
- 10-K for FY24, filed 8 Oct 2024 (acc. 0001213900-24-085984): cash-flow statement, Liquidity section, Notes 11, 13 and 20.
- 8-K filed 24 May 2024 (0001213900-24-046649).
- 10-Q for Q1 FY25 (0001213900-24-097800), used only to confirm the Customer Note.

## 1. Reconciliation: Q4 FY24 (1 Apr to 30 Jun) = FY24 (10-K) minus 9 months (Q3 10-Q)

| Line | Q4 amount | Date | Relative to D | Class |
|---|---:|---|---|---|
| Opening cash, 31 Mar | 15.200 | 31 Mar | — | reported |
| Operating cash flow | −8.108 | spread over the quarter | mixed | derived (−40.346 − (−32.238)) |
| – of which convertible-note coupon paid in cash | −0.442 | 15 Jun coupon date (a Saturday), so paid 17 Jun | pre-D | derived. The 10-K reports FY cash interest of 0.442; the 9-month figure was nil; the rest of the 1.32 coupon was paid in shares (0.878) |
| Investing (capex net of investment tax credit and fixed assets in accounts payable) | +0.133 | not dated | mixed, small | derived (−5.992 − (−6.125)) |
| Registered direct offering: shares | +1.934 | 24 May (closing) | pre-D | derived (12.341 − 10.407) |
| Registered direct offering: pre-funded warrants ("proceeds from exercise of warrants") | +7.274 | 24 May (prepaid at closing; the $0.001 exercise price is immaterial) | pre-D | derived (7.274 − 0) |
| Employee stock purchase plan | +0.014 | not dated | mixed, trivial | derived |
| **Customer Note (secured, non-interest-bearing, repaid through sales to a key customer)** | **+8.000** | **26 Jun 2024** | **post-D** | reported (10-K Liquidity section and Note 11) |
| ATM sales | 0 | — | — | reported. The 10-K says the ATM was suspended since May 2022, with no FY24 sales |
| Closing cash, 30 Jun | 24.447 | 30 Jun | — | reported. Ties exactly: 15.200 − 8.108 + 0.133 + 17.222 |

**The premise does not hold.** April–June burn was not near zero. Operating cash burn was about 8.1, in line with guidance. That burn was offset by 9.2 of offering proceeds (24 May) **and 8.0 from the Customer Note (26 Jun, after D)**. Q4 burn was also flattered by working-capital swings: accrued professional fees rose 1.94, meaning legal bills were deferred, and inventory fell 2.88. This matters for the feed because no working capital was being released through customer prepayments before D.

## 2. Anchoring rule

Business days use Federal Reserve holidays (Memorial Day 27 May; Juneteenth 19 Jun). Q4 has 63 business days; 57 of them fall on or before 20 Jun; 6 fall between 21 and 30 Jun.

| Option | Cash at 20 Jun | Verdict |
|---|---:|---|
| (a) D1: prorate the reported quarter's +9.247 by business days | 23.57 | **Rejected.** It moves 7.24 of the post-D Customer Note into the pre-D feed and overstates cash by about 6.4. It also spreads the 24 May offering lump across the quarter. |
| (b) Pre-D information only: dated pre-D lumps, plus the Q3 run-rate for operating + investing (−8.082 over 62 business days) | 16.54 | Honest about what was knowable before D. It is not what the bank would have shown: the burn is a forecast rather than the observed flow. Use it as a sensitivity. |
| **(c) Dated lumps on their dates, with only the undated residual prorated** | **17.16** | **Recommended.** |

**Recommended rule, replacing D1 for Akoustis:**
1. Place every flow the filings date on its own date:
   - offering +9.208 on 24 May;
   - coupon cash −0.442 on 17 Jun;
   - Customer Note +8.000 on 26 Jun. This is excluded from the pre-D feed and from every snapshot.
2. Prorate only the undated residual by business days. The residual is operating cash flow excluding the coupon, plus investing, plus the employee stock purchase plan: −7.519 for the quarter.
3. Use the reported quarter-end balance only as a check. The balance rolled forward to 30 Jun must equal 24.447 once the post-D items are added back.

This is the rule that matches what a connected bank feed would actually have shown. The bank records real dated credits and debits, so the offering is in the balance and the Customer Note is not. Only the day-to-day spread of operating flows within the quarter is estimated.

**Estimated cash at 20 Jun 2024: 17.16**, from 15.200 + 9.208 − 0.442 − 7.519 × 57/63.
- The basis is derived and estimated: dated lumps from the filings, with the residual prorated.
- Check: 17.163 − 7.519 × 6/63 + 8.000 = 24.447. ✓
- The pre-D-only sensitivity (b) gives 16.54, so the working range is 16.5–17.2.

Caveat: the Q4 operating figure used in (c) was published after D (8 Oct 2024), although it describes flows that occurred before D. If the program rule bars post-D-published figures even for pre-D flows, use (b). In either case the Customer Note stays out.

## 3. Line-limit inputs, trailing three complete months (Mar, Apr, May 2024)

Receipts per quarter = revenue + decrease in accounts receivable + change in deferred revenue:
- Q3 (Jan–Mar): 7.510 + 0.360 + 0.040 = **7.910**
- Q4 (Apr–Jun): 5.855 + 0.537 + 0 = **6.392**

Both totals are derived from XBRL. Monthly figures split each quarter by business days (Q3 has 62; March 21, April 22, May 22), so every monthly figure is estimated.

| Month | Customer receipts | Operating outflows excl. interest | Payroll (assumption) | Supplier-invoice outflows | Debt service |
|---|---:|---:|---:|---:|---:|
| Mar 2024 | 2.679 | 5.318 | 1.462 | 3.856 | 0 |
| Apr 2024 | 2.232 | 4.909 | 1.462 | 3.447 | 0 |
| May 2024 | 2.232 | 4.909 | 1.462 | 3.447 | 0 |
| Mean | **2.381** | 5.045 | | 3.583 | **0** |

How each column was built:
- **Operating outflows excluding interest** = receipts minus operating cash flow, less cash interest. The quarterly totals are Q3 15.701 and Q4 14.058, and they are derived.
- **Payroll is an assumption, not a filed figure:** 117 full-time employees (10-K, as of 30 Jun 2024) × about $150k loaded cost a year. **Supplier-invoice outflows** = operating outflows minus payroll, so they are estimated and are the weakest numbers here. Capex paid to vendors (Q3 0.291; Q4 net +0.133) is excluded.
- **Debt service** is derived from the contract terms:
  - The 6% convertible notes pay coupons on 15 Jun and 15 Dec only. The Dec 2023 coupon was paid entirely in shares.
  - The GDSI promissory note carries no interest, and its first scheduled reduction falls on 1 Jan 2025.
  - No other debt was outstanding before D.
  - So debt service is 0 for March–May. The 17 Jun cash coupon of 0.442 falls in June, outside the trailing window.

**Line limit (recommended rule): 15% × (2.381 − 0) ≈ 0.357.**

Pre-D-only variant (b): April and May receipts are taken at the Q3 per-business-day rate, 2.807 each. The mean is then 2.764, and the limit ≈ 0.415.

Feed note: the 26 Jun Customer Note is a credit from a key customer. A receipts classifier could tag it as customer revenue. It is post-D, so it must not enter any window or snapshot.

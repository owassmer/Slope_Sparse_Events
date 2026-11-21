# Akoustis (D = 20 Jun 2024): Stage 3 targeted acquisition

This closes the Stage 3 list in DECOMPOSITION §7 and checks the bounded legal slots in §1.4.

**Isolation.** Case facts come only from sources dated on or before 20 Jun 2024. General law and other cases' decisions are used regardless of date. The isolation log is at the end.

**Scratch.** Scripts, downloads and OCR output are in `~/.hermes/profiles/connor/cache/scratch/stage3_akts/`. The verdict-form text files sit next to the PDFs in `.../scratch/acq/akts/recap/`.

## Summary

| Item | Disposition | Effect on DECOMPOSITION |
|---|---|---|
| E2 D.I. 601 / 616-4 | FOUND | Q2(a) = "No". No UDTPA damages were ever asked on the trade-secret theory (J4 evidence). The $7.0M sits under a joint DTSA/NCTSPA heading with no statute named. Willful and malicious = Yes; patent willfulness = No. No number changes. |
| E16 13 May call | FOUND | June quarter: revenue $7.0–7.5M; operating burn cut a further 30% (about $5.5M); opex $10–11M a quarter. CHIPS: "$2.8 million and $4 million over the next 9 to 12 months", no date. The base of $0 stands. The $2.33M sensitivity lies within the sourced range. |
| E27 §1961 rate | FOUND | **5.14%** (WGS1YR, week ending Fri 17 May 2024). $5,435/day on $38,595,023; $1.147M from 20 May to 17 Dec. Fills §2 "Interest rates". |
| E19 proxy / 8-K | NOT FOUND (reverse split); FOUND (authorized-share increase, Nov 2023) | No vote was called by D, so A7 stays open. The Nov 2023 vote already used the §242(d)(2) votes-cast standard (evidence for ST1). About 26.3M authorized shares were unissued after the May offering. |
| Headcount | FOUND (stale) | Latest pre-D count: **222 full-time (30 Jun 2023)**. CASH_CHECK's 117 is a post-D fact. Pre-D payroll basis: about **$1.92M/month** excluding stock comp ($2.23M including it), against the assumed $1.462M. Total outflow is unchanged; only the payroll/supplier split moves. |
| E9 Bennis App. C | FOUND (one page) + reproduced | Only Schedule 54 is public (D.I. 477 p. 202, 23 Feb 2024). It reproduces Bennis's model exactly: 54-month PV to the dollar; the 55-month benefit is $66,114,092 against $66,114,093 testified. §5.2 gains a month-by-month table. The $23.1M remittitur scenario is about 21 months on the $28M-FY24 basis. |
| L8 amended judgments | (a) OPEN, bounded; (b) SETTLED | (a) The in-circuit trial authority (W.D. Pa. 2023) applied a new 30-day stay to the whole amended judgment, so the sensitivity has authority behind it. Keep the base (lender-conservative). (b) Base confirmed: *Dunn v. HOVIC* (remittitur: interest from 20 May); *Kaiser* (new trial: interest from the new judgment); *Eaves* (fees: from quantification). |
| L9 §1963; §324 | SETTLED | The *Associated Business Telephone* cite is correct. Akoustis, Inc. is a Delaware corporation, so §§169/324 reach its shares. §324 bars a sale order before final judgment, and certificated shares need seizure (§8-112). "No cash in horizon" is confirmed. |
| L12 Nasdaq "listed" | SETTLED (mechanics); bounded (indenture reading) | Suspension comes first. Delisting takes effect 10 days after the Form 25, which Nasdaq files only after the review periods lapse. Keep suspension as base and Form 25 effectiveness as the sensitivity; the date rule is in §L12. |
| L1 §24-5(b) start | SETTLED (base) | *Beach Mart* (E.D.N.C. 2021): interest runs from the original complaint even when the claim is added by amendment. Base 4 Oct 2021 ($6.58M) confirmed; the $3.21M sensitivity loses support. |
| L4 new trial and exemplary | SETTLED (base) | N.C. (*Carawan*; *Shaver*; §§1D-15(a), 1D-25(b)), DTSA (2× "the damages awarded") and *Gasoline Products* agree: a new trial on compensatory damages takes the $7.0M. Base "yes" confirmed; the "no" sensitivity can be dropped. |
| Cite: *Lightning Lube* | VERIFIED | 4 F.3d 1153, 1166 (3d Cir. 1993). Used at L5 for the JMOL standard. |
| Cite: *Associated Bus. Tel.* | VERIFIED | 128 F.R.D. 63, 66–68 (D.N.J. 1989), exactly as cited at L9. |
| Cite: DGCL §242(d)(2) | VERIFIED | Added effective 1 Aug 2023. Used at L12/ST1: votes for > votes against, if listed immediately before and holder-count listing rules are met after. |

---

## E2. D.I. 601 verdict form and D.I. 616-4 redline: FOUND

**Method.** Both PDFs are scanned images.
- Typed text: `pdftoppm -r 300 -gray` + `tesseract` 5 (`--psm 4`). Outputs: `recap/601.0.ocr.txt`, `recap/616.4.ocr.txt`.
- Handwritten answers: read visually from the 300-dpi page images (tesseract cannot read handwriting). Transcription: `recap/601.0.transcribed.txt`.
- Cross-check: every handwritten figure matches D.I. 602. That covers the 37 trade secrets, $31,315,215, $7,000,000, and $139,904 × 2 = $279,808.

**D.I. 601 (filed 17 May 2024; form stamped "5.15.2024 10:00 AM").**
- Section heading: "TRADE SECRET MISAPPROPRIATION UNDER THE DEFEND TRADE SECRETS ACT AND THE NORTH CAROLINA TRADE SECRET ACT".
- **Q1(a)** has two columns:
  - (i) "(a) this alleged trade secret qualifies as a trade secret and (b) Akoustis is liable for trade secret misappropriation";
  - (ii) "Akoustis was unjustly enriched by misappropriating this trade secret".
  - 37 of 46 rows are Yes/Yes. Five rows (5.1, 5.2, 5.3, 7.1, 7.4) are **No / Yes**: unjust enrichment found without liability. This is the "inconsistent verdict form" ground in D.I. 613 ¶1, and it is evidence for J2.
- **Q1(b)** "State the amount Akoustis was unjustly enriched…" → **$31,315,215.00**.
- **Q1(c)** "Was Akoustis's misappropriation … willful and malicious?" → **Yes**.
- **Q1(d)** "What amount of exemplary damages, if any, do you award to Qorvo against Akoustis?" → **$7,000,000.00**.
  - The question names no statute. It sits under the joint DTSA/NCTSPA heading, and D.I. 602 also says only "exemplary damages constitute $7,000,000.00".
- **Q2(a)** "Has Qorvo proven … its Unfair and Deceptive Trade Practices Act claim against Akoustis as to its confidential information and trade secret claim?" → **No**.
- **Q2(b)** (poaching) → **No**. Q2(c)–(d) (poaching damages) were left blank as instructed.
  - **No UDTPA damages question exists for the trade-secret theory.**
- **Q3(a)** all four patent claims infringed: Yes.
- **Q3(b)** '018: $139,904 (an earlier entry is scribbled out); '755: $139,904.
- **Q3(c)** willful: **No** on all four claims.

**D.I. 616-4 (Ex. D to the Elkins omnibus declaration, 17 Jun 2024).**
- Contents: the court's 5 May 2024 draft form, with Akoustis's edits sent 6 May, 10:11 AM.
- Pp. 4–8, Akoustis's redline to Q1(a):
  - It struck "Please write "YES" or "NO" in each column".
  - It inserted a threshold column: "(1) Has Qorvo established … that the alleged trade secret qualifies as a trade secret?"
  - It added: "If you answered "NO" in Column 1 … write "N/A" in Columns 2 and 3."
  - It changed Q1(b) and (c) to reach only secrets marked "Yes" in "ALL three columns".
- The final form did not adopt the separate column. It merged "qualifies" and "liable" into one column and left the enrichment column independent. That is how the No/Yes rows could arise.
- Pp. 9–11 (draft): the draft had civil conspiracy (Q2), Lanham Act (Q3), and a **single UDTPA question with its own damages line** (Q4(a)–(c): "What damages, if any, did Qorvo prove … for Akoustis' unfair and deceptive trade practices? $___").
- The final form split UDTPA into 2(a) trade secret and 2(b) poaching, and kept a damages line **only for poaching**.

**Effect.**
- **J4 / L2:** §75-16 trebles "damages … assessed" by the verdict. The jury said "No" on 2(a) and was never asked for trade-secret UDTPA damages. Add both facts, with the 616-4 draft/final contrast, to J4's record items. No number changes.
- **L3:** the $7.0M is not labelled NCTSPA punitive. The election rule is still applied on trebled paths (same conduct, same punitive purpose). This is noted as a weak point of L3, not a change.
- **L4:** caps are met under either statute.
- **J1/J2:** add the five No/Yes rows as record items.

## E16. 13 May 2024 Q3 FY24 earnings call: FOUND

**Sources.**
- Free transcripts: Insider Monkey (published 14 May 2024), https://www.insidermonkey.com/blog/akoustis-technologies-inc-nasdaqakts-q3-2024-earnings-call-transcript-1303277/. The Q&A was taken from the Exa library copy (https://exa.ai/library/markets/stocks/AKTS/earnings/FY2024/Q3/transcript), which matches Alpha Spread's text.
- Pre-D press release: 8-K Ex. 99.1, 13 May 2024 (`stage3_akts/ex991_20240513.txt`).

| Term | Quote |
|---|---|
| June-quarter revenue | Boller: "We are guiding the June quarter revenue to be flat to down 5%". Press release: "the Company expects between $7.0 to $7.5 million in sales revenue in the June quarter." |
| Burn cut | Boller: "aggressive expense reduction and cost saving measures, as well as pursuing the investment tax credits or ITCs, all of which we estimate will reduce our operating cash flow burn rate by an additional 30% sequentially in the June quarter." Q3 cash used in operations: $7.8M, so the target is about $5.5M. |
| Opex (Q&A) | Boller: "I do anticipate it coming down to the $10 million to $11 million per quarter range on OpEx, on the expense line. We have identified over $20 million in annualized savings on our OpEx." Also: "operating cash flow breakeven will be in the $11 million to $15 million of revenue per quarter … roughly 25% margin". |
| CHIPS ITC | Shealy: "the CHIPS Act of 2022 includes a provision for a 25% refundable investment tax credit or CHIPS ITC on investments in facilities that manufacture semiconductors that were placed in service after December 31, 2022. We currently estimate the amount of the refundable tax credit applicable to Akoustis to be between $2.8 million and $4 million over the next 9 to 12 months." |
| Timing | Boller: "Given the top-line projections, the current timing of the CHIPS ITC refund and the full impact of recent cost savings, we expect to be operating cash flow breakeven within the next 9 months." No refund date was given. |
| Q2 call (Feb 2024), for drift | "$3.7 million-$4.7 million over the next 12 to 15 months". The estimate fell and its window slid. |
| Labor (payroll input) | Boller: "operating loss was $22.6 million … driven by revenue of $7.5 million, offset by labor costs of $6.7 million". |

**Effect (§2 CHIPS; §6 feed).**
- **CHIPS base of $0 stands.** No date was given, and a refundable §48D credit is paid only through the elective payment made on the FY2024 (June year-end) return (26 U.S.C. §48D(d) and its elective-payment regulations). A payment before 17 Dec is unlikely.
- **Sensitivity:** the sourced rate, pro-rated over 20 Jun–17 Dec, gives $1.38M (low end, $2.8M/12 months) to $2.63M (high end, $4.0M/9 months). The existing $2.33M lies inside that range, so keep it and cite the transcript.
- **June-quarter guidance** implies an operating burn of about $5.5M. CASH_CHECK's roughly 8.1 (the D1 option (c) basis) is higher. The feed already follows option (c), so nothing changes. The guidance is the pre-D evidence for option (b).

## E27. 28 U.S.C. §1961 rate: FOUND

- **Rule:** the "weekly average 1-year constant maturity Treasury yield … for the calendar week preceding the date of the judgment". Judgment: Mon 20 May 2024, so the week is 13–17 May.
- **Value:** FRED `WGS1YR` (H.15 weekly, week ending Friday) = **5.14%**, observation dated 2024-05-17. Source: https://fred.stlouisfed.org/graph/fredgraph.csv?id=WGS1YR (`stage3_akts/WGS1YR.csv`).
- **Check:** the daily `DGS1` values for 13–17 May are 5.16, 5.16, 5.10, 5.13, 5.14, which average 5.138%.
- **Effect:** on $38,595,023 the interest is $5,435.03 a day. That is $0.168M to D and $1.147M from 20 May to 17 Dec (211 days, simple; annual compounding does not bite before 20 May 2025). This fills §2 "Interest rates" and the bond's forward interest.

## E19. Stockholder vote on a reverse split or authorized shares: NOT FOUND for a reverse split

Checked: the EDGAR submissions JSON for CIK 1584754, all filings dated 1 Jun 2023–20 Jun 2024.
- **Reverse split:** no PRE 14A, DEF 14A, DEFA14A or 8-K calls a reverse-split vote. The only statements are "could include seeking to effect a reverse stock split" (10-Q of 13 May; 424B5 of 23 May).
- **Authorized-share increase (pre-D, already done):**
  - PRE 14A (8 Sep 2023) and DEF 14A (19 Sep 2023, acc. 0001213900-23-077402), Proposal 3: 125,000,000 → 175,000,000 authorized shares.
  - Vote standard stated: "requires that the votes cast for the amendment exceed the votes cast against".
  - 8-K of 2 Nov 2023 (Items 5.03 and 5.07): approved, For 37,388,404, Against 8,919,309, no broker non-votes. The Certificate of Amendment was filed and effective 2 Nov 2023.
- **Headroom at D:** 98,654,282 shares outstanding (31 Mar 2024), plus the May offering, gives 148,654,282 (424B5). That leaves about 26.3M authorized but unissued, before options, RSUs, warrants and note shares.
- **Effect.** A7 stays a Jev question; the vote had not been called by D. ST1 gains a record item: the same electorate approved a §242(d)(2)-standard charter amendment 4.2 : 1 in Nov 2023. The "by about 18 Sep" clock is unchanged.

## Headcount and payroll: FOUND (stale count)

- **Count.** FY2023 10-K (filed 6 Sep 2023), Employees: "As of June 30, 2023, we had a total of 222 full-time employees."
  - None of the later pre-D filings gives a count: the 10-Qs of 13 Nov 2023, 13 Feb 2024 and 13 May 2024; the 13 Nov 2023 earnings 8-K (Ex. 99.1); the 13 May call.
  - The Nov 2023 release gives only "expense reductions … up to $14 million" annually. Later filings say the cuts include "decreases in research and development and headcount costs".
  - There is no stand-alone cost-reduction 8-K.
- **Isolation flag.** CASH_CHECK's payroll assumption uses "117 full-time employees (10-K, as of 30 Jun 2024)". That is a post-D fact from the FY2024 10-K, not a pre-D flow.
- **Pre-D payroll basis** (replaces headcount × $150k):
  - Q3 FY24 "labor costs of $6.7 million" (call) is $2.23M a month including stock comp.
  - Q3 FY24 stock-based compensation was $0.944M (10-Q of 13 May, stock-compensation note: R&D $132k, G&A $777k, COGS $35k).
  - **Cash labor ≈ $5.76M a quarter ≈ $1.92M a month.**
  - For comparison: Q2 FY24 labor was $8.0M, including severance ("additional payroll costs associated with our expense reduction efforts"); accrued salaries and benefits were $2.171M at 31 Mar 2024.
- **Effect.** §6 "assumed payroll of $1.462M a month" becomes **$1.92M a month (pre-D basis)**. Supplier-invoice outflows are the residual, so they fall by about $0.46M a month (Apr/May to about $2.99M). Total operating outflow and the line limit are unchanged.

## E9. Bennis Appendix C: FOUND (one page public), full schedule reproduced

**What was checked (RECAP, all entries on or before 20 Jun 2024).**
- D.I. 456/457/458: Akoustis's Daubert motion, brief and declaration, sealed. Redacted versions: D.I. 475, **476** (brief) and **477** (Elkins declaration, 327 pp., Ex. A = the Bennis report of 21 Nov 2023).
- D.I. 482/484: Qorvo's opposition and the Bennis declaration, sealed. Redacted versions: **D.I. 496** and **D.I. 498 / 498-1** ("Exhibit A (Excluded in Its Entirety)"; B = CV).
- D.I. 553 (the Daubert order); D.I. 616-1 (trial excerpts).

**Result.**
- In D.I. 477, report pp. 5–201 are full-page black redactions: 197 of 198 rendered pages are 100% dark.
- The one unredacted page is **p. 202: "Appendix C … Total Value of Akoustis Revenue as of June 30, 2024 - 54 Month Delay, Schedule 54"**.
- That page gives:
  - revenue by fiscal year, FY2016–FY2024: 254,834; 486,496; 1,208,000; 1,443,000; 1,790,000; 6,618,000; 15,350,000; 27,121,000; 59,432,573 (total 113,703,903);
  - the rate: 14.80% (Bloomberg WACC);
  - the mid-period convention, with valuation as of 30 Jun 2024;
  - the delayed PV total: **$75,750,349**.

**Reproduction.**
- Script: `stage3_akts/appendix_c.py`; output `appendix_c_reproduced.json`, months 1–55, on both revenue bases.
- The factor is (1.148)^(days from midpoint to 30 Jun 2024 / days in period), rounded to 4 d.p. as displayed on the page.
- Checks:
  - 54-month delayed PV: 75,750,349, exact.
  - Actual PV: $140.98M (testimony: "113 million today is worth 140 million").
  - **55-month benefit: $66,114,092** (testified: $66,114,093).
  - With FY2024 = $28M: $50,344,370 (testified: "50.3 million").

| Head start (months) | Report basis ($59.4M FY24) | FY24 = $28M basis |
|---|---|---|
| 6 | 9,464,854 | 7,207,995 |
| 12 | 18,181,817 | 13,844,122 |
| 18 | 26,429,860 | 20,121,343 |
| 21 | 30,281,570 | 23,055,221 |
| 22 | 31,523,796 | 24,001,982 |
| 24 | 34,019,728 | 25,900,694 |
| 30 | 41,204,229 | 31,368,977 |
| 36 | 47,811,779 | 36,398,612 |
| 48 | 59,784,839 | 45,523,880 |
| 55 | 66,114,092 | 50,344,370 |

**Effect (§5.2).** The monthly schedule is now a record-derived input: the page is pre-D public, and the reproduction matches Bennis to the dollar.
- The verdict of $31,315,215 lies between **21 and 22 months** on the report basis, and between **29 and 30 months** on the corrected-FY24 basis.
- The declared **$23.1M remittitur scenario equals about 21 months on the $28M basis** ($23.06M). Irwin's non-BAW adjustment is not modelled by month.
- Shanfield's single "22 months" item gives $31.52M (report basis) or $24.00M ($28M basis).
- Remittitur scenarios stay declared and unweighted. Code can now show any shorter period the record supports, as §5.2 anticipated.

---

## Legal confirmations (general law)

### L8. Amended judgments

**(a) Does a Rule 59(e) amended judgment restart the Rule 62(a) stay? OPEN (bounded).**
- Text: "execution on a judgment and proceedings to enforce it are stayed for 30 days after its entry".
- *Steelworkers Pension Trust v. Republic Steel*, No. 22-1198 (W.D. Pa. Feb. 2023) (Kelly, M.J.), mem. order on ECF 50. Judgment of 4 Jan 2023; amended judgment increasing the amount, 31 Jan. The creditor sought execution on the original amount on 7 Feb. The court held: "Because enforcement of the Amended Judgment is automatically stayed for 30 days under Rule 62(a), the stay remains in effect until March 2, 2023." In effect, the whole amount was stayed.
- *Office Create Corp. v. Planet Entertainment*, No. 22-cv-8848 (S.D.N.Y. Feb. 1, 2024) treated the amended judgment as carrying a new stay, but dissolved it because the amendment did not change substantive rights (*Cody v. Town of Woodbury*).
- **Effect:** this is the only in-circuit trial authority, and it supports the sensitivity (the whole amount waits 30 days). Keep the base ("only increases wait"), because earlier execution is the lender-adverse case. Label the sensitivity with *Steelworkers*.

**(b) §1961 on the amended amount: SETTLED.**
- *Dunn v. HOVIC*, 13 F.3d 58, 60–62 (3d Cir. 1993): after a remittitur, interest runs "from the date of entry of the original judgment". The court quoted: "interest on a judgment thus partially affirmed should be computed from the date of its initial entry."
- *Kaiser Aluminum & Chem. Corp. v. Bonjorno*, 494 U.S. 827, 835–36 (1990): where damages were not "ascertained in any meaningful way", interest runs from the later judgment (the new-trial case).
- *Eaves v. County of Cape May*, 239 F.3d 527 (3d Cir. 2001): interest on fees runs from the judgment that quantifies them.
- **Effect:** confirms the L8(b) base. Remitted $23.1M: interest from 20 May. New trial: from the new judgment. Trebling, pre-judgment interest and fees: from the amended judgment date.

### L9. §1963 good cause; 8 Del. C. §324: SETTLED

- **§1963.** *Associated Business Telephone Systems Corp. v. Greater Capital Corp.*, 128 F.R.D. 63, 66–68 (D.N.J. 1989) (order of 19 Oct 1989). The cite in DECOMPOSITION is correct.
  - It adopts Siegel's commentary: good cause on "a mere showing that the defendant has substantial property in the other district and insufficient in the rendering district to satisfy the judgment".
  - It adds that "the distinct possibility of plaintiff being faced with an unsatisfied judgment is sufficient 'good cause'".
  - It is followed, for example, in *Johns v. Rozet* (D.D.C. 1992) and *In re Reddy* (Bankr. E.D. Cal. 2018).
- **§324.** 8 Del. C. §324(a): "The shares of any person in any corporation … may be attached … for debt"; "**No order of sale shall be issued until after final judgment**"; certificated shares require 6 Del. C. §8-112. §169 places Delaware shares in Delaware.
  - Physical seizure of certificated shares is being litigated (*Deng v. HK Xu Ding*, Del. Super. Ct., on appeal).
  - Record: Akoustis, Inc. is "also a Delaware corporation", wholly owned by the judgment debtor (FY2023 10-K). The shares are attachable in Delaware.
- **Effect:** confirms the L9 base. The shares are a record item for A4 and yield no cash in the horizon.

### L12. When shares "cease to be listed" (mid-2024): mechanics SETTLED; indenture reading bounded

- **Indenture.** §7.01(b) is triggered when "the Common Stock is not listed on any Eligible Market". The Fundamental Change definition uses "cease to be listed or quoted on any Eligible Market" (`notes_2022_ex41`, lines 3072 and 932).
- **Nasdaq, pre-Jan-2025 rules.**
  - A Panel delist decision suspends trading. Nasdaq then files Form 25 only after the company's 15-day window to appeal to the Listing Council, and the Council's 45-day window to call the matter for review (Rule 5820), have lapsed. It files later still if the matter is reviewed (Rules 5815, 5820, 5830; see SEC Rel. 34-68053 n. 6).
  - "The Form 25, and the delisting of the security, will become effective 10 days after it is filed" (17 C.F.R. §240.12d2-2(d)(1); Nasdaq's conforming rule filing, 71 Fed. Reg. 34656 (2006)).
  - With no hearing request, suspension follows the Staff Determination and the Form 25 follows administratively.
- **Effect:** keep the §1.4 bounded split. Base: "not listed" at suspension (the lender-adverse case). Sensitivity: Form 25 effectiveness, set at Panel decision + 60 days (appeal and call windows) + 10 days where a Panel rules. With no hearing, use suspension + 10 days as a floor; the filing lag is administrative and has no rule.

### L1. §24-5(b) start when the claim appears only in an amended complaint: SETTLED (base)

- N.C. Gen. Stat. §24-5(b): "from the date the action is commenced". N.C. R. Civ. P. 3 and FRCP 3: an action is commenced by filing the complaint.
- *Beach Mart, Inc. v. L&L Wings, Inc.* (E.D.N.C. Mar. 26, 2021) (Flanagan, J.). The UDTPA claim was added by an amended complaint in Dec 2013. The court held interest runs from the 9 Sep 2011 complaint: "Defendant does not cite, nor is the court aware of, any North Carolina case holding that prejudgment interest accrues from the date the amended complaint is filed." It cites *Harris v. Scotland Neck Rescue Squad*, 75 N.C. App. 444, 452 (1985).
- **Effect:** the base of 4 Oct 2021 ($6.58M) is confirmed. The 8 Feb 2023 sensitivity ($3.21M) has no authority behind it; keep it only as a labelled stress case, or drop it.

### L4. Does a new trial on compensatory damages take the exemplary award? SETTLED (base = yes)

- **N.C.** §1D-15(a): punitive damages only if the defendant "is liable for compensatory damages". §1D-25(b) caps them by reference to "the amount of compensatory damages".
  - *Carawan v. Tate*, 304 N.C. 696 (1982): "on retrial the jury must determine first that plaintiff is entitled to recover on [compensatory damages] before it can consider plaintiff's entitlement to punitive damages".
  - *Shaver v. Monroe Constr. Co.*, 63 N.C. App. 605, 617 (1983): a new trial on punitive damages is required where the issues are "so intertwined in the minds of the jurors".
- **DTSA.** 18 U.S.C. §1836(b)(3)(C): exemplary damages are "not more than 2 times the amount of the damages awarded under subparagraph (B)". With no (B) award, there is no base.
- **Federal partial new trial.** *Gasoline Products Co. v. Champlin Ref. Co.*, 283 U.S. 494, 500 (1931): allowed only if the issue is "so distinct and separable from the others that a trial of it alone may be had without injustice".
- **Effect:** confirms the base. A remittitur Qorvo accepts keeps the $7.0M (0.30× of $23.1M, within both caps). A new trial takes it. The "no" sensitivity can be dropped.

### Citation checks

- ***Lightning Lube, Inc. v. Witco Corp.*, 4 F.3d 1153 (3d Cir. 1993): exists.** At 1166: JMOL "should be granted only if, viewing the evidence in the light most favorable to the nonmovant … there is insufficient evidence from which a jury reasonably could find liability". It also applies the "minimum quantum of evidence" test to damages. DECOMPOSITION uses it at **L5** for the JMOL standard (J1, J3). Correct.
- ***Associated Business Telephone Systems Corp. v. Greater Capital Corp.*, 128 F.R.D. 63 (D.N.J. 1989): exists; the cite is correct** (pin 66–68). DECOMPOSITION uses it at **L9** for the §1963 good-cause standard (J9).
- **DGCL §242(d)(2): exists.** Added by the 2023 amendments (SB 114, effective 1 Aug 2023).
  - Text: a combination (reverse split) or change in authorized shares may be approved if "(A) the shares of such class are listed on a national securities exchange immediately before such amendment becomes effective and meet the listing requirements … relating to the minimum number of holders immediately after", and "(B) … the votes cast for the amendment exceed the votes cast against the amendment".
  - DECOMPOSITION uses it at **L12** for the ST1 threshold. Correct. Add condition (A) as a precondition checked in code; Akoustis was still listed through the vote window.

---

## Isolation log

- **Case facts used (all dated on or before 20 Jun 2024):**
  - D.I. 601, 602, 613, 616, 616-4, 476, 477, 496, 498, 498-1;
  - FY2023 10-K; 10-Qs of 13 Nov 2023, 13 Feb 2024 and 13 May 2024;
  - 8-Ks of 13 Nov 2023, 2 Nov 2023, 13 Feb 2024, 13 May 2024 and 5 Jun 2024 (Item 5.02, director resignation; not otherwise used);
  - DEF 14A / PRE 14A of Sep 2023; 424B5 of 23 May 2024;
  - the 13 May 2024 call transcript (Insider Monkey, published 14 May 2024).
- **Not opened:** D.I. 622 and later; the FY2024 10-K; 8-Ks after 20 Jun 2024. The EDGAR submissions JSON lists later filings; only rows dated on or before 20 Jun 2024 were printed.
  - Alpha Spread's page carries an undated AI summary and a current share price. Only its transcript text was used, and it was cross-checked against the Insider Monkey copy of 14 May.
- **General law of later date, used for law only:** *Steelworkers* (2023); *Office Create* (2024); *Beach Mart* (2021); the *Deng* briefs (Del.); the Nasdaq filings describing Form 25 practice.
- **One post-D fact flagged in an existing file:** CASH_CHECK's "117 full-time employees (as of 30 Jun 2024)". See the Headcount section.

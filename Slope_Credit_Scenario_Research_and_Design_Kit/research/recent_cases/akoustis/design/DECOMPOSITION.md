# Akoustis (D = 20 Jun 2024): Stage 1 decomposition

## Summary for approval

**What the model does.** It runs Slope's reusable line for Akoustis from 20 Jun to 17 Dec 2024. The limit is about $0.357M, a manual-review line. Cash at 20 Jun is about $17.16M. Five threats can stop or shrink collections:
- Qorvo enforcing the $38.6M judgment;
- the post-trial ruling changing it;
- noteholders accelerating $44.0M on the judgment default (from 19 Aug) or on delisting (after 21 Oct);
- operating burn;
- the December coupon.

Law and the record fix the structure. Ruling dates come from data: this judge's 10 fully briefed rulings on this docket (median 61.5 days), drawn per motion from 8 Aug.

**Jev questions (26).**
- **Court:** trade-secret JMOL; damages ruling; patent JMOL; trebling; fees; pre-judgment interest; injunction; stay approved; early registration.
- **Qorvo:** execute before the ruling; accept a remittitur; enforce after the final judgment; accept a settlement.
- **Akoustis:** move for a stay; appeal; offer a settlement; respond to enforcement (pay, seek a sale or financing, file, neither); file on the notes; file at the cash floor; call the reverse-split vote; request a Nasdaq hearing.
- **Others:** stockholders approve the split; the panel grants an exception; holders act on the judgment default; holders act on delisting; holders file an involuntary petition.

**What code computes.**
- Every date and amount: rule deadlines, ruling draws, indenture and Nasdaq clocks, judgment components, interest, bond and collateral, remittitur and settlement scenarios.
- Cash, collections and preference exposure on each trajectory.
- The branches arithmetic removes: paying the judgment, a self-funded bond, and paying $44.0M.

**Decided.** Bank-feed anchoring: Decision D1 amended to option (c) (Owen, 25 Sep 2026; §6).

**Decisions for Owen.**
1. **Remittitur:** two declared, unweighted scenarios. The verdict stands ($31.3M), or it is remitted to $23.1M. $305,000 is not a scenario.
2. **Fees:** $12,116,123.30 if awarded, otherwise $0 (D4 (a)).
3. **Stay:** the bond is the full judgment plus interest and costs, with 100% collateral (80% lower bound). The stay remains a court question, although the company's own cash cannot fund it.

**Stage 3 still acquires:** OCR of D.I. 601 and 616-4; the 13 May earnings-call transcript; the H.15 rate for §1961; any pre-D proxy filing; a pre-D headcount; Bennis Appendix C, if public; checks on the bounded legal unknowns (§1.4).

---

## Scope and conventions

- **Governs:** `Slope_Model_Extensions_Spec.md` (spec), with §15 applied. This document holds only case-specific content and links to the spec for the rest.
- **Isolation:** case facts dated on or before 20 Jun 2024; general law of any date. Two post-D sources are not applied: the Nasdaq Rule 5815 change (17 Jan 2025) and the *Syntel* fee order. The feed's stated basis is in §6.
- **Labels:** each step's basis and each parameter's disposition follow spec §0 and §15.2(3). **Bounded** means an open term with a base value and a sensitivity.
- **Sources:** `D.I. n` for court filings; SEC file names in `sources_akoustis_20240620/` (`notes_2022_ex41` is the indenture); `R2a`–`R8` for `RESEARCH.md`; CASH_CHECK for `CASH_CHECK_20240620.md`.

## Corrections applied

1. **Delisting is itself an Event of Default** (§7.01(b)), with no notice and no grace period. It is also a Fundamental Change with a cash repurchase right (§10.01). This supersedes spec §1.1 and `spec_corrections.md` item 7.
2. **Interest is paid in shares unless the company elects cash** (§16.02(c)). The 17 Jun 2024 coupon was $0.442M cash and $0.878M shares (CASH_CHECK).
3. **Execution is available from 20 Jun.** Entries 1–621 hold no stay, bond or supersedeas motion (R2h).
4. **The judgment default can ripen on the 20 May judgment** (R2j, OPEN): 19 Aug at the earliest. Notice is still required. The insured-amounts exclusion is $0 (R7).
5. **Remittitur follows the maximum-recovery rule** (R2a).
6. **The fee motion does not toll the appeal clock** without a Rule 58(e) order.

---

## 1. Legal decision map

Templates are keyed by forum and instrument (spec §15.2(2)). `Lx` refers to §1.4.

### 1.1 T-A. Federal post-judgment procedure

| Node | Rule (modality) | Akoustis at D | Disposition |
|---|---|---|---|
| A1 Automatic stay | FRCP 62(a), shall, 30 days | Ended 19 Jun; no stay in entries 1–621 | Law, Record (R2h) |
| A2 Stay by security | FRCP 62(b), may; effective on court approval | No motion; judgment "in excess of our liquid assets" (`424b5_0523`) | Amount L7; approval J8 (L6) |
| A3 Renewed JMOL | FRCP 50(b), may | D.I. 607 (sealed); grounds in D.I. 613 | Law (L5); J1, J3 |
| A4 New trial or remittitur | FRCP 59(a); the plaintiff may take a new trial (*Hetzel*, 523 U.S. 208) | D.I. 613: new trial, or remit to $305,000 | Law (L5); J2, Q2 |
| A5 Alter or amend | FRCP 59(e), may | D.I. 611 trebling; D.I. 615 interest (sealed); D.I. 608 injunction | J4, J6, J7 |
| A6 Appeal clock | FRAP 4(a)(1)(A), 4(a)(4)(A) | Tolled by D.I. 607, 611, 613, 615; not by D.I. 618 | Code timing |
| A7 Appeal decided | Federal Circuit schedule | No notice before 8 Aug, so no decision by 17 Dec | Removed: impossible by schedule |
| A8 Execution | FRCP 69(a)(1), Delaware procedure, may | Delaware assets (L9) | Q1, Q3 |
| A9 Registration elsewhere | 28 U.S.C. §1963: after finality, or earlier on good cause | Operating assets in NC and NY (`10q_0513` Note 13) | Law (L9); J9 |
| A10 Trebling | N.C. Gen. Stat. §75-16, shall, once a UDTPA violation exists | Jury "No" on Q2(a) | J4 (L2); election L3 |
| A11 Fees | 18 U.S.C. §1836(b)(3)(D); §66-154(d); may | $12,116,123.30 requested (D.I. 618) | Law (R2g); J5 |
| A12 Exemplary | DTSA 2×; §1D-25(b): greater of 3× or $250,000 | $7.0M = 0.22× | Law (L4) |
| A13 Pre-judgment interest | §24-5(b), 8% (§24-1), from commencement, on "compensatory" damages | Commenced 4 Oct 2021 | J6 (L1) |
| A14 Post-judgment interest | 28 U.S.C. §1961, shall | From 20 May | Law (L8) |
| A15 Injunction | *eBay*, 547 U.S. 388; DTSA may; §66-154(a) shall; FRCP 62(c) | D.I. 608-1 | Law (L10); J7 (§5.3) |

**Entity.** D.I. 602's money sentence runs against Akoustis Technologies, Inc., and the feed is consolidated. Base: once cash is reachable, a levy reaches consolidated cash (lender-conservative).

### 1.2 T-B. Indenture (6.0% notes due 2027; New York law, §17.10)

$44.0M outstanding (`10q_0513` Note 10); guarantor Akoustis, Inc.

| Node | Rule | Akoustis at D | Disposition |
|---|---|---|---|
| B1 Judgment default | §7.01(i): final money judgments "undischarged, unpaid or unstayed" for 60 days "during which execution shall not be effectively stayed"; above $10.0M "excluding amounts covered by insurance"; only after notice by the Trustee or 25% | $38.6M; insurance none (R7); clock from 20 Jun; ripe 19 Aug | Finality: evidence for H1 (L11); clock: Code timing |
| B2 Delisting default | §7.01(b): not listed on an Eligible Market; no notice, no grace | Bid-price deadline 21 Oct | Law; date Code timing (L12) |
| B3 Cross-default | §7.01(h): other debt ≥ $2.5M | GDSI payments Jan 2025; Slope line < $2.5M | Does not reach collections |
| B4 Interest default | §7.01(c): 30 days late | No earlier than 15 Jan 2025 | After horizon |
| B5 Acceleration | §7.02: Trustee or 25% may declare; automatic on bankruptcy | $44.0M plus interest | H1, H2 |
| B6 Rescission | §7.02: majority, once all defaults are cured or waived | A judgment default is cured only by payment, discharge or a stay | Law |
| B7 Repurchase | §10.01: 100% plus interest, in cash; notice within 20 business days; repurchase 20–35 business days later; failures are defaults (§7.01(d), (f)) | Triggered by delisting | H2; dates Code timing |
| B8 Interest | §16.02: shares unless cash is elected; at most 11,403,332 shares without a vote (§9.02(k)) | $1.32M due 15 Dec, paid 16 Dec | Bounded (§2) |
| B9 Debt covenant | §5.09(viii): $25.0M unsecured basket | Slope line fits | Law |

### 1.3 T-C. Bankruptcy effects on Slope

As spec §2.3, plus:
- **Preference (§547).** Report exposure gross and also net of Slope's later draws (new value, §547(c)(4)). The ordinary-course defence (§547(c)(2)) is not computed (L13).
- **Involuntary petition (§303(b)).** Qorvo cannot file alone (L13). Noteholders can (H3).

### 1.4 Legal slots

SETTLED becomes Law. OPEN becomes evidence for the deciding actor's Jev question: the competing positions and pre-D record items. A leaning result stays OPEN. Bounded items are checked in Stage 3.

| Slot | Disposition and answer |
|---|---|
| L1 Pre-judgment interest on unjust enrichment | **OPEN → J6** (R2e). Qorvo: §66-154(b) makes unjust enrichment "actual damages", so §24-5(b) interest is mandatory. Akoustis: the jury designated "unjust enrichment"; disgorgement is restitution (*Winant*); Bennis already present-values at 14.8%, so interest counts twice. **Law:** none on exemplary; §1961 on the whole judgment; patent interest is ordinarily awarded (*Devex*, 461 U.S. 648) and is immaterial. **Bounded:** start date. Base 4 Oct 2021 ($6.58M); sensitivity 8 Feb 2023 ($3.21M) |
| L2 Trebling over the jury's "No" | **OPEN → J4** (R2f). Against: D.I. 590 (14 May 2024) holds head-start benefit is not cognizable UDTPA damages; *Winant v. Bostic*, 5 F.3d 767 (4th Cir. 1993), bars trebling restitution; no per se violation (*Legacy Data*; *Drouillard*); Qorvo filed no Rule 50 motion (*Unitherm*, 546 U.S. 394). For: unfairness is a question of law (*Hardy v. Toler*, 288 N.C. 303); *Ridgway*; *Grout Doctor* (D.I. 587) |
| L3 Election | **SETTLED → Law** (*United Labs. v. Kuykendall*, 335 N.C. 183 (1993)). Trebled $93.9M beats $38.3M, so code drops the $7.0M on trebled paths |
| L4 Exemplary caps | **SETTLED → Law** (R2b); met in every §5.2 scenario. **Bounded:** whether a new trial takes the $7.0M. Base yes; sensitivity no |
| L5 JMOL, new trial, remittitur | **SETTLED → Law.** Third Circuit law applies. JMOL only where the record lacks the minimum evidence (*Lightning Lube v. Witco*, 4 F.3d 1153 (3d Cir. 1993)). Remittitur is to the maximum the evidence supports, and the plaintiff may take a new trial (*Kazan*, 721 F.2d 911; *Hetzel*) |
| L6 Stay on lesser security | **SETTLED (standard) → Law:** good cause (*Dillon*/*Poplar Grove*; R2h). Grant: J8 |
| L7 Bond amount | **SETTLED → Law:** full judgment plus interest and costs (*Southern Track & Pump*); no local multiple |
| L8 Amended judgments | **SETTLED:** an interest motion tolls (*Osterneck*, 489 U.S. 169). **Bounded:** (a) Rule 62(a) restart: base, only increases wait 30 days; sensitivity, the whole amount. (b) §1961 start: base, 20 May on the surviving amount and the amended date on increases (*Kaiser Aluminum*, 494 U.S. 827); under $1M in the horizon. (c) D.I. 608 tolls: base yes; it moves only registration |
| L9 §1963; share attachment | **SETTLED (standard) → Law:** good cause is thin forum assets and substantial assets elsewhere (*Associated Bus. Tel. Sys. v. Greater Capital Corp.*, 128 F.R.D. 63 (D.N.J. 1989)). Grant: J9. Shares of a Delaware corporation are attachable in Delaware (8 Del. C. §§169, 324) but yield no cash in the horizon, so they are a record item for A4. **Bounded:** cash reachable before registration. Base none; sensitivity all consolidated cash |
| L10 Injunction | **SETTLED** standards (R2c); scope **OPEN → J7**; cash in §5.3 |
| L11 "Final judgment" in §7.01(i) | **OPEN → H1** (R2j), leaning yes. Holders: "unstayed" and "undischarged" fit only a judgment still open to challenge. Company: the amount can change, and it calls the final judgment still to come (`424b5_0523`). No New York case decides it |
| L12 Nasdaq; reverse split | **SETTLED → Law** (R2d, mid-2024 rules). A timely hearing request stays suspension; the panel may extend up to 180 days from the determination. DGCL §242(d)(2) (2023): votes for must exceed votes against. **Bounded:** "not listed" at suspension (base) or Form 25 (sensitivity) |
| L13 §303(b); §547(c)(2) | **SETTLED:** with 12 or more creditors, three petitioners are needed, so Qorvo cannot file alone. **Bounded:** §547(c)(2) is not computed |

---

## 2. Clocks and parameters

Draws, collections and line reassessment follow spec §2.

| Item | Value or rule | Disposition |
|---|---|---|
| Ruling date, per motion | 8 Aug (D.I. 605) plus a lag drawn from R4's 10 fully briefed matters on this docket: 17, 39, 52, 57, 58, 65, 91, 104, 146, 159 days (median 61.5). Independent per motion; sensitivity one common date. Lags above 131 days (2 of 10) land after 17 Dec | Code timing (Data) |
| Final judgment | The last order changing the judgment (FRAP 4(a)(4)(A)) | Code timing |
| New stay; appeal deadline | +30 days on increases (L8); +30 days after the last tolling order | Code timing |
| Registration in NC and NY | After the appeal deadline; earlier only on J9 | Code timing |
| §7.01(i) ripe date | 19 Aug if still unpaid and unstayed; otherwise the post-ruling judgment's enforceable date + 60 | Code timing |
| Levy lag; petition after a decision; holder notice | Base 0 days; sensitivity +30 | Bounded |
| Bond | Path judgment + accrued §1961 interest + forward interest [0, 2 years], base 1. No costs filed by D | Sourced (L7) |
| Surety collateral | 100% of the bond; lower bound 80% | Sourced (R2i); lower bound Bounded |
| Split effective by | 8 Oct; vote called by about 18 Sep (Rule 14a-6; DGCL §222) | Code timing |
| Determination; suspension | 22 Oct; +10 days with no hearing | Code timing (R2d) |
| Panel decision | Determination + [30, 60] days; base 45 | Bounded |
| Repurchase date | Latest §10.01 date (base); earliest (sensitivity) | Bounded |
| 15 Dec coupon cash | Base $0.442M (the June split); sensitivities $1.32M and $0 | Bounded |
| CHIPS credits | Base $0 in the horizon; sensitivity $2.33M prorated June–December | Bounded, pending the transcript |
| Interest rates | 8% simple from 4 Oct 2021; §1961 1-year CMT for the week before 20 May | Sourced (H.15 in Stage 3) |
| Settlement date in an interval | Interval start + 30 days; sensitivity interval end | Bounded (D5) |
| $0.10 price delisting | Future price cannot be obtained | Excluded |

---

## 3. Chains from the lender's question

Collections stop or fall short only through a petition or too little cash above need (spec §3.1).

### 3.1 T1. Qorvo enforcing the judgment

**T1-a. D to the ruling (interval I1).**
1. Execution may issue from 20 Jun (A1).
2. Qorvo decides whether to execute now: **Q1**.
3. Cash is reachable only after registration (L9 base). Early registration needs **J9**.
4. Akoustis decides whether to move for a stay: **A1**.
5. **Arithmetic:** collateral of at least $30.9M (80% of $38.6M) against about $17.2M of cash. No trajectory funds a bond, so a stay needs new money or court-approved alternative security. **J8** decides it.
6. An unstayed levy takes min(owed, reachable cash) on the levy date, whatever the operating need. Go to T1-d.

**T1-b. What the ruling decides**, asked in order, each conditional on the earlier outcomes.

| # | Component | Basis | Result |
|---|---|---|---|
| 1 | Trade-secret liability | **J1** (L5) | Vacated: $31.3M, $7.0M, fees and interest fall |
| 2 | Damages: stands, remit, new trial | **J2** | New trial: no trade-secret money in the horizon |
| 3 | Remittitur accepted | **Q2** | Accept: $23.1M (§5.2). Refuse: new trial |
| 4 | Exemplary | Law (L3, L4) | Kept; falls with liability or a new trial; dropped on trebling |
| 5 | Patent verdict | **J3** | Vacated: $279,808 falls |
| 6 | Trebling | **J4** (L2) | 3 × surviving unjust enrichment |
| 7 | Fees | **J5** | $12,116,123.30 |
| 8 | Pre-judgment interest | **J6** (L1) | 8% on surviving compensatory damages |
| 9 | Post-judgment interest | Law (L8) | Arithmetic |
| 10 | Injunction | **J7** | Stress view only (§5.3) |

**T1-c. Ruling to appeal deadline (I2).** Increases are enforceable 30 days after the amended judgment (L8). Akoustis decides whether to appeal (**A2**), which delays registration. The stay steps repeat T1-a steps 4–5 on the path amount.

**T1-d. Enforceable, unstayed and unpaid (I1–I3).**
1. **Arithmetic:** "pay" exists only where the path amount is within the path's maximum cash. Every path with surviving trade-secret money excludes it.
2. Akoustis responds: **A4** (Choice). The options come from what the record says the company can do: equity, debt, real-estate or equipment financing, collaborations or licensing, a sale or other strategic transaction, and restructuring or insolvency (`10q_0513` Note 2 and risk factors; `424b5_0523` risk factors).
   - **Pay:** only where step 1 allows.
   - **Seek a sale or new financing:** every financing and transaction route. Its effect is timing only: the petition decision is deferred to the next interval, and A4 is asked again at the next milestone on the path (ruling, appeal deadline, levy, ripe default date or τ). No cash is booked, because the record gives no terms.
   - **File:** restructuring or insolvency. Petition date per §2.
   - **Neither:** A4 is not asked again. Later petitions come only through A5 or A6.
3. Qorvo decides whether to enforce: **Q3**. The levy follows T1-a step 6.

**Settlement.** In each interval (I1 before the ruling, I2 before the appeal deadline, I3 unstayed, I4 stayed), Akoustis decides whether to offer (**A3**) and Qorvo whether to accept (**Q4**). The amount follows §5.4. A paid settlement removes the §7.01(i) trigger.

### 3.2 T2. The notes

**Judgment default.** It ripens on 19 Aug if the judgment is still unpaid and unstayed, and again on the post-ruling judgment. At each ripe date the holders decide whether to give notice and accelerate: **H1**. Paying $44.0M is impossible (Arithmetic). Akoustis decides whether to file (**A5**); if it does not, the holders decide whether to file (**H3**).

**Delisting.** The board decides whether to call the reverse-split vote in time (**A7**), and the stockholders whether to approve (**ST1**). If the stock is not compliant on 21 Oct, Akoustis decides whether to request a hearing (**A8**), and the panel whether to grant an exception (**N1**). On delisting, an Event of Default exists at once. The holders accelerate, require repurchase only, or do neither (**H2**). Then A5 and H3.

**Cross-default** does not reach collections (B3).

### 3.3 T3 and T5. Operating cash

Operating flows come from the feed (§6). Dispute cash comes from T1, and legal spend continues until the dispute ends on the path. **τ** is the first date available cash falls below need (spec §2.2; sensitivity: below zero). At τ, Akoustis decides whether to file: **A6**. New financing is not booked as cash.

### 3.4 T4. The coupon

The cash share is in §2. It reaches collections only through cash above need on 16–17 Dec. No Jev.

---

## 4. Jev residual questions (26)

Each question is one actor's decision. None asks about timing, an amount, affordability, enforceability or legal meaning. There are no outside-view anchors (spec §15.1). Standard path facts follow spec §3.3. Code multiplies the answers along each path in the order of §3.

**Merits rulings (J1–J3).** Jev receives the public grounds in D.I. 613 and the D.I. 616 exhibits (616-1 to 616-4). The briefs (D.I. 607 and every supporting and answering brief) are sealed at D. The borrower's cash is deliberately withheld, because a court does not weigh solvency on the merits. J4–J6 are rulings on legal remedies and receive no cash either.

**Evidence routing.** Present-state readings are evidence handed to a question. They are never its probability.
- The `event_*` readings (judgment entered, stay secured, appeal filed, paid, amount fixed) set the stage: judgment entered 20 May, no stay, no appeal, unpaid. Code uses them for structure.
- `bears_on_appeal_intent` and `bears_on_appeal_barred` feed A2.
- `bears_on_settlement_signals` feeds A3 and Q4.
- `bears_on_debtor_resistance` feeds A1, A4, Q1 and Q3.
- `bears_on_debtor_liquidity` is not asked: it is scoped to a paying counterparty, and Akoustis is the payer. A4, A5 and A6 get Akoustis's cash as path facts from the bank data and the simulation.
- `bears_on_amount_finality` feeds J2 and Q2.

| Id | Actor | Decision | Asked when |
|---|---|---|---|
| J1 `forecast_ts_liability_jmol` | Court | Grants JMOL on trade-secret liability | Ruling in horizon |
| J2 `forecast_ts_damages_ruling` | Court | Stands / remit / new trial (Choice) | J1 = survives |
| J3 `forecast_patent_jmol` | Court | Sets aside the patent verdict | Ruling in horizon |
| J4 `forecast_trebling` | Court | Grants D.I. 611 | Trade-secret money survives |
| J5 `forecast_fees_awarded` | Court | Awards fees | Trade-secret money survives |
| J6 `forecast_prejudgment_interest` | Court | Awards interest on unjust enrichment | Trade-secret money survives |
| J7 `forecast_injunction_ts` | Court | Enters the trade-secret injunction | Ruling in horizon |
| J8 `forecast_stay_approved` | Court | Approves security and stays execution | A1 = moves |
| J9 `forecast_1963_good_cause` | Court | Orders early registration | Q1 or Q3 = acts before the appeal deadline |
| Q1 `forecast_execution_pending_motions` | Qorvo | Executes before the ruling | I1 |
| Q2 `forecast_remittitur_accepted` | Qorvo | Accepts $23.1M over a new trial | J2 = remit |
| Q3 `forecast_enforcement_after_final` | Qorvo | Enforces the final judgment | Enforceable, unstayed, unpaid |
| Q4 `forecast_settlement_accept` | Qorvo | Accepts the offered terms | A3 = offers |
| A1 `forecast_stay_motion` | Akoustis | Moves for a stay | Q1 or Q3 = acts, or final judgment |
| A2 `forecast_appeal` | Akoustis | Appeals within 30 days | Money award survives |
| A3 `forecast_settlement_offer` | Akoustis | Offers terms within its bound | Each interval |
| A4 `forecast_debtor_response` | Akoustis | Pay / seek a sale or financing / file / neither (Choice; "pay" only where arithmetic allows) | T1-d, and again after "seek" |
| A5 `forecast_petition_on_notes` | Akoustis | Files after acceleration or unpaid repurchase | H1 or H2 = acts |
| A6 `forecast_petition_cash_floor` | Akoustis | Files at the cash floor | τ in horizon |
| A7 `forecast_reverse_split_board` | Board | Calls the vote in time | No earlier petition |
| A8 `forecast_nasdaq_hearing` | Akoustis | Requests a hearing | Not compliant on 21 Oct |
| ST1 `forecast_split_approved` | Stockholders | Approve the split | A7 = yes |
| N1 `forecast_panel_exception` | Nasdaq panel | Grants an exception past 17 Dec | A8 = yes |
| H1 `forecast_holders_act_judgment` | Holders ≥25% | Give §7.01(i) notice and accelerate | Each ripe date |
| H2 `forecast_holders_act_delisting` | Holders | Accelerate / repurchase only / neither (Choice) | Delisted in horizon |
| H3 `forecast_holders_involuntary` | Noteholders | File an involuntary petition | Accelerated, unpaid, no voluntary petition |

Record items for each question are the ones cited at its step in §1 and §3. Surety willingness is not a separate question: no trajectory self-funds a bond, and J8 gates every other route.

---

## 5. Amounts

### 5.1 Components

| Component | At D | Rule on the path |
|---|---|---|
| Unjust enrichment | $31,315,215 (D.I. 602) | Kept, vacated (J1), retried (J2) or remitted (J2, Q2) |
| Exemplary | $7,000,000 | Kept; falls with liability or a new trial (L4); dropped on election (L3) |
| Patent | $279,808 | Kept or vacated (J3) |
| Trebling | Requested $93,945,645 (D.I. 611) | 3 × surviving unjust enrichment if J4 |
| Fees | Requested $12,116,123.30 (D.I. 618) | J5: the requested amount, labelled (D4 (a)); otherwise $0. The *Syntel* ratio post-dates D |
| Pre-judgment interest | Sealed (D.I. 615) | J6: 8% on the untrebled compensatory amount (L1) |
| Post-judgment interest | Statutory | §1961 (L8) |
| Costs; enhanced patent damages | Not filed by 17 Jun | Removed: cannot be obtained; window closed |

Path totals: verdict with fees and interest $57.3M; remitted $47.4M; trebled $112.9M; base judgment $38,595,023.

### 5.2 Remittitur (Decision D4)

Under the maximum-recovery rule (R2a), the court remits to the most the evidence supports, and Qorvo may take a new trial (Q2). The defence's $305,000 is a minimum, so it is not a remittitur scenario.

| Scenario | Amount | Record basis |
|---|---|---|
| No remittitur | $31,315,215 | Verdict (D.I. 602) |
| Remitted and accepted | $23.1M | Bennis's method with Irwin's revenue corrections: 54% off $50.3M, or 65% off $66.1M (D.I. 616-1, tr. 1698–1700, 2346–47) |

Declared scenarios, shown side by side, never weighted. If Stage 3 obtains Bennis Appendix C (R5), code adds the value at any shorter head-start period the record supports.

### 5.3 Injunction (R8)

- **Patent part:** reaches only legacy versions of the 19 parts. The redesigns were released on 22 May (`8k_0522_ex991`); disputes over them go to contempt proceedings, not into the horizon.
- **Trade-secret part:** not tied to part numbers. The only bound is the RF Filters segment, 59.5–66.9% of revenue, which also holds unaccused RFMi products.
- **Result:** the cash effect cannot be obtained pre-D and is excluded from the weighted view. One stress scenario: where J7 = granted, RF Filters receipts (about 63%) fall to zero from the order date.

### 5.4 Settlement (Decision D5)

The feasibility bound is available cash minus 30-day need, capped at the path amount. Two declared scenarios: a lump sum at the bound, or monthly payments to the bound through 17 Dec. Non-cash terms are not booked.

---

## 6. Bank feed and Slope's line

**DECIDED (Owen, 25 Sep 2026): Decision D1 amended to option (c) for Akoustis** (CASH_CHECK §2).
- Dated flows sit on their own dates: offering +$9.208M on 24 May; coupon cash −$0.442M on 17 Jun.
- The undated April–June remainder (−$7.519M) is prorated by business days to 20 Jun (57 of 63).
- **The $8.0M customer note of 26 Jun is excluded from every snapshot and feed.** A receipts classifier could tag it as revenue, so the builder checks for it.
- **Cash at 20 Jun: about $17.16M.** Rolling forward with the post-D items reproduces the reported $24.447M at 30 Jun.
- Stated basis: the April–June figures come from the FY2024 10-K and are used only for pre-D flows.
- Rejected alternative: option (b), strictly pre-D figures ($16.54M).

**Line limit: about $0.357M** = 15% × ($2.381M mean monthly receipts, March–May − $0 debt service). It exceeds Slope's $250k automatic approval, so it is a manual-review line.

**Supplier-invoice outflows** (about $3.58M a month) depend on an assumed payroll of $1.462M a month and are labelled as estimates.

---

## 7. Evidence requirements (the Stage 2 list)

**Resolved:** no stay or bond (R2h); ruling pace (R4); damages figures (R5); insurance none (R7); part-level revenue not obtainable, segment bound only (R8); feed anchors and line inputs (CASH_CHECK). The earlier "Have" items (D.I. 602, 605, 608-1, 611, 613, 616-1, 618; the indenture; the 10-Q, 8-Ks and 424Bs) stand.

**Add to the snapshot (all pre-D):**
- D.I. 590 (14 May 2024), the Rule 50(a) UDTPA damages order; D.I. 587 (May 2024), Qorvo's UDTPA brief; D.I. 616-2 and 616-3 (17 Jun).
- R4 pace entries, closing filing → ruling: D.I. 15 (15 Dec 2021) and D.I. 47 (1 Apr 2022) → D.I. 67 (10 May 2022); D.I. 112 (7 Oct 2022) → D.I. 152 (15 Mar 2023); D.I. 224 (2 Jun 2023) and D.I. 273 (15 Aug 2023) → D.I. 313 (1 Sep 2023); D.I. 398 (10 Nov 2023) → D.I. 470 (22 Feb 2024); D.I. 487 and 488 (27 Feb 2024) → D.I. 557 (2 May) and D.I. 545 (25 Apr); D.I. 491, 492 and 494 (4 Mar 2024) → D.I. 553 (30 Apr) and D.I. 546 (25 Apr).
- The docket report, entries 1–621, truncated at 20 Jun. It confirms filing on 4 Oct 2021. Exclude D.I. 730, a post-D artifact.

**True gaps (Stage 3):**
- OCR of D.I. 601 (verdict form) and D.I. 616-4 pp. 4–13 (redline): J4, J6, L3, L4.
- The 13 May 2024 earnings-call transcript: burn cut, CHIPS timing.
- H.15 1-year CMT for the week before 20 May 2024: §1961.
- Any PRE 14A or DEF 14A filed by 20 Jun: A7.
- A pre-D headcount (FY2023 10-K): payroll.
- Bennis Appendix C, if public: §5.2.
- Checks on the bounded legal unknowns in L1, L4, L8, L9 and L12.

---

## 8. Contract changes (differences from spec §6 and §10 only)

**`dispute_model.json` 4.0.0**
- `templates`: `federal_post_judgment` (A1–A15), `indenture_convertible_2027` (B1–B9), `bankruptcy_effects`; each node carries authority, modality, trigger, conditions, exceptions and dispositioned `legal_unknowns`.
- `parameters` replace `rules`, with §2's dispositions; `ruling_lag_days` holds the 10 R4 values.
- `removed_branches` with reasons: appeal decided in the horizon, costs, enhanced patent damages, a self-funded bond, paying the notes.
- `remittitur_scenarios` and `settlement_scenarios`, declared and unweighted.
- `feed_anchor`: `dated_lumps_plus_prorated_residual`, excluding the 26 Jun note.
- Lender outputs gain preference exposure net of new value.

**`question_registry.json` 4.0.0:** the 26 ids in §4. `forecast_debtor_response` becomes Choice {pay, seek_sale_or_financing, file, neither}. Removed: `forecast_surety_bond`, `forecast_holders_notice_early` (merged into H1), `forecast_exemplary_review`, the `outside_view` profile. `forecast_stay_alt_security` becomes `forecast_stay_approved`. A run makes about 55–80 asks, inside the 250-attempt budget.

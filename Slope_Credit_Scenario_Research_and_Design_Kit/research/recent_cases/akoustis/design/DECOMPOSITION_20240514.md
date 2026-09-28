# Pending money claim at jury trial: decomposition for the 14 May 2024 review (Akoustis case inputs)

## 1. Summary for approval

<!-- summary: written last, after §2–§8 -->

---

## Scope and conventions

- **Governs:** `Slope_Model_Extensions_Spec.md` §16, with §0, §2, §3 and §15 applied. §16 governs where it differs from §1–15. This document holds the event model's legal map, clocks, chains, amounts and questions. The common financial model (opening cash, operating outlook, financing, the operating reserve, legal spend, loan accounting) is §16.3 and belongs to another worker; this document only names the settings it reads.
- **Structure and discipline** follow the 20 Jun decomposition (`DECOMPOSITION.md`): stage order of spec §15.2(1), bases per spec §0 (**Law**, **Record**, **Data**, **Arithmetic**, **Jev**), and dispositions per §15.2(3) (sourced, **Bounded** open term with a base and a sensitivity, **Code timing**, or a Jev question). `Lx` are the legal slots of §2.5. `Jx` (court or jury), `Cx` (claimant), `Dx` (debtor) and `Hx` (holders) are the Jev questions of §6; `B1`–`B10` are indenture nodes.
- **Generic first.** Every node is a template keyed by forum and instrument. The borrower, the claimant, the claims, the court, the instrument and every date are case inputs. The Akoustis column shows how the case fills the template and is never read by code.
- **Isolation.** Case facts come only from sources dated on or before 14 May 2024: the 10-Q and earnings release of 13 May (`akts_2024q3_10q`, `akts_2024_05_13_*`), the FY2023 10-K, the 2022 notes 8-K and indenture (`notes_2022_ex41`), the 8-Ks of 27 Oct 2023 and 29 Jan 2024, the docket through 14 May, D.I. 15–590, D.I. 580 (the blank final verdict form of 9 May, fetched from RECAP for this document), and the research worker's pre-cutoff findings in `acquisition_akoustis_20240514.md` (D.I. 476, 535, 543 and 543-1; the 13 May earnings call; the resale S-3 of 13 May). General law is used whatever its date. From the 20 Jun documents only their settled general law is reused (§2.5). The isolation log is at the end.
- **Sources:** `D.I. n` for court filings; SEC file names as in the kit folder; ACQ for `acquisition_akoustis_20240514.md`; `DECOMPOSITION.md` §n for the 20 Jun document; `R2x`, `R4` for `RESEARCH.md`.

---

## 2. Legal decision map

The map has four templates. Each node gives its rule and modality, trigger, condition and disposition. The Akoustis entries show the case inputs.

### 2.1 T-P. Pending federal civil money claim at jury trial (`pending_money_claim`, forum `court`)

The template starts where the 4.0.0 template (`federal_post_judgment`) assumed a judgment already existed. It runs verdict → judgment → the 30-day automatic stay → post-trial motions → enforcement or stay → registration, and it hands dated cash effects to the same Chain steps. **A missing judgment never activates enforcement:** every enforcement, stay, registration and judgment-default node has the entered money judgment on the path as its trigger, and no such node exists on a path whose verdict leaves no money judgment.

| Node | Rule (modality) | Trigger and condition | Akoustis at 14 May | Disposition |
|---|---|---|---|---|
| P1 Verdict | Jury finds each claim by a preponderance (Seventh Amendment; FRCP 48, unanimous unless stipulated) (may) | Trial under way, claims submitted on a special verdict form | Trial began 6 May; day 7 on 14 May (minute entries); jurors provided for through Fri 17 May (D.I. 550, 26 Apr); the claimant rested on 13 May and the case is "expected to go to the jury before the end of the week" with a verdict "in the next several days" (13 May call, ACQ §1(g)); special verdict form D.I. 580 (9 May) with five claim groups: trade secrets under the DTSA and the NCTSPA (unjust enrichment, willful and malicious, exemplary), civil conspiracy, Lanham Act false advertising, UDTPA, patent infringement | Verdict date: Code timing, a window drawn per trajectory (§3). Outcome: **J1**, the damages theory the jury adopts (§5, §6) |
| P2 Claims removed before verdict | FRCP 50(a) (may); summary judgment FRCP 56 | A ruling before the verdict | RICO and false patent marking out on summary judgment (D.I. 545; 10-Q Note 14). Patent validity decided for the claimant (D.I. 557). No compensable UDTPA damages may go to the jury (D.I. 590, 14 May) | Record |
| P3 Damages | The jury fixes the amount per claim on the form (D.I. 580 Q1(b), 1(d), 2(c), 3(b), 4(c), 5(b)) | A liability finding with a damages answer | **The claimant itemised its claim publicly** (D.I. 543-1 Ex. A.2, 22 Apr 2024; ACQ §1): trade-secret unjust enrichment "at least $66.1 million" (a 55-month head start, ¶31); patent $279,808 (¶19); corrective advertising $1,146,604 (¶42); poaching $809,772 (¶49). The UDTPA and conspiracy $66.1M figures (¶¶50, 56) are the same money under other theories, not additive. No defense figure is public (D.I. 543-1 Ex. A.4 gives none; the $305k avoided-cost figure first appears at trial). Defense counsel: "if the jury adopts the theories of [the claimant]'s experts, it is possible the jury will issue an eight-figure verdict" (13 May call) | Theory: **J1**. Amount per theory: Record and Arithmetic (§5); never Jev |
| P4 Exemplary and enhanced relief | DTSA exemplary ≤ 2× (18 U.S.C. §1836(b)(3)(C)); N.C. punitive cap (§1D-25(b)); patent enhancement ≤ 3× on willfulness (35 U.S.C. §284) (may); fees (§1836(b)(3)(D), §66-154(d), 35 U.S.C. §285) (may); pre-judgment interest (§24-5(b); *Devex*); UDTPA trebling (§75-16) (shall, once damages are assessed) | A finding that opens the remedy | D.I. 580 asks exemplary damages (Q1(d)) and willfulness (Q5(c)). D.I. 590 removes UDTPA damages, so §75-16 has no UDTPA amount to treble (L15) | One bounded term on the claimant's-theory branch (§5.2): each only raises an award already beyond cash, which changes no date or cash on any trajectory. Not a branch |
| P5 Judgment entry | FRCP 58(b)(2): on a special verdict "the court must promptly approve the form of the judgment, which the clerk must promptly enter" (shall) | A verdict with a money award | — | Code timing, Bounded (L14) |
| P6 Automatic stay | FRCP 62(a): execution stayed 30 days after entry (shall) | Entry | — | Law |
| P7 Post-trial motions | FRCP 50(b), 52(b), 59(b), 59(e): within 28 days of entry (may); FRAP 4(a)(4)(A) tolls the appeal clock to the order disposing of the last of them; they do not stay execution (2018 Advisory Committee Note to Rule 62) | A money judgment the debtor contests | The debtor made two Rule 50(a) motions at the close of the claimant's case (D.I. 590), which preserves a Rule 50(b) motion | Filing: **D1**. Ruling date: Code timing (Data, R4). Content: **J2**, compacted (§4.6) |
| P8 Execution | FRCP 69(a)(1), state procedure (may) | Entered, past the Rule 62(a) stay, unstayed, unpaid | Delaware forum; operating assets in NC and NY (L9) | **C1** |
| P9 Stay by security | FRCP 62(b): "a party may obtain a stay by providing a bond or other security", effective on approval (may) | Any time after entry | — | Full bond: Law, a stay as of right (L16). Lesser security: **J3** (L6). Motion: **D4** |
| P10 Registration elsewhere | 28 U.S.C. §1963: after finality, or earlier "for good cause shown" (may) | The creditor acts before finality | — | Law (L9); **J4** |
| P11 Appeal | FRAP 4(a)(1)(A): 30 days after the order disposing of the last tolling motion (shall) | A money award survives the ruling | — | **D5**. Decided in the horizon: removed (window closed, as `DECOMPOSITION.md` A7) |
| P12 Post-judgment interest | 28 U.S.C. §1961 from entry (shall) | Entry | — | Law; rate Bounded (L8) |
| P13 Injunction | eBay four factors; DTSA may; §66-154(a) shall; FRCP 62(c) | A liability finding and a motion | No proposed order in the record by 14 May | Excluded from cash: its revenue effect cannot be obtained (R8: segment bound only) |
| P14 Settlement | Parties' agreement (may); a paid settlement releases the claim | Any interval before a petition | No disclosed talks; the debtor's own E.D. Tex. suit and two IPR petitions against its patent (10-Q Note 14) | **D3** × **C2** per interval (§4.1) |

**Entity.** The claims run against the parent and its operating subsidiary jointly (D.I. 580, "Akoustis" defined as both). As in `DECOMPOSITION.md` §1.1, a levy that reaches cash reaches consolidated cash.

### 2.2 T-B. The indenture's triggers (`indenture_convertible`, reused)

The 4.0.0 template and its node set (`DECOMPOSITION.md` §1.2, B1–B9) stand. The instrument's terms are case inputs quoted by the agent (`instantiate_financing`). Akoustis: $44.0M of 6.0% convertible senior notes due 2027, New York law (§17.10), guarantor Akoustis, Inc. (10-Q Note 10; `notes_2022_ex41`).

| Node | Rule (modality) | Trigger and condition | What 14 May changes | Disposition |
|---|---|---|---|---|
| B1 Judgment default | §7.01(i): final money judgments "undischarged, unpaid or unstayed" for 60 days "during which execution shall not be effectively stayed"; aggregate above $10.0M "excluding amounts covered by insurance"; only "after notice to the Company by the Trustee or the Holders of at least 25%" (shall, on notice) | An entered money judgment above the threshold on the path, not paid, not effectively stayed at the ripe date | The judgment does not exist yet. Its amount is set by the verdict branch (§5): the defense's theory stays below $10.0M and never triggers B1; the claimant's theory exceeds it. Insurance: $0 (R7 applies at 14 May: the 10-Q and FY2023 10-K disclose none) | Threshold and notice: Law. Which judgment starts the 60 days: **OPEN, both readings carried as the recorded model has them (L11)**. Clock: Code timing from the modeled dates. Notice: **H1** |
| B2 Delisting default | §7.01(b): "the Common Stock is not listed on any Eligible Market"; no notice, no grace (shall) | "Not listed" (L12) inside the horizon | Only one route falls inside the horizon (§2.3) | Law; date Code timing |
| B3 Cross-default | §7.01(h): other debt of $2.5M or more accelerated or in payment default | The GDSI seller note's partial prepayment falls in Jan 2025 (10-Q) | After the horizon on every trajectory | Removed (window closed) |
| B4 Interest default | §7.01(c): 30 days late | The 15 Jun 2024 coupon is inside the horizon; a default could come no earlier than 15 Jul | The coupon is §16.3's (below). Whether the company pays it is not a dispute decision | Not a separate node: nonpayment is inside the company's distress decisions (D2, D7, D8, D9) |
| B5 Acceleration | §7.02: the Trustee or 25% "may" declare; automatic on a bankruptcy petition | An Event of Default | — | **H1**, **H2** |
| B6 Rescission | §7.02: majority, once every default is cured or waived; a judgment default is cured by payment, discharge or a stay | After acceleration | — | Law |
| B7 Repurchase | §10.01: holders' put at 100% plus interest on a Fundamental Change (delisting); repurchase 20–35 business days after the company's notice, itself due within 20 business days | Delisting | The only in-horizon delisting falls on 1 Nov (§2.3); the repurchase date falls after 10 Nov on every trajectory | Removed (window closed). H2 becomes binary |
| B8 Interest | §16.02: $44.0M × 6.0% ÷ 2 = **$1.32M due 15 Jun 2024** (a Saturday; paid Mon 17 Jun), inside the horizon; 15 Dec after it (2022 notes 8-K). Paid in shares unless the company elects cash (§16.02(c)), valued at 95% of the ten-day VWAP; §9.02(k) caps shares at 11,403,332 without a stockholder vote | The coupon date | Pre-cutoff share facts only (ACQ §4.2): the resale S-3 of 13 May registers 5,000,000 "Note Shares" for interest and make-whole payments; 175,000,000 authorized and 98,669,282 outstanding at 8 May (10-Q); close $0.60 on 1 May. At $0.60, $1.32M needs about 2.3M shares, inside the 5.0M registered and the 11.4M cap; the registered shares cover the whole coupon at any VWAP of $0.278 or more. The 20 Jun values ($0.20 price, 3.0M capacity) are post-cutoff and are not used | **Bounded** (`coupon_cash_share`, new case values): base all shares, $0 cash (§16.02(c) default, and the 13 May S-3 shows the company preparing to pay in stock); sensitivity all cash ($1.32M on 17 Jun). Booked on every path, event or not (spec §16.3 "Ordinary obligations") |
| B9 Debt covenant | §5.09(viii): $25.0M unsecured basket | The line | — | Law: the Slope line fits |
| B10 Holders' own petition | §7.06 (Limitation on Suits): no holder may institute a proceeding unless it has made a written request to the Trustee and 60 days have passed (shall); §7.07 | Accelerated, unpaid, issuer has not filed | Acceleration on the entered-judgment reading falls about 19 Aug, so a §7.06 petition can fall in the horizon (about 18 Oct). On the post-ruling reading, and after the 1 Nov delisting, it cannot | Bounded route (`holder_petition_route`), reused. **H3** only where it can fall inside the horizon |

### 2.3 T-L. The Nasdaq bid-price process (`listing`, part of the indenture template)

Facts public by 14 May: the deficiency notice of 24 Oct 2023 (Rule 5550(a)(2); 8-K 27 Oct 2023); the first 180 days ended 22 Apr 2024; a second 180-day period was requested and granted, to **21 Oct 2024**; the company "could" seek a reverse split; the stock closed at $0.60 on 1 May (10-Q Note 12 and risk factors).

| Node | Rule (mid-2024 rules, R2d; `DECOMPOSITION.md` L12) | Date on the path | Disposition |
|---|---|---|---|
| N1 Compliance | $1.00 closing bid for 10 consecutive business days ending by 21 Oct; the usual cure is a reverse split (DGCL §242; §242(d)(2) votes-cast standard) | Split effective by 7 Oct; vote called about 17 Sep | Collapsed into **D6** (§4.6) |
| N2 Staff Determination | Rule 5810: issued when the second period ends uncured | 22 Oct | Code timing |
| N3 Hearing request | Rule 5815(a)(1)(B): a timely request (within 7 days) "ordinarily stays the suspension" until the Panel's written decision | By 29 Oct | Collapsed into **D6** |
| N4 Suspension without a hearing | Rule 5815: suspension follows the Determination | 1 Nov (Determination + 10 days, `suspension_after_determination_days`) | Code timing. "Not listed" at suspension (L12 base) |
| N5 Panel decision | Rule 5815(c)(1)(A): up to 180 days from the Determination | Determination + [30, 60] days: 21 Nov at the earliest | **Removed** (window closed): after 10 Nov on every trajectory |
| N6 Form 25 | 17 C.F.R. §240.12d2-2(d)(1): delisting effective 10 days after filing | No earlier than 11 Nov | The L12 sensitivity (Form 25) therefore removes delisting from the horizon entirely |

**Result (Law and Code timing).** Inside the horizon the stock stops being listed only if it is not compliant on 21 Oct **and** no hearing is requested by 29 Oct. A hearing request is the company's own act and suffices whatever the stockholders did, so the whole listing chain in the horizon is one company decision (**D6**).

### 2.4 T-C. Bankruptcy effects on the line (`bankruptcy_effects`, reused)

As spec §2.3 and `DECOMPOSITION.md` §1.3:
- **Petition.** Collections stop on the petition date (11 U.S.C. §362(a)); operating flows continue; the outstanding balance is a stayed claim with recovery a typed unknown, never zero.
- **Preference.** Collections in `[p − 90, p)` are preference-exposed (§547(b)(4)(A)), reported gross and net of later draws (§547(c)(4)). §547(c)(2) is not computed (L13).
- **Involuntary petition.** With 12 or more creditors, three petitioners are needed (§303(b)(1)); the claimant cannot file alone (L13). Noteholders can (**H3**).
- **The claimant after a petition** becomes an unsecured creditor; its levy is stayed. That is Law and enters the creditor's questions as a standard, not as a node.

### 2.5 Legal slots: what the 20 Jun design settled, and what 14 May changes

SETTLED becomes Law. OPEN becomes evidence for the deciding actor's question, with the competing positions. Bounded is an open term with a base and a sensitivity. "Reused" means the 20 Jun slot's general-law answer applies unchanged; "changed" says what the earlier date does to it.

| Slot | 14 May status | Disposition and answer |
|---|---|---|
| L1 Pre-judgment interest on unjust enrichment (§24-5(b)) | Changed | Reused law (R2e, `STAGE3.md` L1). It only raises an award: inside the claimant's-theory bounded term (§5.2). No node |
| L2 Trebling (§75-16) | Changed | D.I. 590 (14 May) holds that "the jury has no reasonable basis to find compensable damages under the UDTPA". §75-16 trebles "the amount fixed by the verdict", so a verdict can carry no UDTPA amount to treble (L15). No trebling branch. No node |
| L3 Election between treble and punitive damages (*Kuykendall*) | Changed | Moot without trebling (L2). No node |
| L4 Exemplary caps (§1D-25(b); DTSA 2×); a new trial takes the exemplary award | Changed | Amount only: inside the bounded term (§5.2). The rule that exemplary damages fall with the compensatory award (*Carawan*; `STAGE3.md` L4) is Law inside J2's "set aside" |
| L5 JMOL, new trial, remittitur (*Lightning Lube*; *Roebuck*; *Gumbs*; *Kazan*; *Hetzel*) | Reused | SETTLED standards → Law; they are J2's standard. Remittitur into a lower class is not a branch (§4.6) |
| L6 Stay on lesser security (*Dillon*/*Poplar Grove*) | Reused | SETTLED standard; the grant is **J3**, asked only where no trajectory funds a full bond |
| L7 Bond amount (*Southern Track & Pump*) | Reused | SETTLED: the full judgment plus interest and costs; no D. Del. multiple. Collateral 100% (80% lower bound) as `DECOMPOSITION.md` §2 |
| L8 Amended judgments; §1961 | Changed | (a) the Rule 62(a) restart on an increase: no increase is modeled (trebling is out, L2; fees and interest sit in the bounded term), so it does not arise. (b) §1961 runs from the modeled entry date on the branch amount. The rate for the week before the modeled entry is not published by 14 May: **Bounded** base, the latest published weekly 1-year CMT at 14 May (H.15, week ending 10 May 2024); sensitivity none needed (on the defense-theory amounts it moves under $0.1M in the horizon; beyond cash it moves nothing) |
| L9 §1963 good cause; share attachment | Reused | SETTLED standard (*Associated Bus. Tel.*); grant is **J4**. Base: no cash reachable before registration where it sits; sensitivity all consolidated cash |
| L10 Injunction | Changed | No proposed order exists by 14 May and no part-level revenue is disclosed (R8). Its cash effect cannot be obtained: excluded from cash, not asked |
| L11 "Final judgment" in §7.01(i) | Reused as recorded | **OPEN** (R2j; New York law; no New York decision). Both readings carried, as the recorded model has them (`judgment_default_reading` = both): **entered reading**, the 60 days run from the end of the Rule 62(a) stay of the judgment as entered (modeled entry + 30 + 60); **post-ruling reading**, from the order disposing of the last pending tolling motion (modeled ruling + 60), on the amount that survives. Sensitivities: entered only; post-ruling only |
| L12 Nasdaq hearing stay; "not listed" | Reused, consequence changed | SETTLED (mid-2024 rules): a timely hearing request stays suspension; the Panel may extend up to 180 days. Bounded: "not listed" at suspension (base) or on Form 25 effectiveness (sensitivity). At 14 May every Panel decision and every Form 25 falls after the horizon (§2.3) |
| L13 §303(b); §547(c)(2) | Reused | SETTLED as `DECOMPOSITION.md` L13 |
| L14 Judgment entry after a special verdict | New | FRCP 58(b)(2): "the court must promptly approve the form of the judgment, which the clerk must promptly enter". **Bounded:** base, entry on the first business day after the modeled verdict (the earliest reading of "promptly", lender-adverse because every enforcement clock starts earlier); sensitivity, entry deferred until the court rules on the post-verdict equitable remedies, dated as a post-trial ruling (§3). No pre-verdict record sets it |
| L15 UDTPA damages at the verdict | New | SETTLED by the court's own order: D.I. 590 grants the Rule 50(a) motion as to damages. The UDTPA claim can yield a liability answer without an amount (D.I. 580 Q4) |
| L16 Stay on a full bond | New | SETTLED: a debtor who posts a sufficient supersedeas bond obtains a stay as of right (*American Mfrs. Mut. Ins. Co. v. American Broadcasting-Paramount Theatres, Inc.*, 87 S. Ct. 1, 3 (1966) (Harlan, J., in chambers); FRCP 62(b), 2018: "a party may obtain a stay by providing a bond"). So approval of a fully collateralized bond is Law, not a question; J3 is asked only for lesser security |
| L17 Post-trial motion deadlines and effect | New | SETTLED: Rules 50(b), 52(b), 59(b) and 59(e) motions are due 28 days after entry; timely ones toll the appeal clock (FRAP 4(a)(4)(A)); they do not stay execution (Rule 62, 2018 Advisory Committee Note; R2h). A fee motion (Rule 54(d)(2), 14 days) tolls only on a Rule 58(e) order |
| L18 After a verdict with no money award | New | SETTLED in effect for the horizon: a claimant's Rule 59 new trial or Rule 50(b) motion after a defense verdict can yield money only after a retrial or a separate damages determination, which cannot fall by 10 Nov: the motion is ruled on no earlier than entry + 28 + 21 days plus the court's measured lag (§3), and this court last set a trial twelve months out (D.I. 198, 10 May 2023, setting 6 May 2024). No money judgment on any trajectory: the claimant's post-trial questions are not asked |

---

## 3. Clocks and parameters

Every clock after the verdict runs from the **modeled** verdict and entry dates on each trajectory, never from what later happened. Dates below are given as the range over the verdict window; code computes them per trajectory. Review date 14 May 2024; horizon 180 days, to 10 Nov 2024 (spec §16.2). Draws, collections and the line follow spec §2.

| Item | Rule and base value | Disposition |
|---|---|---|
| Verdict date V | Drawn per trajectory, uniformly over the court days **Thu 16 May to Wed 22 May 2024** (16, 17, 20, 21, 22 May). Basis, all dated on or before 14 May: the claimant rested on 13 May and the case is "expected to go to the jury before the end of the week, and the jury is expected to issue a verdict in the next several days" (13 May call, ACQ §1(g)); jurors provided for through Fri 17 May (D.I. 550, filed 26 Apr 2024: "furnish lunch for 14 jurors ... from Monday, May 6, 2024 through Friday, May 17, 2024"); trial day 7 on 14 May (minute entry). the parties' proposal of "no more than 25 hours per side" (D.I. 543 n.3, 22 Apr) puts the close of evidence at about the ninth to eleventh court day, 16–20 May (ACQ §2, inference); the window runs from the earliest of those days to three court days after Friday, which covers "the next several days" | Code timing (Record). Draw key `(instance, verdict, date)`, so no probability moves it. Sensitivity: the window's last day |
| Judgment entry E | FRCP 58(b)(2), "promptly" (L14). Base **V + 1 business day: 17–23 May**. Sensitivity: entry deferred to the court's ruling on the post-verdict equitable remedies, dated like the post-trial ruling below | Bounded (`judgment_entry`) |
| Execution available | FRCP 62(a): 30 days after entry. **17–23 Jun** | Law |
| Post-trial motions filed | L17: within 28 days of entry. Base: on the deadline, **14–20 Jun** | Code timing (Law deadline) |
| Briefing closes | D. Del. LR 7.1.2(b): answering brief 14 days after the opening brief, reply 7 days later. Base **5–11 Jul** (filing + 21). No stipulated schedule exists before the verdict | Code timing (Law); the existing `briefing_days_new_motion` (21) |
| Post-trial ruling | Briefing close + one lag drawn from the judge's 10 fully briefed rulings on this docket (R4: 17, 39, 52, 57, 58, 65, 91, 104, 146, 159 days; all ruled by 2 May 2024, so the sample is pre-14-May). One common lag for all the post-trial motions, because which motions will be filed is unknown before the verdict. Ruling 22 Jul to 17 Dec; **8 of 10 lags land inside the horizon** for every verdict day | Code timing (Data). Sensitivity: the later of two independent draws (several motions decided separately) |
| Appeal deadline | FRAP 4(a)(4)(A): ruling + 30 | Code timing (Law) |
| Stay approval | Motion + 21 days (LR 7.1.2) + a lag draw (R4). The motion may come "at any time after judgment is entered" (Rule 62(b)), so its day is the debtor's decision day (§4.1), at the earliest E | Code timing (Data) |
| Early registration order (§1963) | The creditor's motion + 21 days + a lag draw (R4) | Code timing (Data) |
| Levy | Registration order (or appeal deadline + 1 day, when final and unappealed) + `levy_lag_days` (base 0, sensitivity 30) | Code timing; lag Bounded (reused) |
| §7.01(i) ripe dates (L11) | Entered reading: execution available + 60 days = **16–22 Aug**. Post-ruling reading: ruling + 60 (inside the horizon on 5 or 6 of the 10 lags, depending on the verdict day; otherwise after it). Each only if the branch amount is above $10.0M and the judgment is unpaid and not effectively stayed that day | Code timing; reading Bounded (`judgment_default_reading` = both, reused) |
| Holders' notice and acceleration | Ripe date + `holder_notice_lag_days` (base 0, sensitivity 30) | Bounded (reused) |
| Holders' own petition | §7.06: acceleration + 60 days (base); sensitivity at acceleration (§7.07). Entered reading: **15–21 Oct**, inside. Post-ruling reading and delisting: after 10 Nov on every trajectory | Bounded (`holder_petition_route`, reused) |
| Listing | Deadline 21 Oct; Determination 22 Oct; hearing request by 29 Oct; suspension without a hearing **1 Nov**; Panel decision ≥ 21 Nov (after the horizon) | Code timing (Law, §2.3); "not listed" Bounded (L12, reused) |
| Notes coupon | $1.32M, 15 Jun 2024 (paid 17 Jun), inside; 15 Dec after the horizon. Base all shares ($0 cash); sensitivity all cash (B8) | Bounded (`coupon_cash_share`, new case values). A common input booked on every path (§16.3), specified here because its terms are the indenture's |
| Settlement payment date | Interval start + 30 days (base); sensitivity the interval's end | Bounded (`settlement_date_in_interval`, reused) |
| Petition after a decision to file | `petition_lag_days` base 0, sensitivity 30 | Bounded (reused) |
| Bond and collateral | Bond = class amount + accrued §1961 interest + one year's forward interest (L7); collateral 100% of the bond, lower bound 80% (R2i) | Sourced (reused) |
| §1961 rate | Latest weekly 1-year CMT published by 14 May (week ending 10 May 2024) (L8) | Bounded; the value is fetched from H.15 by the implementer |
| Branch amounts | §5: the claimant's theory at the claimed amount; the defense's theory a bounded amount below $10.0M; enhancements one bounded term | Record and Arithmetic; bounded terms with sensitivities |
| **Operating reserve** | 30 days of operating need, one alternative (spec §16.3). The event model reads it in four places: the settlement amount, the reduced-security offer, whether a class is payable (§5), and the cash floor | §16.3 setting, owned by the common-model worker |
| **Financing** | An explicit amount-and-date scenario shared by every path; central case adds none the record does not fix (spec §16.3). The 13 May re-activation of the at-the-market program ($48.0M remaining; "the sales agents are under no obligation to make any sales", 10-Q) is a known channel that fixes no amount or date, so it books nothing centrally. Completion is worker A's shared scenario input, not a question in this tree: the debtor's "seek a sale or financing" branch books no cash of its own and inherits whatever A's scenario books | §16.3 setting, owned by the common-model worker (A) |
| Legal spend | Attributable spend stops when the dispute ends on the path (spec §16.3). The event model supplies the end date: payment or a paid settlement. A verdict of either kind, or a ruling that sets a judgment aside, does not end the dispute inside the horizon (post-trial motions, an appeal or a retrial follow, L18), so spend continues. Whether spend after the trial runs at the trial-period level is the §16.3 worker's proxy question, flagged to it | §16.3 rule; end dates from the event model |

---

## 4. Chains from the lender's question

Slope's collections stop at a petition (Law: §362) or fall short when cash above the 30-day need is too low on a due date (Arithmetic: spec §2.2). Only chains that reach one of the two are walked.

### 4.0 What can stop or shrink collections between 14 May and 10 Nov 2024

| # | Threat | Basis |
|---|---|---|
| T1 | A money verdict, the judgment on it, and its enforcement: a levy on cash, cash locked as stay security, or a petition in response | Record: trial under way (10-Q Note 14); the claimed amounts (D.I. 543-1); an adverse judgment could lead the company to seek "protection by filing a voluntary petition" (10-Q Note 2). Law: FRCP 58, 62, 69; §1963 |
| T2 | The notes: acceleration of $44.0M on a judgment default above $10.0M, then a petition by the issuer or the holders | Record: indenture §§7.01(i), 7.02, 7.06 |
| T3 | Delisting on suspension (about 1 Nov), an Event of Default without notice | Record: 10-Q Note 12; indenture §7.01(b). Law: Nasdaq Rule 5815 |
| T4 | Operating cash reaching the 30-day need, then zero, including the June coupon if paid in cash | Data: the 14 May feed (§16.3). Record: going-concern doubt; cash "into the third quarter of fiscal 2025" absent a judgment (10-Q Note 2) |
| T5 | Settlement: a payment that ends the claim but draws cash | Law: a paid settlement releases the claim. Arithmetic: bounded by cash above the reserve |

### 4.1 T1. The verdict and its enforcement

Intervals: **I0** review date to verdict; **I1** entry to the post-trial ruling; **I2** after the ruling, unstayed; **I3** stayed on approved security. The debtor's response `D2` is asked at each consequential milestone (entry, a levy, a ripe judgment default) in the situation of that day.

**I0. Before the verdict (14 May to V).**
1. Settlement before the verdict: **D3 × C2** on the terms of §4.5, paid at 13 Jun (interval start + 30; sensitivity V).
2. The verdict on V (Code timing, §3): **J1**, the damages theory the jury adopts. Three branches, cut at the mechanism thresholds (§5):
   - **no award**: no money judgment on any claim;
   - **defense theory**: an award below $10.0M (bounded amount, §5.2);
   - **claimant's theory**: the claimed $68,336,184, beyond cash on every trajectory (enhancements one bounded term, §5.2).
3. **No award** → no judgment exists, so no enforcement, stay, registration or judgment-default node exists on the path (the template's rule, §2.1). The claimant's own post-trial motions cannot yield money by 10 Nov (L18). The path goes to T3 and T4; legal spend continues (§3).
4. A money award → judgment entered at E = V + 1 business day (L14), in the branch amount.

**At entry E.**
5. **Arithmetic** (spec §0: only impossibility removes a branch): "pay" exists where the amount owed is within available cash on some trajectory of the path at the decision; a full bond exists where cash above the reserve covers its collateral on some trajectory (the approved 20 Jun stay rule, `DECOMPOSITION.md` decision 3). On the claimant's-theory branch neither exists.
6. **D2** at entry: pay, file, or continue (operate and contest). "Continue" includes seeking a sale or new financing, which books no cash of its own; any financing is worker A's shared scenario (§3). Pay ends the dispute on the payment day (`resolve`); file books a petition at E + `petition_lag_days`.
7. **D1**, on "continue": the debtor files timely post-trial motions (by E + 28). Yes → I1 and a ruling date (§3). No → the judgment is final at entry, the appeal deadline is E + 30, and the path goes to I2 without a ruling.

**I1. Entry to the post-trial ruling.**
8. Settlement in I1: **D3 × C2**, paid at E + 30.
9. **C1**, the creditor executes before the ruling, from E + 31 (situation: motions pending). Asked only where a levy can move cash before any stay approval (`Forecaster.moves_cash`).
10. **D4 × J3**, on execution: the debtor moves for a stay; where no trajectory funds a full bond, the court decides a stay on reduced security, the debtor's cash above the reserve on the approval day, locked if approved (**J3**, L6). Where a full bond is fundable, posting it stays execution as of right (L16) and J3 is not asked.
11. **J4**: cash is reachable before finality only after registration where it sits (L9), which needs good cause.
12. On the levy day, before the levy: **D2** (levy). Then the levy takes min(owed, reachable cash) (Arithmetic).
13. On the claimant's-theory branch only, the notes' judgment default on the entered reading ripens 16–22 Aug (T2).

**The post-trial ruling.**
14. **J2** (binary): the judgment **stands**, or is **set aside** (JMOL on liability, or a new trial; no money judgment on the path by 10 Nov). A remittitur the claimant accepts is inside "stands"; one it refuses is a new trial (*Hetzel*), inside "set aside" (§4.6). "Set aside" releases any stay security (`release_lock`) and ends enforcement.

**I2. After the ruling (or from entry where D1 = no), unstayed.**
15. Settlement in I2: **D3 × C2**, paid at ruling + 30.
16. **D5**, the debtor appeals within 30 days, asked where a levy after the appeal deadline can fall inside the horizon. Unappealed, registration follows the appeal deadline (Law, §1963); appealed, it needs **J4**.
17. **D4 × J3** again where no stay is in place: a stay pending appeal on the path amount.
18. **C1** (situation: after the ruling; appealed or final), then J4 where appealed, then **D2** on the levy day, then the levy.
19. On the claimant's-theory branch, the post-ruling judgment default ripens at ruling + 60 where the holders did not act on the entered reading (T2).

**I3. Stayed on approved security.** Settlement in I3: **D3 × C2**. The locked security stays out of available cash until the dispute ends. A stay in effect before a ripe date means no judgment default that day (§7.01(i), "effectively stayed").

### 4.2 T2. The notes

Only the claimant's-theory branch crosses $10.0M, so the judgment default exists only there (Arithmetic on §5). Which judgment starts the 60 days is open, and both readings are carried as the recorded model has them (L11).
1. **Entered reading:** ripe 16–22 Aug where the judgment is unpaid and not effectively stayed. **H1**: the holders give notice and accelerate. Then **D9**: the issuer files on acceleration; if not, **H3**: the holders file once §7.06 allows (acceleration + 60 days, 15–21 Oct, inside); if not, the notes stay due and unpaid.
2. **Post-ruling reading:** ripe at ruling + 60 where J2 = stands and the holders did not act at the entered date; H1 is asked there in that situation, then D9. H3 falls after 10 Nov on every trajectory, so it is not asked (window closed).
3. **D2** (ripe) is asked where the default ripens and the debtor has so far continued: the day tells it the notes may be accelerated.
4. Paying $44.0M is impossible on every trajectory (Arithmetic), so no "pay the notes" branch exists. An acceleration before the listing decision ends the listing chain (the notes are already due).

### 4.3 T3. The listing

At 14 May every Panel decision and every Form 25 falls after 10 Nov (§2.3). Inside the horizon the stock stops being listed only if it is not compliant on 21 Oct and no hearing is requested by 29 Oct. The hearing request is the company's own act, and a timely one stays suspension whatever the stockholders did (Law, Rule 5815). So the chain is one decision:
1. **D6**, asked on 29 Oct where no earlier petition or acceleration exists: the company keeps the stock listed through 10 Nov (a reverse split effective by 7 Oct, or a timely hearing request).
2. No → suspended 1 Nov, an Event of Default (§7.01(b)). **H2**: the holders accelerate or not (the repurchase date falls after 10 Nov, so "repurchase only" is cash-identical to "neither" and merges with it). On acceleration, **D9**; H3 falls after the horizon (1 Nov + 60).

### 4.4 T4. Operating cash

Operating flows come from the common model (§16.3): the 14 May feed, the operating outlook, the financing scenario, the coupon (B8). Event cash comes from T1 and T5. **τ** is the first day available cash falls below the 30-day need; **D7**: the company files at τ. If it keeps operating, **D8**: it files when cash first falls below zero. Both reuse `petition_cash_floor` and `petition_cash_out`. They are also the only questions in the ordinary-operating-risk attribution run (the event given no cash effect, spec §16.1), asked there with that run's facts (`bank_state`).

### 4.5 Settlement terms

Settlement is its own decision on stated terms (spec §16.4), asked once per interval where the terms exist:
- **Offer (D3):** the debtor offers a lump sum equal to its available cash above the 30-day reserve on the payment date, capped at the amount owed; in I0, where nothing is owed yet, capped at the claimed $68,336,184. It exists only where that amount is positive on some trajectory (the approved 20 Jun rule, `DECOMPOSITION.md` §5.4). Sensitivity: the same amount in monthly payments to the horizon.
- **Acceptance (C2):** the claimant accepts those terms. The offered amount (P5/P50 on the payment date) is a path fact, and each interval and branch is its own node, so an acceptance probability is never reused for a different offer.
- A paid settlement releases the claim and the stay security, removes the §7.01(i) trigger, and ends attributable legal spend.

### 4.6 What is collapsed, and why

Each distinction below changes no payment timing, cash, receipts, financing access or another actor's material decision inside the horizon, or it changes them only inside a branch that is already beyond cash.

| Collapsed | Into | Basis |
|---|---|---|
| Liability per claim (trade secrets, conspiracy, Lanham Act, UDTPA, patent) | J1's three theories | Only the trade-secret head-start measure can reach $10.0M or exceed cash; the other claims total $2,236,184 at most (D.I. 543-1) and sit inside the defense-theory amount. Conspiracy and UDTPA restate the same $66.1M (¶¶50, 56) |
| Any award of $10.0M or more | The claimant's-theory branch | Beyond cash on every trajectory: pay, a full bond, the levy (all reachable cash) and the notes default are identical. The merged-class test (§7) checks it |
| Exemplary, enhanced, trebled damages; fees; pre-judgment interest | One bounded term on that branch (§5.2) | They only raise an award already beyond cash. Trebling cannot arise (D.I. 590, L2) |
| Remittitur; the claimant's election after it | J2's two branches | Accepted above $10.0M: cash-identical to "stands". Refused: a new trial, inside "set aside". Accepted below $10.0M: inside "stands" (lender-adverse). In the 20 Jun run the whole damages ruling moved collections by about $2.2k |
| The separate merits motions (JMOL, new trial, patent JMOL, fees, interest, injunction) | One ruling date and J2 | One common lag (§3); only "any money judgment survives" moves cash |
| Seek a sale or financing vs neither | "Continue" in D2 | Neither books cash (financing completion is worker A's scenario); D2 is re-asked at every consequential milestone anyway |
| Board vote call, stockholder approval, hearing request, Panel exception | D6 | The Panel decision falls after 10 Nov; the hearing request alone decides in-horizon listing |
| Repurchase after delisting | H2 binary | Repurchase date after 10 Nov (§2.2 B7) |
| The appeal's outcome | Removed | Cannot be decided by 10 Nov (window closed) |
| Injunction | Excluded from cash | Its revenue effect cannot be obtained (R8); no proposed order by 14 May |
| Surety willingness | J3 | No trajectory funds a bond on the claimant's branch; J3 gates the reduced-security route |

---

## 5. Amounts

Amounts are code-owned: record figures, arithmetic on them, and bounded terms with sensitivities. Jev never sets an amount (spec §0, §16.4).

### 5.1 What the record fixes before the verdict (ACQ §1; slot filled by the research worker)

| Claim | Claimed amount (D.I. 543-1 Ex. A.2, 22 Apr 2024) | Treatment |
|---|---|---|
| Trade secrets (DTSA, NCTSPA), unjust enrichment on a 55-month head start | "at least $66.1 million" (¶31) | The claimant's-theory amount; the record figure is $66,100,000 |
| UDTPA unjust enrichment; civil conspiracy | $66.1 million each (¶¶50, 56) | The same money under other theories: not additive. UDTPA damages are out in any case (D.I. 590) |
| Patent ('018, '755) | $279,808 (¶19) | Added |
| Lanham Act corrective advertising | $1,146,604 (¶42) | Added |
| UDTPA poaching | $809,772 (¶49) | Added. D.I. 590 removed compensable UDTPA damages from the jury; kept here, lender-adverse, because it moves no mechanism (it sits inside a branch already beyond cash, or inside the defense-theory bound) |
| Exemplary, punitive, enhanced, treble damages; fees; interest | Requested, never quantified (¶¶32, 57, 61–62; D.I. 535 Q4 proposes exemplary up to 3× the trade-secret damages) | The bounded term below |
| The defense's figure | Not public before the verdict (D.I. 543-1 Ex. A.4 gives none; the $305k avoided-cost figure first appears at trial) | The defense-theory bound below |
| Defense counsel, 13 May call | "if the jury adopts the theories of [the claimant]'s experts, it is possible the jury will issue an eight-figure verdict" | Read as: the claimant's theories give $10M or more; the defense's give less. **Inference**, labelled |

### 5.2 The branch amounts

| J1 branch | Amount | Disposition |
|---|---|---|
| No award | $0; no judgment | Record (the branch's definition) |
| Defense theory | Bounded `defense_theory_amount`. **Base $9,999,999**, the most an award can be and stay below eight figures (the record's upper bound, as approved Decision D4(a)). **Sensitivity $2,236,184**, the claimant's own itemised claims outside the head-start measure (patent, corrective advertising, poaching), the only record figures on this branch. The two sit either side of the one mechanism threshold on this branch, whether the judgment is payable from cash above the reserve | Bounded. **Open decision O1** (§1): which setting is the base |
| Claimant's theory | **$68,336,184** = $66,100,000 + $279,808 + $1,146,604 + $809,772 (Arithmetic on the record). Plus the bounded `claimant_enhancements` term: base $0 added; sensitivity DTSA exemplary at the 2× cap ($132,200,000) and §24-5(b) interest at 8% from 4 Oct 2021 to entry (about $13.9M), about $214.4M in all. Fees have no public figure and are left out; they would only raise the same amount | Record and Arithmetic; enhancements Bounded. Beyond cash on every trajectory under both settings, so the setting changes no date and no cash (checked, §7.5). Jev is told the range, never one figure |

Post-judgment interest (§1961) accrues on the branch amount from E at the L8 rate. The bond is the branch amount plus accrued and one year's forward interest; collateral 100% (80% lower bound) (L7).

### 5.3 Where the mechanism thresholds fall (Arithmetic, checked on the built feed)

- **$10.0M (§7.01(i)).** Crossed only by the claimant's theory. The defense-theory base is set one dollar below it, so no reading of the bound triggers the notes.
- **Cash above the reserve.** At 31 Mar cash was $15.2M (10-Q); the June-quarter operating burn is guided at about $5.5M (13 May call, `STAGE3.md` E16). So at entry cash above the reserve is of the order of $8–11M: the defense-theory base ($9,999,999) is payable on few or no trajectories and the sensitivity ($2,236,184) on all. The implementer reports both shares from the §16.3 feed; if both settings fall on the same side on every trajectory, one of them needs no run.
- **A compromise award between $10.0M and cash above the reserve** has no record figure. It is payable only where cash above the reserve exceeds $10.0M. The implementer reports the share of trajectories where it does; if none, the band is empty and nothing is lost. If some, it is **open decision O2**: add a declared scenario class for it (spec §16.4, "where no figure is public"), run on its own.

### 5.4 Settlement and stay security

Both are the approved 20 Jun rules (`DECOMPOSITION.md` decisions 3 and §5.4), reading the §16.3 reserve: available cash above the 30-day need, capped at the amount owed (the claimed amount before the verdict). Neither is a Jev amount.

---

## 6. The Jev questions

Eighteen questions, each one actor's decision. None asks about timing, an amount, affordability, enforceability or legal meaning. Every question receives the standard state (spec §3.4; registry `state_contract`): the case, the question with its branches and situation, the governing standard, its record items (§8), the path facts code computed, the assumptions that hold, the evidence, and the readings routed to it. Material factors are established first, and aggregation receives the factor results and the structured facts, not the whole record again (spec §16.4).

**Cash facts and the engine (spec §16.4).** A question marked **⚑** receives cash facts: available cash, the 30-day need, the amount owed, collateral, the offered amount, and the dated triggers, at its decision date. Those facts must equal the engine's state on that date, including the line's draws and collections to that date (§7.2). **⚑⚑** marks the questions where the line's own flows can move a structural threshold, not just a figure: whether "pay" is feasible, and the day τ at which the cash-floor questions arise. Questions without a mark are court or jury decisions on the merits or the law and receive no cash, because solvency is not their standard (as `DECOMPOSITION.md` §4).

### 6.1 The list

| Id | Registry id | Actor | Decision (branches) | Asked where | Facts |
|---|---|---|---|---|---|
| J1 | `forecast_verdict_theory` (new) | Jury | Which damages theory it adopts (no award / defense theory / claimant's theory) | Once, before the verdict, where no settlement was paid | The claims, the claimed amounts, the verdict form; no cash |
| J2 | `forecast_post_trial_ruling` (new) | Court | The money judgment stands or is set aside (JMOL or new trial) | Each money branch where D1 = yes and the ruling falls inside the horizon on some trajectory | The branch amount and the preserved grounds; no cash |
| J3 | `forecast_stay_approved` (reused) | Court | Approves reduced security and stays execution (yes / no) | D4 = yes and no trajectory funds a full bond | ⚑ the offered security on the approval day, the bond, the collateral |
| J4 | `forecast_1963_good_cause` (reused) | Court | Orders registration before finality (yes / no) | C1 = yes before finality | ⚑ stay status, the amount owed |
| C1 | `forecast_execution_pending_motions` in I1; `forecast_enforcement_after_final` in I2 (both reused) | Claimant | Enforces the unpaid, unstayed judgment (yes / no) | Where a levy can move cash before any stay approval | ⚑ owed, reachable cash, the petition's effect on its position (Law) |
| C2 | `forecast_settlement_accept` (reused) | Claimant | Accepts the offered terms (yes / no) | D3 = yes, per interval and branch | ⚑ the offered amount (P5/P50 on the payment date), owed |
| D1 | `forecast_post_trial_motions` (new) | Debtor | Files timely post-trial motions (yes / no) | Each money branch, after D2 at entry = continue | The branch amount, the preserved grounds |
| D2 | `forecast_judgment_response` (new; the 4.0.0 `forecast_debtor_response` stays for the recorded run) | Debtor | Pays, files, or continues | At entry; on each levy day before the levy; on a ripe judgment default | ⚑⚑ owed, cash, the reserve, dated triggers (the ripe dates, the coupon) |
| D3 | `forecast_settlement_offer` (reused; new context I0) | Debtor | Offers the §4.5 terms (yes / no) | Each interval where the offered amount is positive on some trajectory | ⚑ the offered amount, owed, cash, dated triggers |
| D4 | `forecast_stay_motion` (reused) | Debtor | Moves for a stay (yes / no) | C1 = yes (I1); after the ruling (I2) | ⚑ the bond, the collateral, cash above the reserve |
| D5 | `forecast_appeal` (reused) | Debtor | Appeals within 30 days (yes / no) | J2 = stands, or D1 = no, where a later levy falls inside the horizon | The branch amount; stay status |
| D6 | `forecast_listing_kept` (new) | Company | Keeps the stock listed through the horizon: a reverse split in time, or a timely hearing request (yes / no) | 29 Oct, where no earlier petition or acceleration | ⚑ cash, the notes' status, the deadline, the $0.60 price and authorized shares (Record) |
| D7 | `forecast_petition_cash_floor` (reused) | Company | Files when available cash first falls below the 30-day need | τ inside the horizon, before any petition | ⚑⚑ cash, need, dated triggers |
| D8 | `forecast_petition_cash_out` (reused) | Company | Files when cash first falls below zero | D7 = no, and cash below zero inside the horizon | ⚑⚑ as D7 |
| D9 | `forecast_petition_on_notes` (reused) | Issuer | Files on acceleration (yes / no) | H1 or H2 = accelerate | ⚑ cash, the $44.0M due, the judgment owed |
| H1 | `forecast_holders_act_judgment` (reused) | Holders of 25% or the trustee | Give §7.01(i) notice and accelerate (yes / no) | Each ripe date on the claimant's branch | ⚑ the judgment, its stay status, the issuer's cash |
| H2 | `forecast_holders_act_delisting` (reused; "repurchase only" merges with "neither") | Holders | Accelerate on the delisting default | D6 = no and suspended inside the horizon | ⚑ as H1 |
| H3 | `forecast_holders_involuntary` (reused) | Three or more noteholders | File an involuntary petition (yes / no) | Accelerated on the entered reading, unpaid, the issuer has not filed; §7.06 date inside | ⚑ as H1 |

**Situation.** Each question is asked in the conditions its actor weighs that hold at the decision on every trajectory of the path (the 4.0.0 `situation` rule, reused): the verdict branch and its amount, whether motions are pending or the ruling has issued, a stay moved or in force, an appeal, a levy, a settlement or payment, the notes due and unpaid, a delisting. A condition that holds on only some trajectories is left unstated.

### 6.2 Material factors and the record items that evidence them

| Id | Material factors (established first) | Record items (named slots; §8 lists the sources) |
|---|---|---|
| J1 | The weight of the head-start measure after the court admitted it; what the jury may award on each claim; the liability findings still open | the claimant's itemised damages claim; the defense's statement of intended proof on damages; the court's rulings admitting or limiting the damages evidence; the final verdict form; the summary-judgment and validity rulings; the defendant's public statements on the trial and its likely damages |
| J2 | The grounds preserved at trial; the court's own rulings on those grounds before the verdict | the motions for judgment as a matter of law made at trial; the court's rulings on the damages evidence |
| J3 | Whether the offered security protects the claimant; the debtor's showing of hardship | the company's statements on its liquidity and ability to post security |
| J4 | Where the debtor's assets sit; the risk of an unsatisfied judgment | the locations of the company's operating assets |
| C1 | What execution recovers now against what a petition would do to the claimant's position; the claimant's stated aims | the claimant's public statements on the litigation and its remedies; the parties' competitive relationship |
| C2, D3 | Signals of willingness to settle; each side's leverage | the company's statements on settlement; the company's own claims against the claimant; the parties' competitive relationship |
| D1, D5 | The debtor's stated intent to contest; the grounds preserved | the company's statements on contesting the claims; the motions for judgment as a matter of law made at trial |
| D2, D7, D8, D9 | The company's stated responses to an adverse judgment or a cash shortfall; its access to financing | the company's going-concern and bankruptcy statements; the company's financing routes and their status |
| D4 | The company's ability and stated intent to secure the judgment | the company's statements on its liquidity and ability to post security |
| D6 | The company's plan to regain compliance; the stockholders' past votes on share amendments | the listing deficiency notice and compliance deadline; the company's stated cure; the latest stockholder vote on a charter amendment |
| H1, H2, H3 | The holders' recovery if they act now against waiting; the notes' terms | the indenture's default, acceleration and suit terms; the notes' interest terms and payment record |

Readings (`dispute_interpretation`) are routed as in 4.0.0 (`evidence_routing`): settlement signals to D3 and C2, appeal intent to D1 and D5, debtor resistance to D2, D4 and C1, amount finality to J2. A reading is evidence handed to a question, never its probability.

<!-- next -->

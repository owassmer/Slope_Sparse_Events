# Pending money claim at jury trial: decomposition for the 14 May 2024 review (Akoustis case inputs)

## 1. Summary for approval

**What the model does.** It forecasts the dated cash flows of the supplied Slope line from 14 May to 10 Nov 2024 while the claimant's trade-secret, patent and related claims are with the jury. The event model is a generic template, `pending_money_claim` (a civil money claim at jury trial in US district court), chosen by the dispute's state, `liability_pending`, and never by the borrower. Akoustis fills it as case inputs. It feeds the existing post-judgment steps (settlement, execution, stay, registration, levy), the indenture template and the bankruptcy effects. **A missing judgment never activates enforcement.**

**Amounts come from the record (§5).** The claimant itemised its claim before trial (D.I. 543-1, 22 Apr 2024): at least $66.1M of trade-secret unjust enrichment on a 55-month head start, plus $2,236,184 on the patent, advertising and poaching claims. The conspiracy and UDTPA $66.1M figures restate the same money. D.I. 590 (14 May) leaves no UDTPA damages, so there is no trebling. The jury's choice of damages theory is the Jev question; code sets each branch's amount:
- **no award**: no judgment;
- **liability without the head-start measure** (O1): $1,426,412, the claimant's own patent ($279,808) and corrective-advertising ($1,146,604) figures; sensitivity $9,999,999;
- **claimant's theory**: $67,526,412 (the UDTPA poaching $809,772 is out: D.I. 590), beyond cash on every trajectory. Exemplary damages, interest and fees are one bounded term that moves no date or cash.

The jury is asked its own verdict form's questions (D.I. 580), not "which theory"; the three branches are composites of its answers (§7.12).

**Clocks (§3).** The verdict falls in a window of 16–22 May, drawn per trajectory from the 13 May call ("to the jury before the end of the week ... verdict in the next several days") and D.I. 550. Every later clock runs from the modeled verdict: entry the next business day; execution 30 days later; post-trial motions, briefing, and a ruling lag drawn from the judge's pace on this docket (8 of 10 draws fall inside the horizon). The judgment default ripens 16–22 Aug on the entered-judgment reading, or at ruling + 60 on the post-ruling reading. Both readings are carried (L11, open). The listing can fail inside the horizon only by suspension about 1 Nov without a hearing request. The June coupon ($1.32M) is paid in shares by base (the 13 May S-3 registers 5.0M Note Shares), with all cash as the sensitivity.

**Chains (§4).**
- **T1, the verdict and its enforcement:** settlement before the verdict; the verdict; the debtor's response at entry; post-trial motions; execution before the ruling, a stay, early registration, and the response on the levy day; the ruling (stands or set aside); an appeal, a stay pending appeal, and enforcement after the ruling.
- **T2, the notes** (claimant's branch only): notice and acceleration, then the issuer's or the holders' petition.
- **T3, the listing:** one company decision.
- **T4, operating cash:** the cash floor, then cash exhaustion.
- **T5, settlement:** on stated terms in each interval.

§4.6 lists what is collapsed and why: liability per claim; awards of $10.0M or more; enhancements; remittitur; the separate merits motions; the listing sub-steps; the repurchase.

**Jev questions.** `QUESTIONS_20240514.md` owns them: each question's event, answers, situation, record and consequences, and the rules every question follows.

**Implementation (§7).** Contract and registry 4.1.0, both additive: a new template, stage, five new registry entries, and label templates filled from case inputs. There are small changes to the domain, interpretation and tool, plus new walker methods and Chain steps that reuse the 4.0.0 flow. The 4.0.0 template and the recorded 20 Jun run are unchanged. Estimates: about 3,000–7,000 paths and 100–150 Jev asks per variant.

**Decisions (Owen, 27 Sep 2026; §7.12 records how each is built).**
- **O1.** The lower award branch is liability without the head-start measure: base $1,426,412 (patent plus corrective advertising, D.I. 543-1); the poaching $809,772 is out because D.I. 590 leaves no compensable UDTPA damages, which also makes the claimant's-theory sum $67,526,412. Sensitivity $9,999,999.
- **O2.** A compromise-award class is added only if some trajectory holds more than $10.0M above the reserve. Measured on the 14 May feed with the existing line: none (0.0% at entry and on every day), so the band is empty and has no class (§5.3).
- **O3.** The attributable legal-spend proxy continues at its level until the dispute ends on the path; no source supports a post-trial reduction.
- **O4.** Entry the next business day after the verdict (base); entry with the post-trial ruling is the sensitivity.
- **Item 1, financing access is a decision.** At the cash floor the company raises equity, files or continues (`financing_at_floor`); D8 stays the fallback at zero cash. The raise is code-owned by situation: $9.7M with no adverse money judgment on the path (inference: the ATM's baby-shelf capacity), $0 after the claimant's-theory judgment, each with a $5.0M sensitivity, received in equal daily amounts over 30 days. The common model's shared equity scenario is a sensitivity only.
- **Item 2, J1 follows the verdict form.** One jury node per money-bearing question of D.I. 580, in the form's order and conditioned on the earlier answers; J1's three branches are composites of them (§7.12).
- **Item 3, the existing line.** The ⚑ cash facts' forward runs start from the line's opening state (`Setup.exposure`), so Jev's cash equals the engine's.

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
| P3 Damages | The jury fixes the amount per claim on the form (D.I. 580 Q1(b), 1(d), 2(c), 3(b), 4(c), 5(b)) | A liability finding with a damages answer | **The claimant itemised its claim publicly** (D.I. 543-1 Ex. A.2, 22 Apr 2024; ACQ §1): trade-secret unjust enrichment "at least $66.1 million" (a 55-month head start, ¶31); patent $279,808 (¶19); corrective advertising $1,146,604 (¶42); poaching $809,772 (¶49). The UDTPA and conspiracy $66.1M figures (¶¶50, 56) are the same money under other theories, not additive. No defense figure is public (D.I. 543-1 Ex. A.4 gives none). Defense counsel: "if the jury adopts the theories of [the claimant]'s experts, it is possible the jury will issue an eight-figure verdict" (13 May call) | Theory: **J1**. Amount per theory: Record and Arithmetic (§5); never Jev |
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
| B8 Interest | §16.02: $44.0M × 6.0% ÷ 2 = **$1.32M due 15 Jun 2024** (a Saturday; paid Mon 17 Jun), inside the horizon; 15 Dec after it (2022 notes 8-K). Paid in shares unless the company elects cash (§16.02(c)), valued at 95% of the ten-day VWAP; §9.02(k) caps shares at 11,403,332 without a stockholder vote | The coupon date | Pre-cutoff share facts only (ACQ §4.2): the resale S-3 of 13 May registers 5,000,000 "Note Shares" for interest and make-whole payments; 175,000,000 authorized and 98,669,282 outstanding at 8 May (10-Q); close $0.60 on 1 May. At $0.60, $1.32M needs about 2.3M shares, inside the 5.0M registered and the 11.4M cap; the registered shares cover the whole coupon at any VWAP of $0.278 or more. The 20 Jun run's parameter values are not reused | **Bounded** (`coupon_cash_share`, new case values): base all shares, $0 cash (§16.02(c) default, and the 13 May S-3 shows the company preparing to pay in stock); sensitivity all cash ($1.32M on 17 Jun). Booked on every path, event or not (spec §16.3 "Ordinary obligations") |
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
| **Financing** | An explicit amount-and-date scenario shared by every path; central case adds none the record does not fix (spec §16.3). The 13 May re-activation of the at-the-market program ($48.0M remaining; "the sales agents are under no obligation to make any sales", 10-Q) is a known channel that fixes no amount or date, so it books nothing centrally. Decided (item 1): the company's raise is its own decision at the cash floor (D7, `financing_at_floor`), in a code-owned amount by situation (§7.12); the central case books no exogenous financing, and the common model's equity scenario is a sensitivity only. "Continue" in D2 books no cash of its own | Case input (raise amounts) and §16.3 sensitivity |
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
   - **liability without the head-start measure**: $1,426,412, below $10.0M (O1, §5.2);
   - **claimant's theory**: the claimed $67,526,412, beyond cash on every trajectory (enhancements one bounded term, §5.2).
   J1 is not one question: each branch is a composite of the jury's answers to its verdict form's money-bearing questions (§7.12).
3. **No award** → no judgment exists, so no enforcement, stay, registration or judgment-default node exists on the path (the template's rule, §2.1). The claimant's own post-trial motions cannot yield money by 10 Nov (L18). The path goes to T3 and T4; legal spend continues (§3).
4. A money award → judgment entered at E = V + 1 business day (L14), in the branch amount.

**At entry E.**
5. **Arithmetic** (spec §0: only impossibility removes a branch): "pay" exists where the amount owed is within available cash on some trajectory of the path at the decision; a full bond exists where cash above the reserve covers its collateral on some trajectory (the approved 20 Jun stay rule, `DECOMPOSITION.md` decision 3). On the claimant's-theory branch neither exists.
6. **D2** at entry: pay, file, or continue (operate and contest). "Continue" includes seeking a sale or new financing, which books no cash of its own; equity is raised only at the cash-floor decision (D7, item 1). Pay ends the dispute on the payment day (`resolve`); file books a petition at E + `petition_lag_days`.
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

Operating flows come from the common model (§16.3): the 14 May feed, the operating outlook, the coupon (B8). Event cash comes from T1 and T5. **τ** is the first day available cash falls below the 30-day need; **D7**: the company raises equity, files or continues at τ (`financing_at_floor`, item 1; 'raise equity' only where the amount available in its situation is positive). After a raise or 'continue', **D8**: it files when cash first falls below zero (`petition_cash_out`, reused). They are also asked in the ordinary-operating-risk attribution run (the event, including its legal costs, given no cash effect, spec §16.1), as the forecast's own questions on the same record with that run's facts (§7.12), together with the listing chain (D6 and the delisting route to the notes), a common borrower input (§7.12, PR #18 fixes).

### 4.5 Settlement terms

Settlement is its own decision on stated terms (spec §16.4), asked once per interval where the terms exist:
- **Offer (D3):** the debtor offers its available cash above the 30-day reserve on the settlement date, capped at the amount owed. In I0, where nothing is owed yet, the cap is the claimed $67,526,412. That amount bounds what can be paid (spec §16.3); it is not what every negotiation produces in one payment. **Terms (Owen, 28 Sep 2026):** the amount is paid in 12 equal monthly installments, the first on the settlement date (`settlement_payment`, `scenario.json`). A distressed company does not pay out its runway at once. The claim is released on the settlement date. Installments due after 10 Nov fall outside the horizon, and none is paid after a petition. A settlement exists only where the amount is positive on some trajectory (the approved 20 Jun rule, `DECOMPOSITION.md` §5.4). Sensitivity: the same amount as one lump sum on the settlement date. The first 14 May run paid it as a lump sum, about $6.1M of $11.7M before the verdict, which left the company at its cash floor at once.
- **Acceptance (C2):** the claimant accepts those terms. Both settlement questions are told the terms: the total and the monthly installment (P5/P50 on the settlement date) and the schedule. The offered amount is a path fact, and each interval and branch is its own node, so an acceptance probability is never reused for a different offer.
- A paid settlement releases the claim and the stay security, removes the §7.01(i) trigger, and ends attributable legal spend.

### 4.6 What is collapsed, and why

Each distinction below changes no payment timing, cash, receipts, financing access or another actor's material decision inside the horizon, or it changes them only inside a branch that is already beyond cash.

| Collapsed | Into | Basis |
|---|---|---|
| Liability per claim (trade secrets, conspiracy, Lanham Act, UDTPA, patent) | J1's three theories | Only the trade-secret head-start measure can reach $10.0M or exceed cash; the other claims total $2,236,184 at most (D.I. 543-1) and sit inside the lower award amount (O1). Conspiracy and UDTPA restate the same $66.1M (¶¶50, 56) |
| Any award of $10.0M or more | The claimant's-theory branch | Beyond cash on every trajectory: pay, a full bond, the levy (all reachable cash) and the notes default are identical. The merged-class test (§7) checks it |
| Exemplary, enhanced, trebled damages; fees; pre-judgment interest | One bounded term on that branch (§5.2) | They only raise an award already beyond cash. Trebling cannot arise (D.I. 590, L2) |
| Remittitur; the claimant's election after it | J2's two branches | Accepted above $10.0M: cash-identical to "stands". Refused: a new trial, inside "set aside". Accepted below $10.0M: inside "stands" (lender-adverse). In the 20 Jun run the whole damages ruling moved collections by about $2.2k |
| The separate merits motions (JMOL, new trial, patent JMOL, fees, interest, injunction) | One ruling date and J2 | One common lag (§3); only "any money judgment survives" moves cash |
| Seek a sale or financing vs neither | "Continue" in D2 | Neither books cash (equity is raised only at the cash-floor decision, D7); D2 is re-asked at every consequential milestone anyway |
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
| UDTPA poaching | $809,772 (¶49) | Excluded (O1): D.I. 590 held "the jury has no reasonable basis to find compensable damages under the UDTPA" |
| Exemplary, punitive, enhanced, treble damages; fees; interest | Requested, never quantified (¶¶32, 57, 61–62; D.I. 535 Q4 proposes exemplary up to 3× the trade-secret damages) | The bounded term below |
| The defense's figure | Not public by 14 May (D.I. 543-1 Ex. A.4 gives none) | Not used: the lower branch rests on the claimant's own non-head-start figures (O1) |
| Defense counsel, 13 May call | "if the jury adopts the theories of [the claimant]'s experts, it is possible the jury will issue an eight-figure verdict" | Read as: the claimant's theories give $10M or more; the defense's give less. **Inference**, labelled |

### 5.2 The branch amounts

| J1 branch | Amount | Disposition |
|---|---|---|
| No award | $0; no judgment | Record (the branch's definition) |
| Liability without the head-start measure | Bounded `lower_award_amount`. **Base $1,426,412** = patent $279,808 + corrective advertising $1,146,604 (D.I. 543-1 Ex. A.2 ¶¶19, 42), the record's own figures for a verdict that rejects the head-start measure. **Sensitivity $9,999,999**, the most an award can be and stay below the notes' threshold | Bounded (O1, decided) |
| Claimant's theory | **$67,526,412** = $66,100,000 + $279,808 + $1,146,604 (Arithmetic on the record; the conspiracy and UDTPA $66.1M restate the same money, and the UDTPA poaching $809,772 is barred by D.I. 590). Plus the bounded `claimant_enhancements` term: base $0 added; sensitivity DTSA exemplary at the 2× cap ($132,200,000) and §24-5(b) interest at 8% from 4 Oct 2021 to entry (about $13.9M), $146.1M in all. Fees have no public figure and are left out | Record and Arithmetic; enhancements Bounded. Beyond cash on every trajectory under both settings (test 5). Jev is told the range, never one figure |

**An unknown amount stays unknown (Owen, 28 Sep 2026).** A branch sums the agent's recorded components (`amount_rules`). If any counted component has no quoted amount, the sum is unknown, never $0. The lower branch then takes the declared `lower_award_amount.bound` ($1,426,412, `scenario.json`), and Jev and the page label it as the bound. A branch with no declared bound stops the analysis and names the component. The claimant's branch declares none. The kinds that the `claimant_enhancements` term stands for are never summed: exemplary, trebling, fees, costs and interest. The first 14 May run booked $0 on the lower branch because the agent left patent and advertising unquantified.

Post-judgment interest (§1961) accrues on the branch amount from E at the L8 rate. The bond is the branch amount plus accrued and one year's forward interest; collateral 100% (80% lower bound) (L7).

### 5.3 Where the mechanism thresholds fall (Arithmetic, checked on the built feed)

- **$10.0M (§7.01(i)).** Crossed only by the claimant's theory. The lower branch's base and its sensitivity are below it, so no setting triggers the notes (test 4).
- **Cash above the reserve** (Arithmetic on the 14 May feed with the existing line, engine-matched cash, 512 draws). Entry falls 17–23 May. Cash at entry P5/P50/P95 $10.37M / $11.65M / $11.81M; cash above the 30-day reserve $8.70M / $8.86M / $8.96M. The lower branch's base ($1,426,412) is payable from cash above the reserve on 100% of trajectories; its sensitivity ($9,999,999) on 0%, though cash covers it on 100%, so 'pay' is offered under both settings (the rule is cash ≥ owed) and the sensitivity's payment breaches the reserve. The cash floor falls inside the period on every trajectory, median day 102 (25 Aug) on the lower branch.
- **A compromise award between $10.0M and cash above the reserve** has no record figure. Cash above the reserve exceeds $10.0M on 0.0% of trajectories at entry and on 0.0% on any day of the period (maximum $8.97M), so the band is empty and needs no class (O2, decided).

### 5.4 Settlement and stay security

Both are the approved 20 Jun rules (`DECOMPOSITION.md` decisions 3 and §5.4), reading the §16.3 reserve: available cash above the 30-day need, capped at the amount owed (the claimed amount before the verdict). Neither is a Jev amount.

---

## 6. The Jev questions

`QUESTIONS_20240514.md` owns the Jev questions: the rules every question follows, the shared state, the legal scenarios held along a path, and for each question its event, answers, situation and grouping, record, and consequences.

---

## 7. The generic contract design: implementation plan for the next worker

The plan adds one template and one stage and reuses the walker (`forecast._Walk`), the engine chain (`events.Chain`), the composition (`Dist`, composite edges), the settlement, stay, levy, notes and listing steps, and the cash-floor questions. The 4.0.0 post-judgment template and the recorded 20 Jun run stay as they are (§7.8). Every step below ends in a test run, a commit and a push; JSON contracts are edited by small per-section scripts (skill: 'Break worker tasks into small, checkpointed steps').

### 7.1 `dispute_model.json` → 4.1.0 (additive)

- **`templates.pending_money_claim`** (forum `court`, instrument "a civil money claim at jury trial in US district court"), beside `federal_post_judgment`:
  - `intervals`: I0 (review date to verdict), I1, I2, I3 as 4.0.0.
  - `nodes`, each with `actor`, `decision`, `standard`, `record_items`, `path_facts`, `timing`, `asked_when`, `branches`, `residual_question`, `situation`, as 4.0.0 nodes: `verdict_theory` (J1), `post_trial_motions` (D1), `post_trial_ruling` (J2), `judgment_response` (D2); and by reference to the 4.0.0 nodes, reused unchanged: `execute_pre_ruling`, `enforce_after_final` (C1), `stay_motion` (D4), `stay_approved` (J3), `registration_early` (J4), `appeal` (D5), `settlement_offer` (D3), `settlement_accept` (C2). A `reuses` list names them, so `_q` keeps one spec per node name.
  - `edges`: the §4.1 order as data: `I0: settle → verdict_theory`; `verdict_theory.no_award → tail`; money branches `→ entry: judgment_response → post_trial_motions`; `post_trial_motions.yes → I1 (settle, execute, stay, registration, response) → post_trial_ruling`; `.no → I2`; `post_trial_ruling.stands → I2`, `.set_aside → tail`. The walker reads this list to choose its next method; it does not fork.
  - `verdict_branches`: for each J1 branch, its amount rule, in the case's terms: `no_award` → none; `defense_theory` → scenario parameter `defense_theory_amount` (as built: `without_principal_measure` → `lower_award_amount`, O1; §7.12); `claimant_theory` → the sum of the claimant's requested components, excluding any component marked `duplicates`, plus `claimant_enhancements`.
  - `label_templates`: every phrase the question text and the page need for this template, with placeholders filled from case inputs only (`{claimant}`, `{debtor}`, `{amount}`, `{range}`, `{date}`): e.g. `"I0": "before the jury's verdict"`, `"verdict_claimant": "the jury adopted {claimant}'s damages theory: judgment of {range}"`, `"verdict_defense": "the jury adopted {debtor}'s damages theory: an award below {threshold}"`, `"entry": "on the day the judgment is entered"`. No party name appears in the contract or in code.
- **`templates.indenture_convertible`**: add node `listing_kept` (D6) and a rule, `listing_route`: where the Panel decision falls after the horizon on every trajectory, the listing chain is `listing_kept` alone; otherwise the four 4.0.0 nodes. The choice is by dates, never by case.
- **`stages.court`**: prepend `liability_pending`. **`readings.events`**: add `trial_pending` ("the claims are being tried, or are set for trial, and no verdict has been returned"). **`readings.stage_rules`**: append `{event: trial_pending, stage: liability_pending}` last (lowest precedence), so a returned verdict or an entered judgment always wins.
- **`rules`**: add `frcp_58b2` (L14), `frcp_50b_59_deadline` (L17, 28 days), `stay_as_of_right` (L16), `nasdaq_5815_hearing_stay` (L12 text already cited in `panel_decision_days`, now its own rule), each with its citation and modality.
- **`parameters`**: add `judgment_entry` (bounded: `next_business_day` / `with_ruling`), `ruling_lag_days.mode = common` for this template, `verdict_window` (code timing: read from the case's scenario parameters, not the contract), `defense_theory_amount` and `claimant_enhancements` (bounded; values from the case, §7.6). `coupon_cash_share` gains per-case values (§7.6); the 4.0.0 values stay for the recorded run.
- **`composition`**: `order` gains "I0: settlement, then the verdict (J1)" before I1 and "entry: the response (D2), then post-trial motions (D1)". J1 is a real Choice node, not a composite; the ruling is J2 alone. `composites` unchanged.
- **`excluded_branches`**: add the appeal decided in the horizon (reused), the Panel decision and Form 25 (window closed), the repurchase (window closed), the injunction's cash (cannot be obtained), trebling (D.I. 590 leaves no UDTPA amount).

### 7.2 `app/domain/investigation.py`

- **A genuine pending-liability state.** `DisputeInstance.stage` may be `liability_pending`: the claims are pending and no judgment exists, so `judgment_date` is `None` by design, not a gap. New optional fields: `trial_started: date | None` (quoted), `claims: tuple[Claim, ...]` where `Claim(claim_id, label, finding_ids)` names each claim as the case states it.
- **`Component`** gains `claim: str = ""` (the claim it belongs to), `theory: Literal["claimant", "defense", ""] = ""`, and `duplicates: str = ""` (the component id it restates under another theory, e.g. the conspiracy $66.1M restating the trade-secret $66.1M). `status = "requested"` and `amount_cents` quoted from the claimant's statement of damages already exist. `ComponentKind` gains `other_compensatory` for claims outside the named kinds (corrective advertising, poaching).
- Nothing else changes; `PendingMotion` and `FinancingInstrument` are reused as they are.

### 7.3 `app/disputes/interpret.py`

- `DATED_STAGES` unchanged; the check `stage in DATED_STAGES and judgment_date is None → outside_model` does not fire for `liability_pending`, which is not dated.
- Accept `liability_pending` in the agent-only arm (`stage not in DATED_STAGES + ("amount_pending", "liability_pending")`).
- The stage rule added in §7.1 does the rest: a finding that establishes `trial_pending` and nothing later places the dispute at `liability_pending`. `amount_status` is `sought` (the claimant's figures), never `fixed`.

### 7.4 `app/agent/tools.py`

- `instantiate_dispute` accepts, for a pending claim: `trial_started` (quoted date), `claims` (labels, each resting on accepted findings), and requested `components` with `claim`, `theory` and `duplicates` (the same quote checks as today: every `amount_cents` appears in a cited quote). `judgment_date` stays absent; `_motions` is not required.
- The tool's `note` names the template the stage selects, so the agent sees which chain its findings feed.
- **As built (28 Sep 2026).** The agent reads the slots with `get_record_items(stage)`. That returns the record items of the questions the stage's chain asks: the contract's `asks` for `pending_money_claim`, the §6.1 list. Each item comes with the decisions that name it, in the dispute model's text and no more. For each item the agent records, with `attach_record_item`, the accepted findings that supply it, or that the record has nothing, with how it searched. `submit_packet` refuses while an item of a live dispute's template is open. The analysis builds `slots.json` from these records (spec §3.5). Jev's slot screen runs only for runs recorded before the tool existed. Each component cites in `finding_ids` the findings whose passages state it, and its amount must be in those findings' own quotes. The first 14 May run pooled all the dispute's quotes, and the page named the order (D.I. 590) as every requested component's source.

### 7.5 `app/disputes/forecast.py`: reuse the walker

- `Forecaster.paths`: accept `liability_pending` (with `borrower_role == "debtor"`); `_Walk.run` dispatches on the stage's template to a new entry method, `pre_verdict`, and otherwise runs as today.
- New `_Walk` methods, each a few lines calling what exists:
  - `pre_verdict(s)`: `self.settle(s, "I0", self.verdict)`;
  - `verdict(s)`: J1 as one real Choice node (`self.node("verdict_theory", "I0", ...)`), one `take` per branch; `no_award` → `self.tail(y, "no_judgment")`; money branches → `entry(y)` with `cls` from the branch amount (the existing `_entered_class` arithmetic: `beyond:` where collateral at its lower bound exceeds `reach`, else `amt:`);
  - `entry(s)`: D2 through the existing `a4(s, "entry", then=self.motions, on_file=...)`, with branches `pay` (only where `pay_possible`), `file`, `continue`;
  - `motions(s)`: D1; yes → `self.settle(s, "I1", self.q1)` (the existing I1 flow: `q1 → stay_i1 → j9_i1 → a4_i1 → ripe_i1`); no → `self.post(s)`;
  - `ruling(s)`: for this template, J2 as one binary node; `stands` → `self.post(y)`, `set_aside` → `self.tail(y, "set_aside")`. The 4.0.0 `ruling_classes` stays for the 4.0.0 template.
- `tail`: where the template's `listing_route` selects `listing_kept`, ask D6 alone (`listing_date` probe at the hearing-request date) with classes `listed` / `delisted_suspension`; else the 4.0.0 four-node chain.
- `a4` gains the `continue` branch (it behaves as `seek_sale_or_financing` does now: marks `seeking`, re-asked at the next milestone) and the `entry` phase. Its node and registry id come from the template (`judgment_response` here, `debtor_response` in 4.0.0).
- `context_phrases` reads the template's `label_templates` first, filled from case inputs; the 4.0.0 dictionaries stay as the fallback, byte-identical, so the recorded run's questions and Jev cache are unchanged.
- `MERITS`, `MERITS_KINDS` and `TRIGGER_PHRASES` are 4.0.0-only; the new template's nodes carry their own `record_items` and triggers in the contract.

### 7.6 `app/analysis/events.py`: the Chain books the new steps

- `_timeline` for `liability_pending`: V drawn per trajectory from the case's `verdict_window` (key `(instance, "verdict", "date")`); E = V + 1 business day (or, under `judgment_entry = with_ruling`, the ruling day); `E0` and `e_ix` become per-trajectory arrays. Every scalar use of `d.judgment_date` (`owed_at`'s `since_entry`, `judgment_default`, `rate_1961_bps`) reads the array E, and the rate comes from the case (§7.7).
- New steps: `verdict` (sets `entered` to the branch amount; `no_award` books nothing); `post_trial_motions` (yes: F = E + 28 + 21 + one common lag; no: F = A = E, AD = E + 30); `ruling` with `stands` / `set_aside` (`set_aside`: `cls_amount = 0`, `release_lock`, the dispute not resolved, so legal spend continues); `debtor_response` with ctx `entry` (milestone E); `settle` with ctx `I0` (start −1, end V); `listing` with the `listing_kept` route.
- **Cash facts that match the engine (spec §16.4).** `Chain.cash_at` leaves the line's draws and collections out today. For every ⚑ question the path facts, and for every ⚑⚑ question also `pay_possible`, `tau` and `cash_out`, read the borrower's available cash from `engine.run(line, opening, prefix events)` up to the decision day: a forward run of the loan on the prefix's event cash, cached by the prefix digest. It is causal (the prefix books nothing after the decision it feeds), so no feedback solver is needed. The difference is at most the line's net position (about one limit) and matters where it moves a threshold: pay feasibility on the defense-theory branch and the day τ.
- The coupon (B8) uses `coupon_cash_share` with the case's values: base `all_shares`, cash $0; sensitivity `all_cash`, $1.32M on 17 Jun, none after a petition.

### 7.7 Registry, case inputs and scenario parameters

- **`question_registry.json` → 4.1.0 (additive).** New entries, each with `actor`, `template`, `node`, `asked_when`, prompt instructions as the 4.0.0 forecasts, and criteria from the host's label templates: `forecast_verdict_theory` (Choice: `no_award`, `defense_theory`, `claimant_theory`), `forecast_post_trial_ruling` (Choice: `stands`, `set_aside`), `forecast_post_trial_motions` (Noul), `forecast_judgment_response` (Choice: `pay`, `file`, `continue`; "pay" offered only where arithmetic allows), `forecast_listing_kept` (Noul). Profiles: the first four join `dispute_forecast`, the last `financing_forecast`. `evidence_routing`: `debtor_resistance` → `forecast_judgment_response`; `appeal_intent` → `forecast_post_trial_motions`; `amount_finality` → `forecast_post_trial_ruling`; `no_cash` gains `forecast_verdict_theory` and `forecast_post_trial_ruling`. Nothing is retired.
- **`cases/akoustis_20240514/scenario.json`** (case inputs; the only place Akoustis figures live): `verdict_window` (16–22 May 2024, with the quoted basis); `judgment_entry` (base `next_business_day`); `lower_award_amount` (base `components_without_principal`, 142,641,200 cents; sensitivity 999,999,900 cents; O1 as decided); `claimant_enhancements` (base 0; sensitivity 14,610,000,000 cents, the DTSA 2× exemplary and §24-5(b) interest to entry); `coupon_cash_share` (base `all_shares`; sensitivity `all_cash`; `share_capacity` 5,000,000 from the 13 May S-3; price 60 cents at 1 May; limit 11,403,332); `rate_1961_bps` (the H.15 weekly 1-year CMT for the week ending 10 May 2024, fetched and cited by the implementer). The reserve and financing are §16.3's (worker A) and are read, not restated.
- **Test fixture** `tests/akoustis_20240514_fixture.py`: the pending dispute as the agent would instantiate it from the pre-14-May record (the claims, the requested components from D.I. 543-1 with `duplicates` marked, `trial_started` 6 May 2024, the notes), with fixed readings (no Jev), like `tests/akoustis_fixture.py`.

### 7.8 Keeping 4.0.0 and the recorded 20 Jun run working

- Both contracts change additively. `build.py` refuses disputes interpreted under another model version; the contract gains `compatible_versions: ["4.0.0"]`, and the check accepts them, so the recorded run builds without `refresh`.
- The 4.0.0 template, node names, registry ids, phrases and parameter values are untouched, so its questions' states are byte-identical and the Jev cache still hits. The existing suite (`tests/test_chains.py`, `test_jev_states.py`) runs unchanged, and `load_page_state` on the recorded `analysis.json.gz` still loads.

### 7.9 Tests (composition and cash only)

1. **Composition:** under random Dirichlet answers for every node, `model.probs` sums to 1 over the paths of each dispute, including J1's three branches and every composite (the skill's fast check).
2. **A missing judgment never activates enforcement:** on every `no_award` path, and after every `set_aside`, no enforce, stay, registration, judgment-default or `judgment_response` step exists, and the engine books no levy or lock.
3. **Clocks move with the modeled verdict:** V lies in the window on every trajectory; E = V + 1 business day; execution E + 31; motions E + 28; no ruling before E + 49 + 17.
4. **Branch amounts:** the claimant's theory is $67,526,412 (duplicates and the barred UDTPA claim excluded); the lower branch never ripens §7.01(i) under either setting.
5. **Merged class:** the claimant's branch is cash- and date-identical under `claimant_enhancements` base and sensitivity (the existing swap test).
6. **Coupon:** base books $0 cash; sensitivity books $1.32M on 17 Jun, none after an earlier petition.
7. **§16.4:** for every ⚑ node, the path facts' cash equals the engine's available cash on the decision day on each trajectory; for ⚑⚑ nodes, `pay_possible` and τ are computed on the same cash.
8. **Settlement:** a paid settlement releases the claim and the lock and stops legal spend; its amount never exceeds cash above the need or the amount owed (the claimed amount in I0).
9. **4.0.0 unchanged:** the existing chain tests pass without edits.

### 7.10 Size estimates

- **Paths** (per run variant; each bounded setting is its own variant): `no_award` gives a handful (listing and cash floor only). The defense branch has no notes default, so its tail is the listing and the cash floor: about 500–1,500 paths. The claimant's branch carries both notes readings, one ruling binary instead of the 4.0.0 amount classes, and the one-node listing chain: about 2,000–5,000. **Total about 3,000–7,000**, below the 20 Jun tree's 12,650. If it runs above ~8,000, merge D2 `continue` re-asks whose prefixes are digest-identical (the existing `unfiled` rule). The implementer measures and reports.
- **Jev asks:** 18 questions, keyed by situation: J1 once; J2 twice; D2 about 10–14; D3 and C2 about 14; C1 about 7; D4 and J3 about 8; J4 about 4; D1 and D5 about 4; the notes (H1, H2, H3, D9) about 8; D6 about 3; D7 and D8 about 30–60 by situation tags, plus 2 in the ordinary-risk attribution run. **About 100–150 asks** per central variant. Economic variants re-ask only the ⚑ questions whose facts changed (spec §16.6). Caps are raised to fit (spec §16.8).

### 7.11 Order of work (each step: tests, commit, push)

1. Contract 4.1.0: the template, stage, rules, parameters, `compatible_versions` (small per-section scripts).
2. Domain, interpretation and tool changes, with the fixture.
3. Chain steps and the per-trajectory timeline.
4. Walker methods and label templates.
5. The engine-matched cash facts.
6. Registry 4.1.0.
7. Tests 1–9; measure paths and asks; report both.

### 7.12 As built: Owen's decisions and what they changed (27–28 Sep 2026)

**O1–O4** are recorded in §1 and applied in §5.2, §5.3 and the case inputs (`cases/akoustis_20240514/scenario.json`: `lower_award_amount`, `claimant_enhancements`, `judgment_entry`). The lower J1 branch is named `without_principal_measure`; its amount is the claimant's requested components less the principal measure and any claim whose damages the court barred (`amount_rules`). O3 needs no model change: the legal-spend proxy stops only where the dispute resolves (payment, settlement), and `set_aside` does not resolve it.

**Item 1: financing access is a decision.**
- Contract: node `financing_at_floor` (template `bankruptcy_effects`, branches `raise_equity` / `file` / `continue`), booking `raise` in `branch_bookings`, parameters `raise_capacity`, `raise_capacity_after_adverse_judgment` and `raise_days`, and `adverse: true` on the `claimant_theory` verdict branch. The node replaces `petition_cash_floor` only where the case sets `raise_capacity`, so the 20 Jun run keeps its floor question.
- Case inputs: $9.7M (sensitivity $5.0M) with no adverse money judgment on the path; $0 (sensitivity $5.0M) from the entry of a claimant's-theory judgment; 30 days. The $9.7M is an **inference** (one third of public float per 12 months, net of January's $10M). Its record basis is the ATM re-activated on 13 May with $48.0M remaining, where the agents are not obliged to sell.
- Chain: the raise books the amount available on the floor day in equal daily amounts over 30 days (the remainder on the first day), and none after a petition. `raise_equity` is offered only where the amount is positive on some trajectory. Jev is told the amount (`equity_raise_available`, P5/P50/max). D8 (`petition_cash_out`) stays the zero-cash fallback after `raise_equity` or `continue`. The bank view asks the same decision.
- The common model's `equity_injection` scenario is a sensitivity only; the central case books no exogenous financing.
- **Where the raise is available (Owen, 28 Sep 2026).** The raise capacity depends on whether an adverse money judgment stands on the day. `raise_capacity_after_adverse_judgment` applies only from the entry of a claimant's-theory judgment until it is set aside after trial, paid or settled. Everywhere else, `raise_capacity` applies: no award, the lower award, a claim settled before the verdict, a judgment set aside. The dispute's own resolution does not close the raise. The first 14 May run asked the floor question after a pre-verdict settlement without the raise, and that drove its top sensitivity.

**Item 2: J1 from the jury's verdict form.** The source is D.I. 580 (the blank final special verdict form, filed 9 May 2024, RECAP), read with the claimant's proposed form D.I. 535 (12 Apr 2024). D.I. 535 asks one liability question for all trade secrets (Q1), then unjust enrichment (Q2), willfulness and exemplary damages up to 3× (Q3–4), UDTPA (Q5–6), false advertising (Q7–8), conspiracy (Q9–10) and patent infringement, validity, willfulness and royalty (Q11–14). The final form asks per alleged trade secret in two columns and puts conspiracy second. It drops validity, which the court decided (D.I. 557). The model follows D.I. 580. Each money-bearing question is one jury node (`verdict_finding`: liability; `verdict_measure`: whether the award rests on the head-start measure), asked once per sequence of earlier answers that reaches it. None asks for an amount. The case's `verdict_form` holds the questions and their routing; the quotes are verbatim (checked against the fetched text).

| Form question | Quoted (D.I. 580) | Yes → | No → |
|---|---|---|---|
| 1(a) (`verdict_finding`) | "Has Qorvo established by the greater weight or preponderance of the evidence that (a) this alleged trade secret qualifies as a trade secret and (b) Akoustis is liable for trade secret misappropriation for this trade secret?" and "Has Qorvo established by a greater weight or preponderance of the evidence that Akoustis was unjustly enriched by misappropriating this trade secret?" Asked as: "YES" in both columns for at least one alleged trade secret (the form's gate to 1(b)) | 1(b) | 2(a) |
| 1(b) (`verdict_measure`) | "State the amount Akoustis was unjustly enriched by misappropriating Qorvo’s trade secrets as to which you have answered “Yes” and “Yes” in Question 1(a) above." Asked as: the award adopts the head-start measure (not the amount) | claimant's theory | without the head-start measure |
| 2(a) | "Has Qorvo proven by a preponderance of the evidence its Civil Conspiracy claim against Akoustis?" | 2(b) | 3(a) |
| 2(b) | "Do you find that Qorvo has proven by a preponderance of the evidence that actual damage to Qorvo was caused by the conspiracy?" | 2(c) | 3(a) |
| 2(c) (`verdict_measure`) | "What amount of damages for actual loss, if any, were caused by Akoustis’ action and thus you award to Qorvo against Akoustis?" Asked as: the award adopts the head-start measure, which the conspiracy claim restates (D.I. 543-1 ¶56) | claimant's theory | without the head-start measure |
| 3(a) | "Has Qorvo proven by a preponderance of the evidence each of the elements of its False Advertising claim against Akoustis?" | without the head-start measure | 5(a) |
| 5(a) | "Do you find that Qorvo has proven by a preponderance of the evidence that the claims of the ’018 or the ’755 Patents have been infringed?" Asked as: "YES" for at least one patent claim | without the head-start measure | no award |

Collapsed, each with its reason in the case input:
- **1(c)–1(d)**, willful and malicious and exemplary damages. They only raise an award already beyond cash (the bounded enhancements).
- **4(a)–(c)**, UDTPA. D.I. 590 says "The jury has no reasonable basis to find compensable damages under the UDTPA".
- **5(b)–(c)**, patent amount and willfulness. Both sit inside the lower bound.

Routing inferences, labelled:
- Once 1(b) rejects the head-start measure, 2(c) is not asked. The model assumes a jury that rejects the measure for the trade-secret award does not adopt it for the same money under conspiracy.
- Once any lesser claim succeeds, the later questions are not asked. A verdict on fewer lesser claims is carried at the O1 amount, which is lender-adverse.

The three branches' composites are disjoint and exhaustive over the answers (tested), so J1's probability is the chain rule over 9 jury nodes. The cash tree is unchanged: the same three verdict steps.

**Item 4: settlement terms (28 Sep 2026).** §4.5 as amended. Contract parameter `settlement_payment` (`lump_sum` by default, so the 20 Jun run is unchanged; `installments` with `installments: 12` in the 14 May case). `Chain.settle` books the bound in 12 calendar-monthly parts from the settlement date and resolves the dispute that day. Jev's settlement facts carry the total, the monthly installment and the schedule. The acceptance question's assumption states the terms. After an agreed settlement the situation reads 'agreed to settle ..., in 12 equal monthly installments'. The lump sum is the sensitivity (`settlement_payment: true`).

**Page text and the claimant's sums (28 Sep 2026).** The page's cash sentence is generated from `Setup.collection`: under `debit`, the 14 May central setting, each installment is debited in full when cash covers it. The `protect_need` sensitivity collects only from cash above the need. The claimant's-theory and lower-branch sums exclude any component marked as the defense's theory.

**Item 3: the existing line.** The line opened on 1 Apr 2024. Its state on 14 May is $389,677 principal and 8 installments of $404,095 due 3 Jun–14 Aug (`Setup.exposure`, case worker). The Basis cash of both the tree and the analysis includes the line's history cash (+$384,839). The forward runs behind every ⚑ fact pass the engine its own opening, which adds that cash itself. Chain cash equals the engine's available cash on every trajectory and day (test 7).

**Registry.** Seven entries tagged `version: 4.1.0`: `forecast_verdict_finding`, `forecast_verdict_measure`, `forecast_post_trial_motions`, `forecast_post_trial_ruling`, `forecast_judgment_response`, `forecast_listing_kept` and `forecast_financing_at_floor`, plus routes and `no_cash`. `forecast_verdict_theory` is not created: J1 is never asked as one question. `registry_version` stays 4.2.0, because the snapshot-sweep cache is keyed on it. The Jev cache is keyed on each built question's text, which is unchanged for every 4.0.0 question.

**Measured on the 14 May tree** (the design's fixture of the pending dispute and notes, `tests/akoustis_20240514_fixture.py`; no Jev):
- **Paths:** 3,570 (central). Sensitivities: lower award $9,999,999, 3,298; both raises $5.0M, 5,159; coupon in cash, 3,259; the window's last day, 3,550. Bank view: 4 paths.
- **Jev asks:** 221 = 219 dispute + 2 bank view. By node:

| Node | Asks | Node | Asks | Node | Asks |
|---|---|---|---|---|---|
| verdict_finding | 7 | verdict_measure | 2 | judgment_response | 21 |
| post_trial_motions | 2 | post_trial_ruling | 2 | settlement_offer | 13 |
| settlement_accept | 13 | execute_pre_ruling | 2 | enforce_after_final | 16 |
| stay_motion | 6 | stay_approved | 6 | registration_early | 8 |
| appeal | 2 | holders_act_judgment | 10 | holders_act_delisting | 8 |
| petition_on_notes | 17 | holders_involuntary | 8 | listing_kept | 19 |
| financing_at_floor | 28 | petition_cash_out | 29 | | |

- **Cost:** fixture states are 1.9k–4.4k chars. With the 20 Jun run's mean evidence, readings and record items (4,283 chars) and each built question, the estimate is 7.1k–9.1k chars per ask and about 1.78M chars in all. That is about $0.025 a pass, with $0.075 reserved at 3 attempts ($0.042 per million input tokens, 3 chars per token). Caps are settings, raised to fit.
- **Above the §7.10 estimate** (100–150): the counts are keyed by situation. `financing_at_floor` and `petition_cash_out` split by situation tags (28 and 29), and `listing_kept` by amount class and notes status (19). Three `holders_involuntary` asks fall after the period on every trajectory and cancel inside the merged unfiled class (the 4.0.0 rule; their reach is zero, tested). They are left as in 4.0.0, because pruning them would change the 20 Jun tree.
- **Fix found while measuring:** the I0 settlement questions read no cash facts, because nothing is owed before a verdict. The owed filter no longer applies in I0.
- **4.0.0 unchanged:** the 20 Jun tree was rebuilt after every step. Each rebuild gave 13,821 paths and 337 node keys, with every question and bank-view state hash identical.

**PR #18 review fixes (28 Sep 2026).**
- **Date order.** The cash floor, cash exhaustion, the levy-day response after the ruling and the notes' judgment default book on their own day on every trajectory, whatever the walk order (`events.py` `waits`); the walker asks the floor before the first decision it precedes, or whose cash it reads after the floor (a stay's approval, a settlement's payment, a levy). Their facts come from each whole path. A pending claim's levy that falls before the I3 window on some trajectory is walked first, and every branch of the company's response still reaches the window.
- **Only impossibility removes a branch.** The raise is offered wherever a whole path makes it available at the floor, including a set-aside or payment walked after the floor question and dated before it.
- **⚑ facts equal the engine.** Bond collateral is the engine's figure on the approval day (with §1961 interest at the pending rate); the amount owed counts only levies and payments dated before the decision.
- **One dispute end.** A levy or payment that satisfies the judgment, a settlement, or a vacatur ends the dispute (`resolve`): legal spend stops that day and never returns, a later petition included.
- **Ordinary operating risk (orchestrator, 28 Sep 2026).** It is the same forecast with the event, including its legal costs, given no cash effect: the dispute ends on the review date at no cost, so its legal spend stops from then (the same `resolve`), and everything else is identical: operations, the line, the coupon, the floor decisions and the listing chain with the notes' delisting route (case input `ordinary_view: same_forecast`; the 20 Jun run keeps its bank view).
- **The ordinary view is asked on the same record (orchestrator, 28 Sep 2026).** Its questions are the forecast's questions of the same node type, built by the same state builder (`Forecaster.built`, through `ordinary_state`): the same case block with the parties named from case inputs, the same record items, evidence and readings routed to the node, the same standard, and the forecast's conditions for the question (the listing and notes conditions, `ASSUMED`). Three things differ, and only these: the path facts come from the ordinary run's engine; the context carries no dispute-branch condition; and the conditions that hold add one item, the model's `no_cash_effect` label template filled from case inputs ("the dispute with {claimant}, including its legal costs, is given no cash effect: it ends on the review date at no cost"). The first build asked these questions on the retired 20 Jun bank-view state (the company unnamed, no record items or evidence, about 1.2k characters against 11–14k for the same decision in the forecast), so the attribution mixed an evidence difference with the event's cash. The 20 Jun bank view keeps its own state; its tree and state hashes are unchanged. Guarded by test 7i.
- **The ordinary view's path facts carry the ordinary obligations (orchestrator, 28 Sep 2026).** Every path fact is classified by its source in the code (`Forecaster.facts_by_source`). Facts that describe the ordinary obligations and background risks are the same on every path (spec §16.3): the notes' principal and judgment-default clause (`obligation_facts`, read from the instrument), the instrument's dated triggers (the coupon, the listing deadline, the repurchase date, the holders' earliest petition; events.py `INSTRUMENT_TRIGGERS`), the raise available, and the decision dates, cash and (at the cash floor and when cash runs out) the 30-day operating need. The ordinary view carries them as the forecast gives them for the matching situation, dated and valued on its own engine run. Facts that exist only because of the dispute stay out: the claim's components, the pending motions, the amount owed, a merged class's judgment range, the bond and reduced security, the settlement terms, and the contract dates the dispute sets (the appeal deadline and the judgment default's ripe dates computed from a modeled judgment; `DISPUTE_TRIGGERS`). Their absence is the event given no cash effect. Before this, the forecast's questions carried the notes' terms and the ordinary view's did not. Guarded by test 7j.
- **One fact contract per node type (orchestrator, 28 Sep 2026).** A question of a given node type receives the same set of path facts in both views, apart from the dispute-only facts the ordinary view omits by rule. One builder, `Forecaster.ordinary_facts`, makes the ordinary half for both: the forecast's `facts_by_source` and the ordinary view's `bank_facts` call it with their own pooled rows, so the two views cannot drift apart. It gives the decision dates and available cash to every dated question, the 30-day operating need only at the cash floor and when cash runs out (`NEED_NODES`), the raise available only at the cash floor, the instrument's dated triggers to the questions that weigh contract dates (`DATED`), and the notes' terms. Before this, the ordinary view's listing, holders' and notes questions also carried the operating need, which the forecast's questions of those types never state. Guarded by test 7j, which asserts equal key sets per node type.

---

## 8. Evidence requirements

The record each question reads (`QUESTIONS_20240514.md` §4, each entry's **Record** field) is a set of named slots. The agent fills each one with accepted findings from the 14 May snapshot; a slot no finding fills is left out of Jev's state. Each item is routed whoever wrote it, with its author, date and status (court ruling, statement of intended proof, party argument, company disclosure, third-party record); a missing statement by one party is not a gap. Every source below is public on or before 14 May 2024. Status: **kit** (in the `akoustis_20240514` snapshot before step 9), **added** (acquired for this table, step 9), **not public by the cutoff** (existed but was sealed, oral or unfiled). Record items that could not exist by 14 May are not listed.

| Record item | Questions | Sources (document, D.I. or section, date) | Status |
|---|---|---|---|
| the final verdict form | J1, J1b | D.I. 580, final special verdict form, blank (9 May 2024; court) | kit |
| the summary-judgment ruling and its reasons | J1 | D.I. 545 (25 Apr 2024; court) | kit |
| validity | J1 | D.I. 557 ('018 claims 1, 12 and '755 claims 9, 10 not invalid; 2 May 2024; court) | kit |
| the exclusion of defense technical opinions | J1, J1b, J2 | D.I. 546 (Lebby ¶¶3, 4, 6, 72–78, 81–116, 125–151, 154 excluded; 25 Apr 2024; court) | kit |
| claim construction | J1 | D.I. 152 (15 Mar 2023; court) | kit |
| each side's statement of intended proof | J1, D5 | D.I. 543-1 (22 Apr 2024): Ex. A.2, Qorvo, pp. 13–25; Ex. A.4, Akoustis ("intends to refute that Qorvo is entitled to any damages"), pp. 35–42 (statements of intended proof) | kit |
| Qorvo's damages method and figures | J1b | D.I. 543-1 Ex. A.2 ¶¶19, 31–32, 42, 56–57, 61–62 (statement of intended proof); the admitted head-start opinion, D.I. 553 (30 Apr 2024; court); the method as argued, D.I. 476 (23 Feb 2024, redacted; party argument) | kit |
| the revenue base left to the jury | J1b | D.I. 553 pp. 6–7 ("more appropriately examined in cross-examination") | kit |
| the defense's avoided-cost opinion as admitted | J1b | D.I. 553 pp. 10–11 and n. 2 (Irwin admitted; opinions resting on excluded Lebby opinions excluded); D.I. 471 (23 Feb 2024, redacted; party argument). The avoided-cost figure itself is redacted | kit; the figure not public by the cutoff |
| the court's limiting rulings | J1b | D.I. 546; D.I. 553 n. 2; D.I. 590 (UDTPA damages barred; 14 May 2024); D.I. 565, stipulated limiting instructions on Akoustis's motions in limine nos. 1 and 4 (2 May 2024; stipulation under the court's order). The motions-in-limine order itself, D.I. 548, is sealed | 546, 553, 590 kit; 565 added; 548 not public by the cutoff |
| the record on actual loss (2(c)) | J1b | D.I. 543-1 Ex. A.2 ¶¶52–57 (conspiracy: the same $66.1M); D.I. 476 (the head-start measure is a benefit to Akoustis; no actual-loss figure) | kit |
| the Rule 50(a) motions made at trial and their disposition | J1, D1, J2 | D.I. 587, Qorvo's bench memorandum opposing Akoustis's Rule 50(a) motion on UDTPA remedies (13 May 2024; party argument); D.I. 590, the order (14 May 2024; court: granted in part and denied in part, and records the denial of the first 50(a) motion); trial days 6–7 in the 14 May docket view. The motions were made orally; the trial transcript is not public | kit; the motions' text not public by the cutoff |
| the court's pre-verdict rulings on the damages evidence | J2 | D.I. 553 (30 Apr 2024); D.I. 546 (25 Apr 2024) | kit |
| the company's statements on contesting the claims and on the trial | D1, D2, D5, D7 | 13 May 2024 earnings call (Wright: "vigorously defend", "an eight-figure verdict", "ability to raise money"; company statement); 10-Q of 13 May 2024 Note 14 and Part II Item 1 (company disclosure) | kit |
| Qorvo's claims and the relief it seeks | C1, C2, C3 | D.I. 543-1 Ex. A.2 ¶41 (injunction against false promotion) and ¶62 (permanent injunctive relief, costs, interest, fees, disgorgement); D.I. 543 §6, "Alleged Damages" (22 Apr 2024); D.I. 133 prayer (17 Feb 2023) (statements of intended proof and pleading) | kit |
| the parties' competitive relationship | D3, C1, C2 | FY2023 10-K Item 1, "Competition" (6 Sep 2023; company disclosure); D.I. 590 ("testimony that the Parties are competitors"; court) | kit |
| the company's statements on settlement | D3 | 10-Q of 13 May 2024 Note 14 (none disclosed) | kit; stated as not in the record if no finding |
| the company's own claims against Qorvo | D3 | 10-Q of 13 May 2024 Note 14 (E.D. Tex. 2:23-cv-00180, filed 20 Apr 2023; the inter partes review petitions) | kit |
| the company's disclosures bearing on collectability | C1, C2 | 10-Q of 13 May 2024 Note 2 (cash $15.2M at 31 Mar 2024; going-concern doubt; an adverse judgment would "create an urgent need for additional liquidity") | kit |
| the company's properties | J4 | FY2023 10-K Item 2, "Properties" (Huntersville, NC headquarters; the Canandaigua, NY fab) and cover (Delaware incorporation); 10-Q Note 13 (leases). Nothing states where cash is held | kit |
| the company's statements on its liquidity and ability to post security | D4 | 10-Q of 13 May 2024 Note 2 and Part II Item 1A; 13 May 2024 release (8-K Ex. 99.1) and call | kit |
| the company's going-concern and bankruptcy statements | D2, D7, N1 | 10-Q of 13 May 2024 Note 2, MD&A overview, Part II Item 1A | kit |
| the company's financing routes and their status | D2, D7 | 10-Q of 13 May 2024 Note 2 and Part II Item 5 (at-the-market program re-activated with the 10-Q, $48.0M remaining, agents under no obligation to sell); ATM Sales Agreement of 2 May 2022 (Ex. 1.1 to the 10-Q filed 2 May 2022); ATM prospectus supplement, 424B5 of 2 May 2022; shelf S-3 333-262540 (4 Feb 2022); the January 2024 offering (below); resale S-3 of 13 May 2024 | agreement and S-3 333-262540 added; the rest kit |
| the January 2024 offering | N1 | 8-K of 29 Jan 2024 Item 1.01 (underwriting agreement with Roth, 25 Jan; preliminary supplement filed 24 Jan; over-allotment exercised; closed 29 Jan); 424B5 dated 25 Jan, filed 29 Jan 2024 (20,000,000 shares plus 3,000,000 over-allotment at $0.50; last sale $0.70 on 24 Jan) | kit |
| the at-the-market program and its re-activation | N1 | ATM Sales Agreement of 2 May 2022 (up to $50.0M through Oppenheimer, Craig-Hallum and Roth; commission up to 3.0%); 424B5 of 2 May 2022; 10-Q of 13 May 2024 Part II Item 5 and Note 2 (re-activated 13 May 2024) | agreement added; the rest kit |
| the 10-Q's liquidity and going-concern text | N1 | as the going-concern row | kit |
| counsel's statement on the ability to raise money | N1 | 13 May 2024 call (Wright: the litigation "may have a significant impact on the company, including its value, operations, and ability to raise money") | kit |
| the shelf's capacity | N1 | S-3 333-262540 ($150,000,000 of securities; filed 4 Feb 2022; base prospectus dated 15 Feb 2022, as the 424B5s state); takedowns on it: 424B5 of 2 May 2022 (ATM, up to $50.0M), 424B5 of 19 Jan 2023, filed 23 Jan 2023 (the January 2023 offering; closing stated in the FY2023 10-K), 424B5 of 29 Jan 2024. The shelf's unused amount and any baby-shelf (I.B.6) limit are not stated in any pre-cutoff filing | S-3 and 2023 424B5 added; the rest kit |
| the authorized and outstanding shares | D6a (and the share capacity, §2.6, for D7 and N1) | 10-Q of 13 May 2024 cover (98,669,282 shares at 8 May 2024) and balance sheet (175,000,000 authorized); common stock equivalents table (9,341,825 for note conversion, 3,031,625 options); resale S-3 of 13 May 2024 (5,000,000 Note Shares) | kit |
| the indenture's default, acceleration and suit terms | H1 | indenture of 9 June 2022 §§7.01, 7.02, 7.06, 7.07, 10.01 (Ex. 4.1 to the 8-K of 10 June 2022) | kit |
| the notes' interest terms and payment record | H1 | 8-K of 10 June 2022; FY2023 10-K (6.0%, payable semi-annually from 15 Dec 2022, in cash or shares; "Common stock issued in payment of interest" $2,684K in FY2023); 10-Q of 13 May 2024 (nine months: $1,320K of interest paid in shares); resale S-3 of 13 May 2024 (5,000,000 more shares registered for interest) | kit |
| the holders of the notes where filings show them | H1 | resale S-3 of 13 May 2024, "Selling Stockholders" table (company disclosure) | kit |
| the deficiency notices and the compliance periods | D6a, D6b | 8-K of 27 Oct 2023 Item 3.01 (notice of 24 Oct 2023; first period to 22 Apr 2024); 10-Q of 13 May 2024 Note 12 (second period granted, to 21 Oct 2024). Nasdaq's letter granting the second period was not filed: EDGAR shows no 8-K between 14 Feb and 13 May 2024 | kit; the April 2024 letter not public by the cutoff |
| the company's stated options to regain compliance | D6a | 10-Q of 13 May 2024 Note 12 and Part II Item 1A ("could include seeking to effect a reverse stock split"; close $0.60 on 1 May 2024) | kit |
| the latest stockholder vote on a charter amendment | D6a | DEF 14A of 19 Sep 2023 (excerpt: record date 5 Sep 2023, meeting 2 Nov 2023, Proposal 3 raising authorized shares from 125,000,000 to 175,000,000; approval "votes cast for ... must exceed the votes cast against"); 8-K of 2 Nov 2023 Items 5.03 and 5.07 (approved 37,388,404 for, 8,919,309 against; amendment effective 2 Nov 2023). No reverse-split vote was called by 14 May 2024 | added |
| the company's statements on its listing | D6b | 10-Q of 13 May 2024 Note 12 and Part II Item 1A | kit |

**Case data (§2.6, not a snapshot source).** The daily AKTS open, high, low, close and volume, 1 Jun 2023 – 14 May 2024 (`cases/akoustis_20240514/akts_daily_px.csv`, with its source and retrieval date in `akts_daily_px.source.json`), for the at-the-market pace (average daily dollar volume 15 Mar – 14 May 2024: $337,069) and the offering price (the 14 May close, $0.44). Added.

**Common-model inputs (§16.3, worker A)** are not event-model slots: the 31 Mar balance sheet, the 10-Q cash flows, the 13 May guidance on revenue, burn and CHIPS credits.

**Considered and not listed.** D.I. 566 (3 May 2024, the poaching opinion admitted) bears only on UDTPA damages, which D.I. 590 bars, and verdict question 4 is not asked. D.I. 535 and 536 (the parties' proposed verdict forms) are superseded by D.I. 580. D.I. 537 (the joint proposed jury instructions), D.I. 522, 525, 568, 573 (letters and supplemental briefs on Bennis's methods) and D.I. 588–589 (bench memoranda on the conspiracy instruction, filed 14 May) repeat party argument already in the record or are named by no question's Record field.

**Not public by 14 May, and so stated as not in the record:** the defense's damages figure (Irwin's avoided costs, redacted in D.I. 471); the entered final pretrial order (D.I. 549) and the motions-in-limine order (D.I. 548), both sealed; the trial transcript, including the oral Rule 50(a) motions; the final jury instructions as given; Nasdaq's April 2024 letter granting the second compliance period.

---

## Isolation log

- **Case facts used** (all dated on or before 14 May 2024): the 10-Q and release of 13 May; the FY2023 10-K; the 2022 notes 8-K and indenture; the 8-Ks of 27 Oct 2023 and 29 Jan 2024; docket entries dated on or before 14 May, D.I. 15–590, and D.I. 580 (fetched from RECAP, filed 9 May); and, through `acquisition_akoustis_20240514.md`, D.I. 133, 476, 535, 543, 543-1, the 13 May call and the 13 May S-3. D.I. 590 is dated the review date itself.
- **Read but not used for case facts:** the 20 Jun design documents (`DECOMPOSITION.md`, `RESEARCH.md`, `STAGE3.md`, the 20 Jun acquisition note), which state the later verdict, judgment and post-trial filings. Only their general law, their pre-14-May record items (R4's pace sample, all ruled by 2 May 2024; R7; R8; `STAGE3.md` E16 and E19) and their approved modelling rules are reused. The 20 Jun code and contracts were read for structure; their Akoustis parameter values are not reused.
- **Seen and not used:** one search of the 20 Jun docket capture printed captions of entries dated 15 May to 7 Jun, the verdict entry among them. Every later reading of the capture was filtered to entries dated on or before 14 May.
- **The verdict date** is a window drawn per trajectory from pre-14-May statements (§3), not the actual date.
- **The acquisition note** is copied verbatim; its [POST] items are marked reveal-only there and enter no slot, parameter or question here.
- **Implementation (27–28 Sep 2026).** D.I. 580 was fetched from RECAP (filed 9 May 2024) and quoted verbatim in §7.12. The figures in §5.3 and §7.12 are code's arithmetic on the 14 May feed and the case inputs. No source dated after 14 May was read for them.
- **Record acquisition (step 9, 28 Sep 2026).** §8 was rebuilt from the Record fields of `QUESTIONS_20240514.md` §4. Added sources, all filed or published on or before 14 May 2024 (dates from EDGAR's submissions JSON and the RECAP filing stamps): the shelf S-3 333-262540 (4 Feb 2022), the ATM Sales Agreement (2 May 2022), the 424B5 of 23 Jan 2023, the 2023 proxy (19 Sep 2023, excerpt) and the 8-K of 2 Nov 2023, D.I. 565 (2 May 2024), and the daily price series filtered to 14 May 2024. The proxy's future-proposals section names a 22 May 2024 deadline (an isolation probe string) and is cut from the kit copy. No source dated after 14 May 2024 was read for this table.

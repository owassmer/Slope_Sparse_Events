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

**Jev questions (18; §6).**
- **Jury or court:** J1 damages theory; J2 the ruling; J3 stay on reduced security; J4 early registration.
- **Claimant:** C1 enforces; C2 accepts the settlement terms.
- **Debtor:** D1 post-trial motions; D2 pay, file or continue (at entry, on a levy, at a ripe default); D3 offers settlement; D4 moves for a stay; D5 appeals; D6 keeps the listing; D7 and D8 file at the cash floor or at zero cash; D9 files on acceleration.
- **Holders:** H1 act on the judgment default; H2 act on delisting; H3 file an involuntary petition.

Questions marked ⚑ receive cash facts that must match the engine on their decision date, including the line's own flows (§7.6).

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

Operating flows come from the common model (§16.3): the 14 May feed, the operating outlook, the coupon (B8). Event cash comes from T1 and T5. **τ** is the first day available cash falls below the 30-day need; **D7**: the company raises equity, files or continues at τ (`financing_at_floor`, item 1; 'raise equity' only where the amount available in its situation is positive). After a raise or 'continue', **D8**: it files when cash first falls below zero (`petition_cash_out`, reused). They are also the only questions in the ordinary-operating-risk attribution run (the event given no cash effect, spec §16.1), asked there with that run's facts (`bank_state`).

### 4.5 Settlement terms

Settlement is its own decision on stated terms (spec §16.4), asked once per interval where the terms exist:
- **Offer (D3):** the debtor offers a lump sum equal to its available cash above the 30-day reserve on the payment date, capped at the amount owed; in I0, where nothing is owed yet, capped at the claimed $67,526,412. It exists only where that amount is positive on some trajectory (the approved 20 Jun rule, `DECOMPOSITION.md` §5.4). Sensitivity: the same amount in monthly payments to the horizon.
- **Acceptance (C2):** the claimant accepts those terms. The offered amount (P5/P50 on the payment date) is a path fact, and each interval and branch is its own node, so an acceptance probability is never reused for a different offer.
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

Post-judgment interest (§1961) accrues on the branch amount from E at the L8 rate. The bond is the branch amount plus accrued and one year's forward interest; collateral 100% (80% lower bound) (L7).

### 5.3 Where the mechanism thresholds fall (Arithmetic, checked on the built feed)

- **$10.0M (§7.01(i)).** Crossed only by the claimant's theory. The lower branch's base and its sensitivity are below it, so no setting triggers the notes (test 4).
- **Cash above the reserve** (Arithmetic on the 14 May feed with the existing line, engine-matched cash, 512 draws). Entry falls 17–23 May. Cash at entry P5/P50/P95 $10.37M / $11.65M / $11.81M; cash above the 30-day reserve $8.70M / $8.86M / $8.96M. The lower branch's base ($1,426,412) is payable from cash above the reserve on 100% of trajectories; its sensitivity ($9,999,999) on 0%, though cash covers it on 100%, so 'pay' is offered under both settings (the rule is cash ≥ owed) and the sensitivity's payment breaches the reserve. The cash floor falls inside the period on every trajectory, median day 102 (25 Aug) on the lower branch.
- **A compromise award between $10.0M and cash above the reserve** has no record figure. Cash above the reserve exceeds $10.0M on 0.0% of trajectories at entry and on 0.0% on any day of the period (maximum $8.97M), so the band is empty and needs no class (O2, decided).

### 5.4 Settlement and stay security

Both are the approved 20 Jun rules (`DECOMPOSITION.md` decisions 3 and §5.4), reading the §16.3 reserve: available cash above the 30-day need, capped at the amount owed (the claimed amount before the verdict). Neither is a Jev amount.

---

## 6. The Jev questions

Eighteen questions, each one actor's decision. None asks about timing, an amount, affordability, enforceability or legal meaning. Every question receives the standard state (spec §3.4; registry `state_contract`): the case, the question with its branches and situation, the governing standard, its record items (§8), the path facts code computed, the assumptions that hold, the evidence, and the readings routed to it. Material factors are established first, and aggregation receives the factor results and the structured facts, not the whole record again (spec §16.4).

**Cash facts and the engine (spec §16.4).** A question marked **⚑** receives cash facts: available cash, the 30-day need, the amount owed, collateral, the offered amount, and the dated triggers, at its decision date. Those facts must equal the engine's state on that date, including the line's draws and collections to that date (§7.2). **⚑⚑** marks the questions where the line's own flows can move a structural threshold, not just a figure: whether "pay" is feasible, and the day τ at which the cash-floor questions arise. Questions without a mark are court or jury decisions on the merits or the law and receive no cash, because solvency is not their standard (as `DECOMPOSITION.md` §4).

### 6.1 The list

| Id | Registry id | Actor | Decision (branches) | Asked where | Facts |
|---|---|---|---|---|---|
| J1 | `forecast_verdict_finding`, `forecast_verdict_measure` (new) | Jury | Its answers to the money-bearing questions of its verdict form (D.I. 580), each yes / no, in the form's order; J1's three branches (no award / liability without the head-start measure / claimant's theory) are composites of them (§7.12). Never an amount | Before the verdict, where no settlement was paid; each question once per sequence of earlier answers that reaches it | The form question quoted, the earlier answers, the claims and requested amounts; no cash |
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
| D7 | `forecast_financing_at_floor` (new; item 1) | Company | At the cash floor: raise equity, file, or continue ('raise equity' only where the amount available is positive) | τ inside the horizon, before any petition | ⚑⚑ cash, need, dated triggers, the equity available in the situation |
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
- The agent's mission text (agent_config, mission `akoustis_20240514`) lists the pending-claim record items of §8 as the slots to fill. The agent attaches findings to slots; neither Jev nor code does (spec §3.5).

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

---

## 8. Evidence requirements

The record items of §6.2 are named slots. The agent fills each one with accepted findings from the 14 May snapshot; a slot no finding fills is stated to Jev as not in the record (spec §3.5). All sources below are dated on or before 14 May 2024. "Kit" means already in `research/recent_cases/akoustis/`; "add" means the snapshot needs it (URLs in ACQ).

| Record item | Questions | Sources | Status |
|---|---|---|---|
| the claimant's itemised damages claim | J1, D2, D3, C2 | D.I. 543-1 Ex. A.2 (22 Apr 2024), ¶¶19, 31–32, 42, 49–50, 56–57, 61–62 | add |
| the defense's statement of intended proof on damages | J1 | D.I. 543-1 Ex. A.4 | add |
| the court's rulings admitting or limiting the damages evidence | J1, J2 | D.I. 553 (30 Apr); D.I. 566 (3 May, poaching opinions); D.I. 590 (14 May) | 553, 590 kit; 566 add |
| the final verdict form | J1 | D.I. 580 (9 May) | add (fetched for this document) |
| the summary-judgment and validity rulings | J1 | D.I. 545 (25 Apr), D.I. 557 (2 May); 10-Q Note 14 | kit |
| the defendant's public statements on the trial and its likely damages | J1, D1, D2, D5 | 13 May earnings call (Insider Monkey copy published 14 May; ACQ §1(g)); 10-Q Note 14 | call add; 10-Q kit |
| the challenges to the damages method | J1, J2 | D.I. 476 (23 Feb, redacted brief against the head-start measure); D.I. 535 (12 Apr, proposed verdict form) | add |
| the motions for judgment as a matter of law made at trial | J2, D1, D5 | D.I. 587 (13 May), D.I. 590 (14 May) | kit |
| the company's statements on its liquidity and ability to post security | J3, D2, D4, D7, D8, D9 | 10-Q Note 2 and risk factors; 13 May release | kit |
| the locations of the company's operating assets | J4 | 10-Q Note 13 (leases); FY2023 10-K (properties) | kit |
| the claimant's public statements on the litigation and its remedies | C1, C2 | D.I. 543 §6 (relief sought); D.I. 133 (second amended complaint, prayer) | add |
| the parties' competitive relationship | C1, C2, D3 | FY2023 10-K (competition); D.I. 590 ("testimony that the Parties are competitors") | kit |
| the company's statements on settlement | D3, C2 | 10-Q Note 14 (none disclosed) | kit; stated as not in the record if no finding |
| the company's own claims against the claimant | D3, C2 | 10-Q Note 14 (E.D. Tex. suit; the two IPR petitions) | kit |
| the company's going-concern and bankruptcy statements | D2, D7, D8, D9 | 10-Q Note 2, MD&A overview, risk factors | kit |
| the company's financing routes and their status | D2, D7, D8, D9, D6 | 10-Q Note 2 and Part II Item 5 (ATM re-activated, $48.0M remaining, no sales obligation); 8-K 29 Jan 2024 (the January offering); resale S-3 of 13 May; 424B5s of 2 May 2022 and 29 Jan 2024 (the shelf) | 10-Q and 8-K kit; S-3 and 424B5s add |
| the listing deficiency notice and compliance deadline; the company's stated cure | D6, H2 | 10-Q Note 12 and risk factor; 8-K 27 Oct 2023 | kit |
| the latest stockholder vote on a charter amendment | D6 | DEF 14A of 19 Sep 2023 and 8-K of 2 Nov 2023 (`STAGE3.md` E19) | add |
| the indenture's default, acceleration and suit terms | H1, H2, H3, D9 | indenture §§7.01, 7.02, 7.06, 7.07, 10.01 | kit |
| the notes' interest terms and payment record | H1, D2, D9 | 2022 notes 8-K; FY2023 10-K; resale S-3 of 13 May (5,000,000 Note Shares) | 8-K, 10-K kit; S-3 add |

**Common-model inputs (§16.3, worker A)** are not event-model slots: the 31 Mar balance sheet, the 10-Q cash flows, the 13 May guidance on revenue, burn and CHIPS credits.

**Not obtainable by 14 May, and so not slots:** the defense's damages figure; the entered pretrial order's time allocations (D.I. 549, sealed); any trial transcript; the claimant's post-verdict filings.

---

## Isolation log

- **Case facts used** (all dated on or before 14 May 2024): the 10-Q and release of 13 May; the FY2023 10-K; the 2022 notes 8-K and indenture; the 8-Ks of 27 Oct 2023 and 29 Jan 2024; docket entries dated on or before 14 May, D.I. 15–590, and D.I. 580 (fetched from RECAP, filed 9 May); and, through `acquisition_akoustis_20240514.md`, D.I. 133, 476, 535, 543, 543-1, the 13 May call and the 13 May S-3. D.I. 590 is dated the review date itself.
- **Read but not used for case facts:** the 20 Jun design documents (`DECOMPOSITION.md`, `RESEARCH.md`, `STAGE3.md`, the 20 Jun acquisition note), which state the later verdict, judgment and post-trial filings. Only their general law, their pre-14-May record items (R4's pace sample, all ruled by 2 May 2024; R7; R8; `STAGE3.md` E16 and E19) and their approved modelling rules are reused. The 20 Jun code and contracts were read for structure; their Akoustis parameter values are not reused.
- **Seen and not used:** one search of the 20 Jun docket capture printed captions of entries dated 15 May to 7 Jun, the verdict entry among them. Every later reading of the capture was filtered to entries dated on or before 14 May.
- **The verdict date** is a window drawn per trajectory from pre-14-May statements (§3), not the actual date.
- **The acquisition note** is copied verbatim; its [POST] items are marked reveal-only there and enter no slot, parameter or question here.
- **Implementation (27–28 Sep 2026).** D.I. 580 was fetched from RECAP (filed 9 May 2024) and quoted verbatim in §7.12. The figures in §5.3 and §7.12 are code's arithmetic on the 14 May feed and the case inputs. No source dated after 14 May was read for them.

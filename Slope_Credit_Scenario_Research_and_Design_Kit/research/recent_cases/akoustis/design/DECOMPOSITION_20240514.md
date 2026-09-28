# Pending money claim at jury trial: decomposition for the 14 May 2024 review (Akoustis case inputs)

## 1. Summary for approval

<!-- summary: written last, after §2–§8 -->

---

## Scope and conventions

- **Governs:** `Slope_Model_Extensions_Spec.md` §16, with §0, §2, §3 and §15 applied. §16 governs where it differs from §1–15. This document holds the event model's legal map, clocks, chains, amounts and questions. The common financial model (opening cash, operating outlook, financing, the operating reserve, legal spend, loan accounting) is §16.3 and belongs to another worker; this document only names the settings it reads.
- **Structure and discipline** follow the 20 Jun decomposition (`DECOMPOSITION.md`): stage order of spec §15.2(1), bases per spec §0 (**Law**, **Record**, **Data**, **Arithmetic**, **Jev**), and dispositions per §15.2(3) (sourced, **Bounded** open term with a base and a sensitivity, **Code timing**, or a Jev question). `Lx` are the legal slots of §2.5. `Jx` (court or jury), `Cx` (claimant), `Dx` (debtor) and `Hx` (holders) are the Jev questions of §6; `B1`–`B10` are indenture nodes.
- **Generic first.** Every node is a template keyed by forum and instrument. The borrower, the claimant, the claims, the court, the instrument and every date are case inputs. The Akoustis column shows how the case fills the template and is never read by code.
- **Isolation.** Case facts come only from sources dated on or before 14 May 2024: the 10-Q and earnings release of 13 May (`akts_2024q3_10q`, `akts_2024_05_13_*`), the FY2023 10-K, the 2022 notes 8-K and indenture (`notes_2022_ex41`), the 8-Ks of 27 Oct 2023 and 29 Jan 2024, the docket through 14 May, and D.I. 15–590, plus D.I. 580 (the blank final verdict form of 9 May, fetched from RECAP for this document). General law is used whatever its date. From the 20 Jun documents only their settled general law is reused (§2.5). The isolation log is at the end.
- **Sources:** `D.I. n` for court filings; SEC file names as in the kit folder; `DECOMPOSITION.md` §n for the 20 Jun document; `R2x`, `R4` for `RESEARCH.md`.

---

## 2. Legal decision map

The map has four templates. Each node gives its rule and modality, trigger, condition and disposition. The Akoustis entries show the case inputs.

### 2.1 T-P. Pending federal civil money claim at jury trial (`pending_money_claim`, forum `court`)

The template starts where the 4.0.0 template (`federal_post_judgment`) assumed a judgment already existed. It runs verdict → judgment → the 30-day automatic stay → post-trial motions → enforcement or stay → registration, and it hands dated cash effects to the same Chain steps. **A missing judgment never activates enforcement:** every enforcement, stay, registration and judgment-default node has the entered money judgment on the path as its trigger, and no such node exists on a path whose verdict leaves no money judgment.

| Node | Rule (modality) | Trigger and condition | Akoustis at 14 May | Disposition |
|---|---|---|---|---|
| P1 Verdict | Jury finds each claim by a preponderance (Seventh Amendment; FRCP 48, unanimous unless stipulated) (may) | Trial under way, claims submitted on a special verdict form | Trial began 6 May (minute entries); jurors provided for through Fri 17 May (D.I. 550); special verdict form D.I. 580 (9 May) with five claim groups: trade secrets under the DTSA and the NCTSPA (unjust enrichment, willful and malicious, exemplary), civil conspiracy, Lanham Act false advertising, UDTPA, patent infringement ('018 and '755, damages, willfulness) | Verdict date: Code timing (§3). Outcome: **J1** (§6), per claim only where cash differs (§4.6) |
| P2 Claims removed before verdict | FRCP 50(a) (may); summary judgment FRCP 56 | A ruling before the verdict | RICO and false patent marking out on summary judgment (D.I. 545; 10-Q Note 14). Patent validity decided for the claimant (D.I. 557). No compensable UDTPA damages may go to the jury (D.I. 590, 14 May) | Record |
| P3 Damages | The jury fixes the amount per claim on the form (D.I. 580 Q1(b), 1(d), 2(c), 3(b), 4(c), 5(b)) | A liability finding with a damages answer | No figure fixed by the record: the 10-Q says the claimant seeks damages "in an unspecified amount" | Amount: declared classes (§5); never Jev |
| P4 Exemplary and enhanced relief | DTSA exemplary ≤ 2× (18 U.S.C. §1836(b)(3)(C)); N.C. punitive cap (§1D-25(b)); patent enhancement ≤ 3× on willfulness (35 U.S.C. §284) (may); fees (§1836(b)(3)(D), §66-154(d), 35 U.S.C. §285) (may); pre-judgment interest (§24-5(b); *Devex*); UDTPA trebling (§75-16) (shall, once damages are assessed) | A finding that opens the remedy | D.I. 580 asks exemplary damages (Q1(d)) and willfulness (Q5(c)). D.I. 590 removes UDTPA damages, so §75-16 has no UDTPA amount to treble (L15) | Folded into the damages class (§5.1): each only changes the amount, and an amount moves cash only by crossing a class threshold |
| P5 Judgment entry | FRCP 58(b)(2): on a special verdict "the court must promptly approve the form of the judgment, which the clerk must promptly enter" (shall) | A verdict with a money award | — | Code timing, Bounded (L14) |
| P6 Automatic stay | FRCP 62(a): execution stayed 30 days after entry (shall) | Entry | — | Law |
| P7 Post-trial motions | FRCP 50(b), 52(b), 59(b), 59(e): within 28 days of entry (may); FRAP 4(a)(4)(A) tolls the appeal clock to the order disposing of the last of them; they do not stay execution (2018 Advisory Committee Note to Rule 62) | A money judgment the debtor contests | The debtor made two Rule 50(a) motions at the close of the claimant's case (D.I. 590), which preserves a Rule 50(b) motion | Filing: **D1**. Ruling date: Code timing (Data, R4). Content: **J2**, compacted (§4.6) |
| P8 Execution | FRCP 69(a)(1), state procedure (may) | Entered, past the Rule 62(a) stay, unstayed, unpaid | Delaware forum; operating assets in NC and NY (L9) | **C1** |
| P9 Stay by security | FRCP 62(b): "a party may obtain a stay by providing a bond or other security", effective on approval (may) | Any time after entry | — | Full bond: Law, a stay as of right (L16). Lesser security: **J3** (L6). Motion: part of **D2** |
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
| B1 Judgment default | §7.01(i): final money judgments "undischarged, unpaid or unstayed" for 60 days "during which execution shall not be effectively stayed"; aggregate above $10.0M "excluding amounts covered by insurance"; only "after notice to the Company by the Trustee or the Holders of at least 25%" (shall, on notice) | An entered money judgment above the threshold on the path, not paid, not effectively stayed at the ripe date | The judgment does not exist yet. Its amount is the path's class (§5): below $10.0M it never triggers B1. Insurance: $0 (R7 applies at 14 May: the 10-Q and FY2023 10-K disclose none) | Threshold and notice: Law. Which judgment starts the 60 days: **OPEN, both readings carried as the recorded model has them (L11)**. Clock: Code timing from the modeled dates. Notice: **H1** |
| B2 Delisting default | §7.01(b): "the Common Stock is not listed on any Eligible Market"; no notice, no grace (shall) | "Not listed" (L12) inside the horizon | Only one route falls inside the horizon (§2.3) | Law; date Code timing |
| B3 Cross-default | §7.01(h): other debt of $2.5M or more accelerated or in payment default | The GDSI seller note's partial prepayment falls in Jan 2025 (10-Q) | After the horizon on every trajectory | Removed (window closed) |
| B4 Interest default | §7.01(c): 30 days late | The 15 Jun 2024 coupon is inside the horizon; a default could come no earlier than 15 Jul | The coupon is §16.3's (below). Whether the company pays it is not a dispute decision | Not a separate node: nonpayment is inside the company's distress decisions (D2, D4, D8, D9) |
| B5 Acceleration | §7.02: the Trustee or 25% "may" declare; automatic on a bankruptcy petition | An Event of Default | — | **H1**, **H2** |
| B6 Rescission | §7.02: majority, once every default is cured or waived; a judgment default is cured by payment, discharge or a stay | After acceleration | — | Law |
| B7 Repurchase | §10.01: holders' put at 100% plus interest on a Fundamental Change (delisting); repurchase 20–35 business days after the company's notice, itself due within 20 business days | Delisting | The only in-horizon delisting falls on 1 Nov (§2.3); the repurchase date falls after 10 Nov on every trajectory | Removed (window closed). H2 becomes binary |
| B8 Interest | §16.02: payable semi-annually on 15 June and 15 December (2022 notes 8-K), in shares unless the company elects cash (§16.02(c)); §9.02(k) caps shares at 11,403,332 without a stockholder vote | 15 Jun 2024 inside the horizon; 15 Dec after it | The 20 Jun parameter `coupon_cash_share` uses a $0.20 share price and 3.0M share capacity: both are post-14-May facts and must not be used. At 14 May the pre-D facts are the $0.60 close on 1 May (10-Q), 175,000,000 authorized and 98,654,282 outstanding at 31 Mar (10-Q balance sheet) | A common borrower input (§16.3 "Ordinary obligations"), set by that worker on pre-14-May facts |
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
| L1 Pre-judgment interest on unjust enrichment (§24-5(b)) | Changed | Reused law (R2e, `STAGE3.md` L1). It changes only the amount, so it is folded into the class (§5.1). No node |
| L2 Trebling (§75-16) | Changed | D.I. 590 (14 May) holds that "the jury has no reasonable basis to find compensable damages under the UDTPA". §75-16 trebles "the amount fixed by the verdict", so a verdict can carry no UDTPA amount to treble (L15). Any later argument to treble another claim's award changes only the amount: folded into the class. No node |
| L3 Election between treble and punitive damages (*Kuykendall*) | Changed | Amount only: folded. No node |
| L4 Exemplary caps (§1D-25(b); DTSA 2×); a new trial takes the exemplary award | Changed | Amount only: folded. The rule that exemplary damages fall with the compensatory award (*Carawan*; `STAGE3.md` L4) is Law inside J2's "set aside" |
| L5 JMOL, new trial, remittitur (*Lightning Lube*; *Roebuck*; *Gumbs*; *Kazan*; *Hetzel*) | Reused | SETTLED standards → Law; they are J2's standard. Remittitur into a lower class is not a branch (§4.6) |
| L6 Stay on lesser security (*Dillon*/*Poplar Grove*) | Reused | SETTLED standard; the grant is **J3**, asked only where no trajectory funds a full bond |
| L7 Bond amount (*Southern Track & Pump*) | Reused | SETTLED: the full judgment plus interest and costs; no D. Del. multiple. Collateral 100% (80% lower bound) as `DECOMPOSITION.md` §2 |
| L8 Amended judgments; §1961 | Changed | (a) the Rule 62(a) restart on an increase: amount only, folded. (b) §1961 runs from the modeled entry date on the path's class amount. The rate for the week before the modeled entry is not published by 14 May: **Bounded** base, the latest published weekly 1-year CMT at 14 May (H.15, week ending 10 May 2024); sensitivity none needed (within a class it moves under $0.1M in the horizon; beyond cash it moves nothing) |
| L9 §1963 good cause; share attachment | Reused | SETTLED standard (*Associated Bus. Tel.*); grant is **J4**. Base: no cash reachable before registration where it sits; sensitivity all consolidated cash |
| L10 Injunction | Changed | No proposed order exists by 14 May and no part-level revenue is disclosed (R8). Its cash effect cannot be obtained: excluded from cash, not asked |
| L11 "Final judgment" in §7.01(i) | Reused as recorded | **OPEN** (R2j; New York law; no New York decision). Both readings carried, as the recorded model has them (`judgment_default_reading` = both): **entered reading**, the 60 days run from the end of the Rule 62(a) stay of the judgment as entered (modeled entry + 30 + 60); **post-ruling reading**, from the order disposing of the last pending tolling motion (modeled ruling + 60), on the amount that survives. Sensitivities: entered only; post-ruling only |
| L12 Nasdaq hearing stay; "not listed" | Reused, consequence changed | SETTLED (mid-2024 rules): a timely hearing request stays suspension; the Panel may extend up to 180 days. Bounded: "not listed" at suspension (base) or on Form 25 effectiveness (sensitivity). At 14 May every Panel decision and every Form 25 falls after the horizon (§2.3) |
| L13 §303(b); §547(c)(2) | Reused | SETTLED as `DECOMPOSITION.md` L13 |
| L14 Judgment entry after a special verdict | New | FRCP 58(b)(2): "the court must promptly approve the form of the judgment, which the clerk must promptly enter". **Bounded:** base, entry on the first business day after the verdict (the earliest reading of "promptly", lender-adverse because every enforcement clock starts earlier); sensitivity, entry deferred until the court rules on the post-verdict equitable remedies, dated as a post-trial ruling (§3). No pre-verdict record sets it |
| L15 UDTPA damages at the verdict | New | SETTLED by the court's own order: D.I. 590 grants the Rule 50(a) motion as to damages. The UDTPA claim can yield a liability answer without an amount (D.I. 580 Q4) |
| L16 Stay on a full bond | New | SETTLED: a debtor who posts a sufficient supersedeas bond obtains a stay as of right (*American Mfrs. Mut. Ins. Co. v. American Broadcasting-Paramount Theatres, Inc.*, 87 S. Ct. 1, 3 (1966) (Harlan, J., in chambers); FRCP 62(b), 2018: "a party may obtain a stay by providing a bond"). So approval of a fully collateralized bond is Law, not a question; J3 is asked only for lesser security |
| L17 Post-trial motion deadlines and effect | New | SETTLED: Rules 50(b), 52(b), 59(b) and 59(e) motions are due 28 days after entry; timely ones toll the appeal clock (FRAP 4(a)(4)(A)); they do not stay execution (Rule 62, 2018 Advisory Committee Note; R2h). A fee motion (Rule 54(d)(2), 14 days) tolls only on a Rule 58(e) order |
| L18 After a verdict with no money award | New | SETTLED in effect for the horizon: a claimant's Rule 59 new trial or Rule 50(b) motion after a defense verdict can yield money only after a retrial or a separate damages determination, which cannot fall by 10 Nov: the motion is ruled on no earlier than entry + 28 + 21 days plus the court's measured lag (§3), and this court last set a trial twelve months out (D.I. 198, 10 May 2023, setting 6 May 2024). No money judgment on any trajectory: the claimant's post-trial questions are not asked |

<!-- next -->

# Model extensions for the Akoustis and Charles & Colvard cases (spec, revision 2)

**Status:** revision 2, 25 Sep 2026: forecasts rebuilt as chains that start from the lender's question; round 2 acquisition corrections applied. Scope for the proof of concept and the corrections that bind Stage 1 are in §15, which governs where it differs from §1–14.

**Scope:** every change to the model, contracts, engine, agent and page needed to move the demo from ChromaDex to:
- **Akoustis Technologies**, decision date (D) 20 Jun 2024, lead case (Owen's choice);
- **Charles & Colvard**, D 18 Nov 2024, second case.

**Governing documents:** where this spec differs from `Slope_Credit_Scenario_Build_Specification.md`, this spec governs these changes. The alignment document governs intent.

**Evidence base:** the round 2 acquisition notes, `acquisition_akoustis_20240620.md` and `acquisition_cthr_20241118.md`, with their source folders. They move into the kit in delivery step 2 (§13).

**How decision dates are set:**
- D falls just after the filing that brings the public record up to date. It is justified by that filing, never by when the known outcome lands.
- The dispute is live, with its next steps inside the 180-day horizon.
- The borrower looks stressed but fundable on bank data alone.
- The event's cash can reach Slope's exposure while that exposure is outstanding.

**Why Akoustis at 20 June:**
- The verdict is in. Every post-trial motion, the injunction, fees, interest and trebling are pending.
- Bank data looks fundable after the May share sale.
- The research's warning comes about six months before the 16 Dec 2024 petition, which falls inside the horizon (it ends 17 Dec).
- A single 90-day draw would be repaid by September, so the loan is Slope's reusable line (§2.1).

---

## 0. What does not change, and one new rule

- **Jev** gives present-state readings and forecast probabilities. It never sets an amount, a date or a path.
- **Code** owns every window, amount, date draw and composition. Every structurally feasible path is simulated, and stress is a separate view.
- **Only impossibility changes structure:** an event that has already occurred, a payment, or arithmetic that rules a branch out on every trajectory of that path. Everything else is a fact or evidence for a question.
- **Unknown is a typed state, never zero.** Contractual, path-conditioned and probability-weighted series stay distinct.
- **The two views share operating draws.** Isolation is unchanged.
- **New: every causal link has a stated basis.** Each step between the evidence and a number is labelled **Law**, **Record**, **Data**, **Arithmetic** (code), or **Jev**. A link with no basis is a defect, not a simplification. "Common sense" is not a basis.

---

## 1. The cases

### 1.1 Akoustis Technologies (AKTS, CIK 1584754; a Delaware corporation), D = 20 Jun 2024

**Dispute:** *Qorvo, Inc. v. Akoustis Technologies*, D. Del. 1:21-cv-01417, Judge McCalla (CourtListener docket 60622376). The counterparty, Qorvo, is a competitor.

**State at D:**

| Item | Record |
|---|---|
| Judgment entered 20 May 2024: **$38,595,023** | D.I. 602. $31,315,215 unjust enrichment (37 trade secrets, "willful and malicious"), $7,000,000 exemplary, $279,808 patent damages. |
| No stay of execution on the docket | D.I. 602–605. The Rule 62(a) automatic stay ended 19 Jun. |
| Akoustis: judgment as a matter of law (sealed); new trial or, alternatively, **remittitur to $305,000** (its own expert's figure) | D.I. 607 (sealed), D.I. 613 |
| Qorvo: amend the judgment to **$93,945,645** by trebling under N.C. Gen. Stat. §75-16 (the jury answered "No" on that claim; Qorvo argues trebling is automatic) | D.I. 611 |
| Qorvo: attorneys' fees of **$12,116,123.30** under the DTSA and the North Carolina trade-secret act, plus fees under the unfair-trade-practices statute if trebled | D.I. 618 |
| Qorvo: pre- and post-judgment interest (sealed) | D.I. 615 |
| Qorvo: permanent injunction. Worldwide ban on using its trade-secret information (including BAW designs and trimming methods) and on selling products made with it; US ban on 19 part numbers; quarantine by a vendor Qorvo approves, at Akoustis's cost; five years of audits | D.I. 608, 608-1 |
| Briefing: answering briefs 25 Jul, **replies 8 Aug** (signed order) | D.I. 605 |
| Company: judgment "in excess of our liquid assets"; the final judgment is still to be rendered; without financing, it "will be required to curtail or cease operations and/or seek protection under applicable bankruptcy laws" | 424B5, 23 May; 8-K, 20 May |
| Funding: ATM suspended 22 May; $0.20 registered direct, $9.3M net, 24 May; 45-day issuance lock-up; about 3.0M authorized shares left, so any increase needs stockholder approval | 8-Ks 22 and 24 May; 424B5 |
| Notes: $44M, 6%, due 2027. **Judgment default:** a *final* judgment above $10.0M, undischarged, unpaid or unstayed for 60 days, *after notice by the trustee or 25% of holders* (§7.01(i)). **Delisting is a Fundamental Change:** holders may require repurchase at 100% plus interest, in cash, on a date the company sets 20–35 business days after its notice; failing to repurchase is a default. Interest of $1.32M is due 15 Dec, in cash or shares | Indenture, 2022 8-K Ex. 4.1 |
| Nasdaq bid-price deadline **21 Oct 2024**; the company is considering a reverse split | 424B5 |
| Leverage: Akoustis's own suit against Qorvo (E.D. Tex. 2:23-cv-00180, at claim construction); two patent-review petitions against its patent, institution expected around September–October | 10-Q, 13 May; earnings call, 13 May |
| Cash: $15.2M at 31 Mar; burn-cut plan; CHIPS Act refundable credits of $2.8–4M expected within 9–12 months | 10-Q and earnings call, 13 May |

**Snapshot sources:** all in `sources_akoustis_20240620/`, all dated on or before D:
- 10-Q, 13 May 2024.
- 8-Ks of 29 Jan, 13 May, 20 May, 22 May (with Ex. 99.1 and 99.2) and 24 May (with Ex. 4.1, 10.1 and 10.2), and of 27 Oct 2023.
- 424B5 of 23 May and 424B3 of 24 May.
- The 2022 notes 8-K and indenture.
- SEC correspondence of 17 May.
- The conflict-minerals report of 31 May.
- Qorvo's 8-K and press release of 20 May.
- RECAP: D.I. 601, 602, 604, 605, 608, 611, 613, 616 and 618.
- To add: the 13 May earnings-call transcript (the source for the CHIPS credits and the burn cut). It is public, but not yet in the folder.

**Reveal only:**
- press releases from 27 Jun onward;
- D.I. 622 onward;
- the FY2024 10-K;
- every 8-K after D;
- the Chapter 11 materials.

**Isolation probes:** "Tune Holdings", "SpaceX", "6,589,064", "11,7" (fee award), "October 7, 2024", "D.I. 709", "petition for relief under chapter 11 … December".

**Bank feed:** synthetic, labelled once (Decision D1, approved: proration).
- **Anchors:** quarterly figures through 31 Mar 2024, and the dated offering proceeds.
- **1 Apr to 20 Jun:** anchored to the April–June quarter as later reported (receipts, outflows and the $24.4M 30 Jun balance), prorated to 20 Jun by business days. Labelled once: "anchored to balances later reported for periods before D". A connected bank feed would have shown these flows at D.
- **Nothing after 20 Jun enters the feed.**

**Categories:**
- customer receipts;
- payroll;
- fab materials and Tai-Saw (tagged `supplier_invoice`);
- legal fees;
- other operations;
- notes interest (tagged `debt_service`).

### 1.2 Charles & Colvard (CTHR, CIK 1015155), D = 18 Nov 2024

**Dispute:** *Wolfspeed v. Charles & Colvard*, a confidential AAA arbitration under the 2014 exclusive supply agreement (North Carolina law; AAA Commercial Rules; hearing in Durham). The counterparty, Wolfspeed, is its sole supplier.

**State at D:**

| Item | Record |
|---|---|
| Hearing held 30 Sep–2 Oct 2024; award pending | NT 10-Q, 15 Nov |
| Claims: $4.25M minimum-purchase shortfall; $3.30M delivered crystals (already booked as a payable); $18.5M anticipatory breach | 10-Q, 6 May |
| Contract terms: a missed minimum leads to cure, then Wolfspeed may end exclusivity or the agreement (¶8–9); arbitrators may not award remedies the agreement expressly excludes (¶16); the prevailing party recovers attorney fees (¶16(b)); unpaid invoices bear interest at a redacted rate (¶6(c)); Wolfspeed holds a security interest for product invoices (¶6(c)) | Supply agreement (8-K Ex. 10.1, 2014) and amendments |
| Term ends 29 Jun 2025; about $24.75M of the commitment unpurchased at 31 Mar 2024 | Second amendment; 10-Q, 6 May |
| JPMorgan line: $5M, $2.3M drawn, secured by a $5.05M deposit, **matures 31 Jan 2025**. Defaults include "any attachment, seizure, sequestration, levy, or garnishment" and a material adverse change; remedies include setoff | 8-K 12 Nov 2024 (Ex. 10.1); credit agreement 2021 §7 |
| Listing: late-filing notice; plan due to Nasdaq **17 Dec 2024**; the late 10-K makes the new S-3 unusable (an inference from the S-3 eligibility rules) | 8-K 24 Oct; S-3 (June) |
| Going-concern doubt; FY2024 sales $22.5M, down 25% | NT 10-K, 1 Oct; NT 10-Q, 15 Nov |
| Proxy contest: Riverstyx, about 9.9%, three nominees | 13D/A; DFAN14A, 1 Oct |

**Snapshot sources:** `sources_cthr_20241118/`, all dated on or before D.

**Reveal only:**
- the interim award (11 Dec 2024), which states the expectation claim as $22.8M;
- the settlement agreement and all later filings.

**Isolation probes:** "interim award", "22.8 million", "4,770,000", "4.77 million", "Ethara", "delist".

**Bank feed:** anchored on the pre-D FY2024 sales of $22.5M, and on the balances at 30 Jun and 30 Sep 2024 that were later reported. After 30 Sep, the October–December quarter is prorated to 18 Nov by business days (Decision D1, approved). Labelled once.

### 1.3 ChromaDex: removed (Decision D2)

ChromaDex and everything specific to it leave the repository, in one reviewable commit in `step-6a-cases`. It lands in the same PR that brings in Akoustis, so the suite always runs against a real case.
- **Deleted:**
  - `cases/chromadex_20240819/`;
  - `runs/recorded/chromadex_20240819-*`;
  - `research/recent_cases/chromadex/` in the kit;
  - its `sources.json` entries, and the manifest refreshed to match;
  - the `chromadex_elysium_2024` mission block in `agent_config.json`;
  - its keys in `app/agent/mission.py` and `app/evidence/__init__.py`;
  - generated `var/` artifacts.
- **Rewritten on Akoustis facts:**
  - `tests/test_analysis.py` (bank-feed fixture and parties);
  - `tests/test_dispute_reading.py`;
  - the ChromaDex isolation probe in `tests/test_evidence_snapshots.py` (replaced by the Akoustis and Charles & Colvard probes);
  - the Elysium counterparty in `tests/test_investigation_tools.py`.
- **Documents:**
  - CLAUDE.md and the build spec (§6 cases) are updated;
  - the alignment document's case section is Owen's document, so its revision is proposed to him, not made by the agent.
- **Now, on `step-5c-channels` (unpushed):** the two Western Alliance commits (e010dd3, b82419e) are dropped, so the rubric PR carries no ChromaDex additions.
- **Kept, because nothing in it is ChromaDex-specific:** the generic dispute machinery built during the ChromaDex work (interpretation, forecasting, the engine).

---

## 2. The loan: Slope's reusable line

### 2.1 Terms and draws

**Terms at D.** The run inputs, written by `cases/<case>/make_run_inputs.py`, apply Slope's published rule to the bank feed:
- **Limit:** 15% × (mean monthly customer receipts − mean monthly `debt_service`), over the trailing three complete months. 15% is the low end of Slope's 15–33% range, used for a stressed profile.
- **Fee and schedule:** 3.7%, in 3 equal monthly installments.
- **Label, once:** "Terms reconstructed by applying Slope's published sizing rule to the connected-bank baseline."

**Reassessment.** Slope reassesses the limit with the same rule at every draw date, on each trajectory's simulated trailing receipts. This is Slope's own bank-data response. The rule reads receipts net of debt service, which dispute cash does not change, so the reassessed limit is effectively blind to the dispute. That is measured in §2.4, not assumed.

**Draws.** Each simulated `supplier_invoice` outflow is routed through Slope when all of these hold:
- outstanding + the invoice ≤ the day's limit;
- no installment is overdue (a labelled model rule);
- no petition has been filed.

**Usage.** `line_usage` is 100% by default. Rationale, stated once: at D the borrower has no cheaper credit. Its equity costs a registered direct sale at $0.20 a share (Akoustis), or is shut off by late filings (Charles & Colvard).

**A routed invoice** leaves the borrower's outflows that day and creates 3 installments of (invoice × 1.037) / 3, in whole cents. Installments due after the horizon are **contractual, not yet due**: exposure, not loss.

### 2.2 Collections

On each due date, and at each month-end while anything is overdue, Slope collects `min(owed, max(0, available − need))`:
- `need` is the lowest point of that trajectory's own operating flows over the next 30 days.
- Rationale, stated once: a borrower near its operating floor protects payroll and suppliers, and an ACH debit fails when the funds have been moved.
- With zero need, the rule reproduces the previous one exactly.

### 2.3 Petition

From the petition date `p`:
- **Collections stop** (11 U.S.C. §362). Operating flows continue.
- **Outstanding at `p`** becomes a **stayed claim**: recovery unknown and outside the horizon, never zero loss.
- **Collections dated in `[p − 90, p)`** are reported as **preference-exposed** (§547(b)(4)(A)). They are not deducted.

### 2.4 Lender outputs

- **Per view:** total drawn; peak and time-weighted outstanding; fees; dated collections; PV of all fundings and collections; principal dollar-days; stayed claim; preference-exposed collections; outstanding not yet due at the horizon.
- **Headroom at each due date:** `available − need − installment`, as P5 / P50 / P95 and the probability it is negative.
- **Backup liquidity:** available cash plus undrawn facility availability.
- **Lead time:** the expected reassessed limit (and its P5), day by day, beside the event-adjusted cumulative probability of a petition by each date and the expected exposure on those paths. Plain series, no threshold.

---

## 3. How forecasts are built: chains from the lender's question

This section replaces the node-by-node holistic forecasts of revision 1.

### 3.1 Start from what decides Slope's collections

Slope's collections stop, or fall short, in only two ways (§2.2–2.3):
- **a petition** (Law: 11 U.S.C. §362);
- **cash above operating need** too low on a due date (Arithmetic: the collection rule).

So the question behind every forecast is: **what could cause a petition, or drain the borrower's cash, while Slope's draws are outstanding?** Each case begins with that list, each item sourced (§4.0, §5.0). Only chains that lead to one of those items are walked.

### 3.2 Walk each chain by asking how the actor decides

For each item, ask who acts and how that actor decides:
- the court: its legal standard, the grounds before it, its schedule;
- a party: its options, constraints, stated intentions and incentives;
- a lender, holder or surety: its contract rights and the facts it would weigh.

Each sub-question is answered, where possible, by:

| Basis | What answers it | Examples |
|---|---|---|
| **Law** | a rule, statute or contract term, cited | Rule 62(a)/(b) stays; FRAP 4(a)(4) tolling; N.C. Gen. Stat. §24-5(b) interest; indenture §7.01(i) |
| **Record** | a dated, cited passage from before D | the briefing schedule; the motion grounds; the company's statements |
| **Data** | measured from the record or a sourced dataset | this judge's time to rule on earlier fully briefed motions on this docket |
| **Arithmetic** | computed by code on the path | amount owed, bond size against cash, dated triggers |
| **Jev** | what remains: one actor's decision, given the facts | how the judge weighs these grounds; whether the holders give notice |

**Stop** only when the next question is no more answerable than the current one. What remains goes to Jev.

**Depth follows the lender.** Chains that reach collections are walked fully. A chain that does not reach collections is walked only far enough to show, with a basis, that it does not.

### 3.3 What code owns

- **Timing** of rulings and awards. The earliest date comes from the record (a schedule or a rule deadline). The spread comes from data (the same judge's pace on this docket), or else a labelled parameter with a sensitivity. **Timing is never a Jev probability.**
  - Discretionary acts with a legal deadline (an appeal within 30 days; a bond before execution) keep "within the deadline" in their Jev question, because the rule sets the window.
- **Amounts:** quoted components; statutory computations from quoted law; arithmetic on record figures. Where the record fixes no amount, the rules of Decisions D4 and D5 apply: data if sourced; otherwise declared bounds or scenarios, never an invented distribution.
- **Path facts given to each Jev question:**
  - the amount owed on that path;
  - available cash at the decision date (P5 / P50 across the path's trajectories);
  - the bond or collateral the law or practice requires;
  - dated contract triggers.

  Event dates are drawn independently of the probabilities, so path facts are simulated **before** Jev is asked. There is no loop.
- **Arithmetic impossibility:** a branch that no trajectory of the path can take (e.g. paying in full an amount greater than the path's maximum cash) is dropped. A branch that is merely unlikely is not.

### 3.4 What Jev answers: one actor's decision

Each Jev question names:
- the actor;
- the decision;
- the legal standard or contract terms;
- the record items;
- the path facts.

**Outside view first.** Each node has a reference class, e.g. "renewed judgment-as-a-matter-of-law or new-trial motions after a plaintiff's jury verdict in federal trade-secret cases".
- Its rate is **sourced** where builder research finds a citable study or dataset.
- Otherwise it is a **separate, case-free Jev question**, labelled "model judgment of a reference-class rate", never a frequency.

**Then the adjustment.** Jev receives the anchor and returns the case-adjusted probability. Both numbers are stored. The adjustment is the research's measurable contribution (§9).

**Recall.** Anchor questions carry no case facts. Adjustment questions are covered by the role-name replay (§9).

### 3.5 Who does what

- **Chains are host-owned design,** versioned in the dispute model contract and reviewed like it.
- **The agent's job** is to find the record items each chain step needs. It supplies them with citations and code checks them.
- **Jev answers only the residual questions.**

### 3.6 What the page shows

Every forecast node has a **"why this probability"** chain: each step with its basis label and source link, then the anchor, then the case adjustment. Russell sees exactly where the record, the law and arithmetic end and where judgment begins.

---

## 4. Akoustis: the chains

### 4.0 What could cause a petition, or drain cash, between 20 Jun and 17 Dec 2024

| # | Threat | Basis |
|---|---|---|
| T1 | Qorvo enforcing a money judgment against Akoustis | Record: judgment D.I. 602. Law: Rule 62(a)–(b) |
| T2 | Noteholders accelerating or demanding repurchase of $44M | Record: indenture §7.01(i), §7.02; Fundamental Change repurchase |
| T3 | Operating cash running out | Data: the bank feed. Record: going concern, burn plan |
| T4 | A dated cash call: notes interest of $1.32M on 15 Dec | Record: indenture |
| T5 | Akoustis choosing to file | Record: its statements (8-K 20 May; 424B5) |

Each is a chain below.

### 4.1 Chain T1: will Qorvo be able to enforce, and what does Akoustis do?

| Step | Question | Actor | Basis and source |
|---|---|---|---|
| 1 | Is the 20 May judgment enforceable now? | — | **Law:** Rule 62(a), a 30-day automatic stay, ended 19 Jun. **Record:** no stay on the docket. So execution may issue unless Akoustis obtains a stay (Rule 62(b)). |
| 2 | Will Qorvo seek execution **before** the post-trial ruling? | Qorvo | **Record:** Qorvo's own motions ask to increase the judgment (D.I. 611) and add fees (D.I. 618). **Path facts:** the judgment against cash. **Jev residual:** Qorvo's choice to execute now or wait. Outside view: judgment creditors executing while post-trial motions are pending. |
| 3 | Could Akoustis stay execution before the ruling? | Akoustis, surety, court | **Law:** Rule 62(b), bond or other security, approved by the court. **Arithmetic:** bond amount (judgment × the district-practice multiple, a labelled parameter) against cash on the path. **Record:** "in excess of our liquid assets". **Jev residual:** whether a stay is obtained (surety willingness and court acceptance of alternative security, given those facts). |
| 4 | When does the court rule? | Court | **Record:** not before 8 Aug (D.I. 605). **Data:** Judge McCalla's time from reply brief to ruling on earlier fully briefed motions on this docket (the agent extracts the dates). **Code:** timing distribution from 8 Aug; no ruling in the horizon if it lands past 17 Dec. **No Jev.** |
| 5 | What does the ruling decide? | Court | See chain 4.2. It produces the path's final-judgment amount and date. |
| 6 | When is the final judgment enforceable? | — | **Law:** Rule 62(a) (30 days after entry); FRAP 4(a)(4)(A) (appeal time runs from the order disposing of the motions). **Code:** dates. |
| 7 | Will Akoustis appeal? | Akoustis | **Record:** it plans to appeal "if required" (8-K 20 May). **Jev residual**, within the 30-day deadline. It matters to cash only through the stay (step 8). |
| 8 | Will Akoustis stay execution pending appeal? | Akoustis, surety, court | As step 3, with the final-judgment amount on that path. **Jev residual.** |
| 9 | If enforceable and unstayed: pay, settle or file? | Akoustis | **Arithmetic:** paying in full is dropped on any path where the amount exceeds the path's maximum cash. **Record:** its statements (curtail or seek protection without financing); funding constraints (ATM suspended, lock-up, authorized shares). **Jev residuals:** settle with Qorvo on this path? file? Outside view for each. |
| 10 | If unstayed and unpaid: does Qorvo enforce, and when? | Qorvo | **Law:** Rule 69 execution. **Code:** timing window after the stay ends. **Jev residual:** enforcement against this debtor, given the path facts. |
| 11 | When would Akoustis file? | Akoustis | **Code:** date drawn between the final judgment (or step 2 execution) and the end of the enforcement window. **Jev** decided whether, in step 9. |

**Settlement** is asked once per interval between milestones, never over a fixed window:
- before the ruling;
- between the ruling and the appeal deadline;
- during an unstayed appeal;
- during a stayed appeal.

The **actors** are both parties. The **record** is:
- no disclosed talks;
- Akoustis's Texas suit and the two patent-review petitions as leverage;
- Akoustis's stated intent to contest.

**Path facts:** the amount and the cash on that path. The amount and timing follow Decision D5: data if sourced; otherwise declared scenarios within the payer's feasibility bound on that path. Jev never sets them. **Jev residual:** each interval.

### 4.2 Chain T1, step 5: what the post-trial ruling decides

**Timing.** The motions share one briefing schedule (Record: D.I. 605), but nothing in the record says they will be decided together.
- **Code** draws a ruling date for each motion from the same judge-pace distribution (chain 4.1 step 4), independently of one another. This independence is a labelled simplification; the sensitivity is one common date.
- **Law:** the final-judgment date on a path is the date of the last order that disposes of a motion changing the judgment (FRAP 4(a)(4)(A)).

**Content.** Each component is a separate court decision with its own standard.

| Component | Standard (Law) | Record | Amount if granted | Residual |
|---|---|---|---|---|
| **Liability survives judgment as a matter of law** | Rule 50(b): whether a reasonable jury had a legally sufficient evidentiary basis | Grounds in D.I. 613 (the damages expert's method; the 55-month head start; the verdict form; the patent simulations); D.I. 616 transcript excerpts. The judgment-as-a-matter-of-law brief is sealed | vacated → no judgment on that claim | Jev: the judge's decision on these grounds |
| **New trial on damages, or remittitur** | Rule 59. A remittitur must be offered to the plaintiff, who may refuse and take a new trial (Seventh Amendment; *Hetzel v. Prince William County*, 523 U.S. 208 (1998)). The remitted amount is what the court finds the evidence supports, under Third Circuit law (**to cite**) | D.I. 613: new trial on damages, or remittitur to $305,000 | new trial → no judgment amount in the horizon. Remittitur → the amount per Decision D4: first by arithmetic on the trial record (e.g. the head-start months the evidence supports, R5), then data. Otherwise the two record figures as declared scenarios. Never assumed to be $305,000 | Jev: the court's choice among "no change / remit / new trial". Then **Qorvo**'s choice to accept the remittitur or take a new trial (a separate actor, a separate Jev question, with the remitted range as a path fact) |
| **Exemplary damages ($7.0M)** | North Carolina caps under ch. 1D (**to cite**); the court's review | Part of D.I. 602; any challenge in the sealed briefs | $7.0M kept or struck | Jev, only if the record shows a challenge; otherwise it stays as entered (Record) |
| **Trebling (D.I. 611)** | N.C. Gen. Stat. §75-16 (treble "shall" follow a UDTPA violation); whether the jury's "No" forecloses it is a legal question | D.I. 611's argument; the verdict form | +$62.6M | Jev: the court's decision |
| **Fees (D.I. 618)** | 18 U.S.C. §1836(b)(3)(D) and N.C. Gen. Stat. §66-154(d): the court "may" award fees for willful and malicious misappropriation (Record: the jury so found) | D.I. 618 | ≤ $12,116,123.30 requested. The amount follows Decision D4: data if sourced, otherwise the requested amount, labelled | Jev: award or not |
| **Pre-judgment interest** | N.C. Gen. Stat. §24-5(b): compensatory damages in a non-contract action bear interest from commencement (4 Oct 2021) at 8% (§24-1). Whether unjust-enrichment damages count as "compensatory" here is a legal question. Exemplary damages bear none | D.I. 615 (sealed) | **Arithmetic:** 8% × days × the compensatory amount surviving on that path (≈ $6.58M on $31.3M to 20 May) | Jev: whether awarded on unjust-enrichment damages |
| **Post-judgment interest** | 28 U.S.C. §1961 | — | **Arithmetic:** the statutory §1961 rate for the week before the judgment date on the path (the Treasury series is sourced in Stage 3) | none (Law) |
| **Injunction (D.I. 608)** | Permanent-injunction factors (*eBay v. MercExchange*, 547 U.S. 388 (2006)) for the patent claims; trade-secret injunction standard (**to cite**) | D.I. 608-1 terms; Akoustis: its patent redesign is "not affected" (22 May). Nothing on the trade-secret scope | **Unquantified:** no source states the revenue affected or the compliance cost. Shown, not in cash, with an evidence request | Jev: grant or not (shown on the page; no cash) |

**How the pieces combine.** Each component's residual question is asked under the assumptions of the earlier components' outcomes on that path, in the order of the table. For example, trebling is asked assuming liability survived. Code multiplies these conditional probabilities: the chain rule, exact given that conditioning. Each path carries its final-judgment amount = the surviving components.

**Is the amount's size decision-relevant to the lender?** That is shown, not assumed. §2.4's outputs and the 0% / Jev / 100% sensitivity reveal whether, for example, trebling changes collections on any path. The model never asserts that a larger amount "doesn't matter".

### 4.3 Chain T2: the notes

| Step | Question | Actor | Basis |
|---|---|---|---|
| 1 | Which judgment starts the 60-day clock in §7.01(i)? | — | **Law and contract:** "a final judgment". **Record:** at D the company treats the final judgment as still to come (424B5: "prior to the rendering of a final judgment"). **Code:** the clock starts at the final judgment on the path (chain 4.2). |
| 2 | Would holders treat the 20 May judgment as final and give notice sooner? | Holders (25%) | **Jev residual**, given the indenture text, the company's reading and the path facts. Outside view: holders asserting a judgment default while post-trial motions are pending. |
| 3 | Default date on a path | — | **Code:** final judgment + 60 days, if still unpaid and unstayed (from chain 4.1), and notice given (step 4). |
| 4 | Do the trustee or holders give notice and accelerate? | Holders | **Record:** §7.02; majority rescission rights. **Path facts:** amount, cash, stay status. **Jev residual.** |
| 5 | Delisting | Nasdaq, company | **Record:** bid-price deadline 21 Oct; a reverse split under consideration. **Law:** a Delaware charter amendment needs stockholder approval (DGCL §242); a hearing request stays suspension (Nasdaq Rule 5815, **to confirm**). **Code:** the delisting date on a path. **Jev residual:** whether a reverse split is approved and effective before the deadline. |
| 6 | Repurchase | Holders, company | **Record:** repurchase in cash at 100%, 20–35 business days after the company's notice; failing to pay is a default. **Arithmetic:** $44M plus interest against cash on the path; paying in full is dropped where impossible. **Jev residual:** whether holders exercise (given the path facts), then the company's response (chain 4.1 step 9's file-or-settle question, in the repurchase context). |

### 4.4 Chains T3 and T4: operating cash and the December interest

- **Code:**
  - the bank-feed bootstrap;
  - the notes interest of $1.32M on 15 Dec, paid in cash (Record: the share route is constrained by the authorized-share limit; a sensitivity covers share payment);
  - the CHIPS Act credits, $2.8–4M, dated uniformly over the stated 9–12 months (Record, the earnings call; labelled parameter; sensitivity);
  - legal spend continuing until the dispute resolves on the path (chain 6.4).
- **No Jev.** A petition driven purely by operating cash is not modelled as a Jev event. It shows as a cash shortfall and as missed collections.

### 4.5 The resulting Jev questions (Akoustis)

About 20–30 residual questions, each one actor's decision with its facts, depending on path contexts. Plus about 12–15 case-free outside-view anchors, shared and cached.

**What is no longer Jev's:**
- any timing;
- any amount;
- affordability;
- whether a judgment is enforceable.

---

## 5. Charles & Colvard: the chains

### 5.0 What could cause a petition, or drain cash, between 18 Nov 2024 and 17 May 2025

| # | Threat | Basis |
|---|---|---|
| T1 | An award payable to Wolfspeed, its payment or enforcement | Record: the claims and the hearing. Law: FAA §9 confirmation |
| T2 | JPMorgan acting on default or non-renewal, and setting off the deposit | Record: the credit agreement §7; note maturity 31 Jan 2025 |
| T3 | Operating cash | Data: the feed. Record: going concern |

### 5.1 Chain T1: the award

| Step | Question | Actor | Basis |
|---|---|---|---|
| 1 | When does the award issue? | Panel | **Law:** AAA Commercial Rules, 30 days after the hearing closes (the closing date is unknown; post-hearing briefs may extend it). **Record:** the company delayed its filings awaiting the outcome (NT 10-K, NT 10-Q). **Code:** timing distribution from D; labelled parameter; sensitivity. **No Jev.** |
| 2 | Each claim head | Panel | **Contract:** the agreement's remedies (cure, then termination of exclusivity or the agreement); arbitrators may not award excluded remedies; North Carolina law. Whether termination is the *exclusive* remedy for a missed minimum is a legal question (**the builder reads ¶8–9 and ¶16 in full; if the text is redacted, it stays a Jev residual**). **Record:** the $3.30M is already a booked payable (Record: the 10-Q). **Jev residuals, chain rule:** $3.30M delivered crystals, then $4.25M shortfall, then $18.5M anticipatory breach. |
| 3 | Fees and interest | Panel | **Contract:** the prevailing party recovers fees (¶16(b)); unpaid invoices bear interest at a redacted rate. **Amounts unknown:** fees shown, not in cash, with an evidence request; interest computed at the North Carolina legal rate (8%, §24-1) as a **labelled fallback** because the contract rate is redacted, with sensitivity. |
| 4 | Pay, settle or challenge? | C&C | **Arithmetic:** award on the path against cash (P5 / P50). **Record:** going concern; funding shut off; the proxy contest. **Law:** vacatur only on the limited grounds of FAA §10, within 3 months (§12). **Jev residuals:** settle (per interval; amount and timing per Decision D5); challenge. |
| 5 | Confirmation and enforcement | Wolfspeed | **Law:** FAA §9 confirmation, then execution. **Code:** timing. **Jev residual:** whether Wolfspeed enforces on the path. |
| 6 | Enforcement trips the JPMorgan line | — | **Contract:** "any attachment … or garnishment" is a default (credit agreement §7.1(K)). **Code:** default on the enforcement date. Chain 5.2 follows. |

### 5.2 Chain T2: the JPMorgan line

| Step | Question | Actor | Basis |
|---|---|---|---|
| 1 | Renewal at 31 Jan 2025 | JPMorgan | **Record:** renewals in 3-month steps; going concern; late filings; the award outcome on the path. **Jev residual.** |
| 2 | Non-renewal or default: repayment and setoff | JPMorgan | **Contract:** remedies include acceleration and setoff (§7.2). **Arithmetic:** $2.3M repaid from the $5.05M deposit. Whether the remaining ~$2.75M is released to available cash depends on the pledge terms: **the builder reads the pledge or security agreement. Until then it is typed unknown, with the release as a sensitivity.** |
| 3 | Material adverse change | JPMorgan | **Contract:** §7.1(M). **Jev residual:** whether JPMorgan invokes it, on paths where the award is large. |

### 5.3 Chain T3: operating cash

Code only, as in §4.4.

**Wolfspeed's 2014 security interest** ranks ahead of Slope only in an insolvency. No petition is in the record inside this horizon, so it is noted and not modelled.

---

## 6. Dispute model 4.0.0 (`dispute_model.json`)

- **`forum`:** `court` | `arbitration`, supplied by the agent and code-checked (§3.1 of revision 1, unchanged).
- **`components`:** `[{component_id, label, status: awarded | requested, amount_cents | statutory | unknown, basis}]`, each code-checked against the quotes or computed from quoted law.
- **Stages:**
  - **court:** `amount_pending`, `post_trial`, `judgment_entered`, `appeal_pending`, `enforcement`, `paid`;
  - **arbitration:** `claims_pending`, `award_pending`, `award_issued`, then the court stages after confirmation.

  Stage rules as in revision 1, including the events `post_trial_motions_pending`, `post_trial_decided`, `arbitration_hearing_held`, `award_issued`, `award_confirmed`, `injunction_entered`.
- **Chains:** each node has:
  - `actor`;
  - `decision`;
  - `standard` (Law citation);
  - `record_items` (what the agent must supply);
  - `path_facts` (what code computes);
  - `timing` (code rule or schedule);
  - `anchor` (reference class and source status);
  - `residual_question` (registry id).
- **Removed:**
  - `settlement_window_days` (settlement is asked per interval);
  - `amount_fixed`, `award_timing`, and the "no ruling" timing branch as Jev questions (now code timing);
  - `ruling_window_days`.
- **Rules added, each with a citation:**
  - Rule 62(a)/(b); FRAP 4(a)(4)(A); Rule 69;
  - N.C. Gen. Stat. §24-1, §24-5(b), §66-154(d), §75-16; 18 U.S.C. §1836(b)(3)(D);
  - 28 U.S.C. §1961;
  - AAA Commercial Rules (award timing); FAA §§9, 10, 12;
  - 11 U.S.C. §§362, 547.
- **Outcomes added:** `vacated`, `new_trial`, `award_challenged`, `reorganization`.

## 7. Financing instruments

Domain object `FinancingInstrument` and agent tool `instantiate_financing`, as in revision 1, §4.1. Instead of generic triggers, instruments carry the **contract terms each chain cites**:
- Akoustis: §7.01(i), §7.02, Fundamental Change repurchase, interest dates;
- Charles & Colvard: §7.1(K), §7.1(M), §7.2, maturity, pledge.

Jev's present-state readings (`financing_interpretation`) confirm the agent's cited terms apply to this instrument. Every figure and date is code-checked. The forecasts are the chain residuals in §4.3 and §5.2.

## 8. Engine (`app/analysis`)

- **`events.py`:**
  - component amounts, including statutory interest, arithmetic on record figures, and the D4/D5 declared bounds or scenarios;
  - ruling and award timing distributions;
  - execution before and after the ruling;
  - stays and bond collateral;
  - the repurchase and acceleration cash;
  - JPMorgan setoff (and release when known);
  - dated interest and credits;
  - the petition date per trajectory.

  Draw keys are `(instance, node, purpose)`, so probabilities never move dates.
- **Path facts:** a pre-pass simulates each path's state at each node's decision date (P5 / P50 cash, amount owed, collateral required) for the Jev questions.
- **`operating.py`:** per-category arrays for `supplier_invoice`, `legal_fees`, receipts and `need`.
- **`engine.py`:** the reusable line (§2.1), the collection rule (§2.2), the petition (§2.3), the outputs (§2.4). The single-draw reference arithmetic stays in `slope finance check`.
- **`core.py`:** attribution (§9); `parameter_sensitivity` is dropped.

## 9. Attribution and the recall replay

**Attribution**, four reweightings of the same trajectories:
1. **Bank data only.**
2. **Plus what the record fixes** by law, arithmetic and schedule: components, timing, triggers, feasibility, with neutral residuals (Noul 0.5, Choice uniform).
3. **Plus Jev's outside-view anchors.**
4. **Plus Jev's case adjustments.**

Step 4 minus step 3 is the qualitative signal from this record, measured in the loan's cash flows.

**Recall replay:** `slope analyze --run <id> --roles` re-asks every adjustment question with the parties' names replaced by roles, and stores the deltas under `recall_check`.

## 10. Question registry 4.0.0 and `agent_config`

- **`dispute_interpretation`:** the readings events of §6, plus `bears_on_litigation_spend_attributed`.
- **`outside_view`** (new, case-free): one question per reference class. The state holds only the reference class and the decision.
- **`dispute_forecast`**, residuals per chain:
  - `forecast_execution_pending_motions`, `forecast_stay_obtained`;
  - `forecast_liability_survives`, `forecast_damages_ruling` (Choice: no change / remit / new trial), `forecast_remittitur_accepted`;
  - `forecast_trebling`, `forecast_fees_awarded`, `forecast_prejudgment_interest`, `forecast_exemplary_review` (only if the record shows a challenge), `forecast_injunction`;
  - `forecast_appeal`, `forecast_settlement` (per interval), `forecast_debtor_response` (file or settle), `forecast_enforcement`;
  - `forecast_head_awarded`, `forecast_challenge_award`.
- **`financing_forecast`:**
  - `forecast_holders_notice_early`, `forecast_holders_accelerate`;
  - `forecast_reverse_split`, `forecast_repurchase_exercised`;
  - `forecast_lender_renewal`, `forecast_lender_mac`.
- **Every residual state** carries the anchor, the path facts, the standard and the record items. Its instruction is: **adjust from the anchor using this record; do not re-derive timing, amounts or affordability, which are given.**
- **`agent_config`:** the new profiles; `instantiate_financing`; Jev budget review. Expected: about 45 residual and 15 anchor questions per case, within 250 attempts.

## 11. The page

In order: evidence → Jev judgment → mechanism → impact.

1. **The business and its Slope line:** how Slope's rule sized it, the invoices it finances, the synthetic-bank label once.
2. **What the research learned:** disputes, components with amounts and their basis, financing terms, deadlines.
3. **What might happen:** the tree. Each node shows its probability and its **"why" chain** (basis labels, sources, anchor then adjustment).
4. **What it does to cash:** bands, backup liquidity, the petition probability over time.
5. **What it does to the loan:** draws, collections, headroom, stayed claim, preference exposure, PV, dollar-days; bank-only against event-adjusted; the lead-time series.
6. **Which judgment matters:** the 0% / Jev / 100% ranking; the four-step attribution.
7. **Stress.**
8. **Controls:** line usage; limit multiplier; fee; award, fee and settlement shares; the remittitur range; collateral share; variability.

## 12. Tests (financial correctness, composition, isolation)

- **Composition:** the chain-rule components sum to one over their combinations. Removing a residual (setting it neutral) reproduces attribution step 2 exactly.
- **Timing:** no ruling before 8 Aug on any trajectory; FRAP tolling moves every post-ruling window by exactly the drawn ruling date.
- **Arithmetic impossibility:** full payment is dropped only when the amount exceeds the maximum cash on every trajectory of the path.
- **Statutory interest:** 8% simple from 4 Oct 2021 on the surviving compensatory amount; none on exemplary damages.
- **Line and petition:**
  - outstanding ≤ the limit;
  - no draw while overdue or after a petition;
  - Σ fundings × 1.037 = Σ installments;
  - collected + stayed + not-yet-due + uncollected = contractual;
  - the preference series is exactly `[p − 90, p)`.
- **Notes:** the default fires only at final judgment + 60 days, when unpaid, unstayed and noticed. The repurchase date is 20–35 business days after notice.
- **Charles & Colvard:** enforcement triggers the line default on its date; setoff repays exactly the drawn balance.
- **Isolation:** the §1 probes are absent from both snapshots.

## 13. Delivery: decompose first, then acquire, then build

### 13.1 Why the decomposition comes before any research or data acquisition

The chains are the specification of what evidence is needed. Acquiring before decomposing gets that order backwards, in four ways:

1. **Only a decomposed question tells you what to look for.** A holistic question ("will the court uphold the judgment?") makes everything and nothing relevant. Asking how the actor decides names the specific things that answer each step:
   - the judge's pace on this docket's earlier motions;
   - the standard for remittitur;
   - what a surety requires as collateral;
   - whether JPMorgan's pledge releases the excess deposit;
   - how many months of head start the trial record supports.

   The round 2 sweep collected "everything that could move a forecast" and still missed every one of these. It also gathered material that no chain uses.
2. **Evidence found first quietly shapes the model.** When acquisition comes first, the model grows branches around the figures that happen to turn up. The "$305k remittitur" branch came from a number found in a motion, not from how remittitur works. Decomposing first means the structure follows how decisions are actually made, and evidence then fills a named slot or leaves a typed gap.
3. **Builder research reads outcomes; the design must not.** Our acquisition work is builder-side and sees later events. Chains derived from the law and from each actor's decision process are designed without the outcome. Acquiring targeted pre-D evidence afterwards keeps the design honest. The acquisition notes' "check against the outcome" shows exactly the temptation this order avoids.
4. **Gaps become visible and research effort goes where it matters.** Each chain step states its basis and what it needs. If acquisition can't supply it, the gap shows as a typed unknown or an explicit Jev residual, instead of disappearing into a holistic number. Outside-view reference classes (R1) can only be named once the actor decisions are. Their research is prioritised by the steps the lender's outcome depends on.

**Example:** the remitted amount (Decision D4). Decomposed, it becomes: what does the law let the court remit to? (Law, R2.) What figures does the trial record support? Akoustis attacks the "speculative 55-month head start", and unjust enrichment scales with the head-start period. So the remitted amount may be arithmetic on a head-start figure in the trial record (D.I. 616). That points acquisition at the transcript's damages testimony. Without the decomposition we would have picked a range.

**What this means for the chains in §4–5:** they are revision 2's first pass, not yet complete by this same standard. For example, "will Qorvo execute before the ruling?" can go further. How does a judgment creditor decide?
- What execution would recover against this debtor's cash (Arithmetic).
- What a petition would do to its position: the automatic stay, and Qorvo becoming an unsecured creditor (Law).
- Whether its own pending motions to increase the judgment argue for waiting (Record).

Only then is the rest Qorvo's choice.

### 13.2 Order of work

1. **PR for `step-5c-channels`:**
   - the waiver-as-evidence change and single-dimension rubrics (done);
   - the two Western Alliance commits dropped;
   - this spec added as a document.
2. **Stage 1, decomposition (a design document; no code, no acquisition):**
   - complete every chain in §4–5 by the §3 method, for both procedures (court post-trial and arbitration) and the financing instruments;
   - each step gets its actor, question, basis type, evidence requirement, and outside-view reference class where Jev remains;
   - **Owen reviews and approves.**
3. **Stage 2, requirements and gap analysis:**
   - list every evidence and data requirement from the approved chains;
   - check each against what round 2 already acquired;
   - the remainder becomes a targeted acquisition and research list: R1 outside-view sources, R2 legal citations, R3 contract terms, R4 docket pace, the trial-record figures, and any new items.
4. **Stage 3, targeted acquisition and research:**
   - builder-side, pre-D evidence only in the snapshots;
   - findings recorded against the requirement they answer.
5. **`step-6a-cases`:**
   - the kit gains the round 2 research, the acquisitions, the recall probe and the Stage 1–3 documents;
   - snapshots for both cases (sources, isolation probes);
   - bank feeds (D1 proration);
   - `make_run_inputs.py`;
   - **the ChromaDex removal (§1.3)**, with the tests rewritten on Akoustis facts.
6. **`step-6b-model`:** dispute model 4.0.0 with the approved chains, registry 4.0.0, financing instruments, engine, attribution, recall replay, page.
7. **`step-6c-record`:** record both runs; analyse; replay; browser walkthrough; one focused review; `merge-ready`.

## 14. Decisions and open research

### Decided

- **D1 (approved; amended 25 Sep 2026): dated flows, then proration.**
  - Every flow the record dates goes on its own date. Only the undated remainder of the quarter containing D is prorated to D by business days. Flows dated after D enter no snapshot or feed.
  - Balances and flows for periods before D that were filed after D are allowed, labelled once.
  - Why amended: straight proration of Akoustis's April–June quarter moved about $7.2M of post-D cash (a customer note received 26 Jun 2024) before D. See `research/recent_cases/akoustis/design/CASH_CHECK_20240620.md`.
- **D2 (approved): ChromaDex removed** (§1.3).
- **D3:** resolved in revision 1. The lead-time series is intrinsic to the reusable line (§2.4).

### D4: amounts granted when the record doesn't fix them

**What it is.** Three places where a court or panel can grant something without the record fixing how much:
- fees awarded below the $12.1M requested;
- a claim head granted in part;
- the amount a court remits the verdict to.

Jev never sets amounts, so code needs a rule.

**Options:**

| Option | What it does | Problem |
|---|---|---|
| a. The record's upper bound (the full requested or claimed amount) | Conservative for the borrower's cash, which errs on the lender's side; invents nothing | Overstates what the borrower pays if courts typically cut. For remittitur it collapses the branch into "no change", so it can't be used there |
| b. A uniform range between the record's figures | Looks realistic | The shape has no basis: a hand-written coefficient in disguise. Revision 1 recommended this; under the §0 rule it fails |
| c. Data | e.g. fee awards against fee requests in federal IP and trade-secret cases; arbitral awards against claims | Exists only if R1 research finds a citable source |
| d. Decomposition to arithmetic on the record | e.g. the remitted amount as unjust enrichment scaled to the head-start months the trial record supports | Needs the Stage 1 chain and the Stage 3 transcript read |

**Recommendation:** (d) wherever the chain reaches it; then (c) where a source exists. Otherwise (a) for fees and heads, labelled, with the "not awarded" branch as the other end (already a branch). For remittitur, if neither (c) nor (d) resolves it, carry the two record figures as two declared scenarios, not a weighted range, and show that choice on the page.

### D5: settlement amount and payment timing

**What it is.** If the parties settle on a path, how much cash leaves, and when? The inherited model uses "60–100% of the amount". Revision 1 added "50–100% paid up front, the rest in 300 days". **Neither has a basis;** I proposed them without one.

**What the decomposition says is knowable:**
- **The cash a settlement can require inside the horizon is bounded by what the payer can fund on that path** (Arithmetic: available cash minus operating need). Akoustis states the judgment exceeds its liquid assets (Record). So a large settlement must be structured over time or in non-cash terms, or not happen.
- **The total and its structure are the parties' negotiation.** No source fixes them for this case, and Jev cannot set them.

**Options:**
- (i) Sourced data on post-verdict and post-award settlement discounts and payment structures (R1).
- (ii) Declared scenarios: a lump sum at the payer's feasibility bound; or staged payments within the horizon up to that bound, with the remainder beyond it. Both labelled; neither weighted as if known.
- (iii) The old ranges: rejected.

**Recommendation:** (i) if found. Otherwise (ii), with the feasibility bound computed per path, and the page showing whether the choice between the two scenarios moves Slope's collections. The realised Charles & Colvard settlement structure is an outcome and must not inform the scenarios.

### D6: how much of each eligible invoice the borrower routes through Slope (`line_usage`)

**What it is.** The borrower decides which supplier invoices to finance through Slope. The default is 100%.

**Basis:**
- **Record:** no cheaper credit at D (Akoustis: ATM suspended, 45-day lock-up, authorized-share limit, equity at $0.20; Charles & Colvard: late filings block the shelf, and the JPMorgan line is short-dated). Both stress cash conservation (Akoustis: "continued focus on cash conservation"; the burn-cut plan).
- **Arithmetic:** on a ~$0.3M limit, the fee is about $11k per full turn (3.7%). That is small against either company's monthly burn, so using the line is rational whenever extra liquidity has any value to a going concern.

**Why it is not load-bearing (Arithmetic, to confirm on the built feeds):**
- the limit (~$0.3M) is small against eligible supplier invoices (several hundred thousand dollars to over $1M a month);
- each draw repays over 90 days, so the line refills only as installments are paid (roughly a third of the limit a month);
- so any usage above roughly 10–30% keeps the line full, and exposure is set by **the limit, not the usage**.

**The decision that actually carries weight is Slope's limit:** 15% of receipts after debt service, against up to 33%. That is the limit-multiplier control.

**Recommendation:** 100%. State once that exposure is limit-bound above the threshold, and show the usage sensitivity, which confirms it.

### Open research (scoped in Stage 2; acquired in Stage 3)

| # | Item |
|---|---|
| R1 | Outside-view sources for each residual's reference class, and data for D4 and D5 |
| R2 | Legal citations marked **to cite**: the Third Circuit remittitur standard; the North Carolina cap on exemplary damages (ch. 1D); the trade-secret injunction standard; Nasdaq Rule 5815 |
| R3 | Charles & Colvard's pledge terms (release of the deposit excess) and whether the supply agreement's remedy for a missed minimum is exclusive |
| R4 | Judge McCalla's pace on earlier fully briefed motions on this docket |
| R5 | The trial record's damages figures, including the head-start months (D.I. 616 excerpts), for D4(d) |

## 15. Scope for the proof of concept, and corrections that bind Stage 1

Owen's decisions, 25 Sep 2026.

### 15.1 Scope

- **Akoustis first, end to end** (decomposition, snapshot, feed, model, recorded run, page). Charles & Colvard follows on the same pieces, plus an arbitration template and the credit-line terms.
- **Outside-view anchors are deferred** (§3.4). Each residual Jev question is asked directly, with its path facts and record items. Attribution has three steps: bank data only; plus what the record fixes, with neutral residuals; plus Jev.
- **No new machinery** for the corrections below. They change the design document and the contracts, not the process.

### 15.2 Corrections

1. **Legal meaning first, inside the Stage 1 document.** One section maps the law: actors, authority, modality (shall or may), triggers, conditions, exceptions, and bounded legal unknowns with citations. A second section attaches clocks and parameters to that map. A third attaches the path arithmetic. Evidence fills named slots afterwards and never changes the map.
2. **Chains are templates by forum and instrument, not per case:** federal post-judgment procedure, the indenture's judgment default and repurchase, and (for Charles & Colvard) arbitration award and the credit agreement. The case fills and tests them.
3. **Every parameter has a disposition:** sourced, a bounded open term shown with a sensitivity, or a Jev question. "Model parameter" is not a disposition. This covers the supersedeas multiple, surety collateral share, voluntary-payment window and ruling-date independence.
4. **Legal meaning is not a Jev question.** Where research can settle a legal question (§24-5(b) and unjust enrichment; trebling after the jury's "No"), it is Law. Jev forecasts a court's resolution only where the design document records, with a citation, that the question is open. `financing_interpretation` does not decide what a contract means; the agent quotes the terms and code checks them.
5. **Exclusions use only three grounds:** the decision belongs to someone else, the fact cannot be obtained, or its window has closed. That an event has not happened yet is never a ground. So: Akoustis models a petition driven by operating cash (threat T5); Charles & Colvard has a petition threat and Wolfspeed's 2014 security interest (¶6(c)); the injunction's revenue effect gets one bounded research attempt before it is left out of cash.
6. **Indenture conditions restored:** §7.01(i) excludes amounts covered by insurance; §7.01(h) cross-default at $2.5M; §7.02 acceleration and rescission.
7. **Anchored at the decision date.** The case for 20 June and for the reusable line rests on the filing record and Slope's published product, never on where the petition falls. Before the Akoustis feed is built, check whether proration by business days moves post-D cash before D (the 31 Mar balance plus the May proceeds is about the reported 30 Jun balance, implying almost no April–June burn).
8. **The round 2 acquisition notes are evidence only.** Their kit copies carry no direction marks and no outcome check.

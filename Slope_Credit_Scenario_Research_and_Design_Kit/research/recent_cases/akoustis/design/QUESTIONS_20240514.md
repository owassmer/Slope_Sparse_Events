# The Jev questions for the 14 May 2024 review: specification

This document owns the Jev questions of the pending-claim model for the 14 May 2024 Akoustis review: what each question's event is, what each answer means, the situation it is asked in, the record it reads, and what code books for each answer. `DECOMPOSITION_20240514.md` owns the legal map, clocks, parameters and chains; the contracts implement this document. Each entry's **Build** line separates what the code at `main@cc8adf7` already does from what changes.

---

## 1. Rules for every question

**What a question is.** One well-defined uncertainty, with the context that bears on it. An actor's decision is the usual form. A threshold on an actor's outcome ("does the amount entered exceed $X?") and a joint event ("does the offering raise at least $X net by the close date?") are questions too, when the event is defined. An event is split into separate questions only where its parts differ in conditions, timing, amounts or consequences.

**What a probability means.** The probability that the defined event occurs, given the stated situation and the evidence available by the cutoff. It is model judgment, never an observed frequency.

**Answers.** Each answer establishes its event and nothing else. "No" means the event does not occur in the stated interval. The next action is its own event, or an explicit state rule; a "no" never supplies it.

**Consequences.** Every booked consequence follows from the defined event, the coherent state on the path, and an explicit legal, accounting or scenario rule, labelled by basis: Law, Record, Data, Arithmetic, or Scenario (a declared assumption with its sensitivity).

**Two dates.** The evidence cutoff is 14 May 2024 for every question. The decision date is the question's own day on the path. The state holds only events reached on the path before the decision date, on every trajectory the question covers. The absence of a document from the record is absence, never a fact that something did not happen.

**Grouping.** One question covers trajectories that share legal status, available actions and ability to pay the amount in question. A difference in any of the three splits the group. Within a group the state gives one representative trajectory's joint facts (date, cash, amounts owed, all from the same trajectory) and the group's range for each; the answer applies to the group as its average. Figures from different trajectories are never combined into one situation.

**Typed values.** Zero is a value. "Not applicable" and "not available" are states, stated in words. Cash is never shown negative: a shortfall is shown as available cash of nil and the obligations unpaid (§2.2).

**Wording.** The question keeps every qualification that defines its event: "if any", notice requirements, burdens, the amount and timing of a proposed payment, the security offered, deadlines, and the month's operating need where it bears on the decision. It carries no implementation language: no draws, trajectories, lags, class names, booking rules, or instructions about amounts. The judgment is stated as entered ("a money judgment of $X entered on <date>"); an amount band states its band.

**Evidence.** Each question receives the record bearing on its event's conditions, options and incentives, whoever wrote it. Each item carries its author, date and status: court ruling, statement of intended proof, party argument, company disclosure, or third-party record. Party advocacy stays, attributed. Duplicates and material that does not bear on the event are removed. A record item that cannot exist by the cutoff is not listed. A reading of the record carries its source date and is routed only where it bears on the event at the decision date.

**Legal meaning is not a Jev question.** Where research settles a legal question, it is Law. Where it does not, the interpretation is a declared legal scenario (§3), held the same way along the whole path, with the alternative as a sensitivity.

**The no-lawsuit view.** The ordinary view asks the same questions on the same record. The dispute's resolution on the review date, at no cost to the company, is a stated event on its paths.

**Depth.** A question is asked only where its answer can change cash, timing, an obligation, or a later decision inside the horizon (to 10 Nov 2024) on some trajectory.

**Shared instruction** (both profiles, `dispute_forecast` and `financing_forecast`):

> Forecast the outcome specified in the question at the stated decision time, conditional on the supplied scenario. Treat the scenario's events and amounts as given. Use the supplied evidence available by the evidence cutoff, preserving each source's author, date and status. Apply the supplied legal or contractual provisions where relevant. Estimate the probabilities of the defined outcomes.

The state distinguishes five kinds of content: historical evidence, party assertions, court findings, events assumed on this path, and earlier readings of the record.

---

## 2. Shared state

### 2.1 The judgment

The amount entered and its date; its standing on the decision date (unpaid, stayed on approved security, levied in part, reduced, set aside, paid, settled); the amount owed on the decision date. Before a verdict: no judgment, and the amounts Qorvo claims (D.I. 543-1).

The claim components in the state are Qorvo's itemised claims. Akoustis's position on damages is evidence, not a component. Components appear only on questions where the claim's size bears on the event: the jury, the post-trial questions, settlement, the company's response to the judgment, the stay, and enforcement.

### 2.2 The company's cash

Available cash on the decision date, and the operating need for the next month. Cash is never negative.

**Processing (Scenario).** Each day, the day's receipts post first. Obligations due that day are then processed against the available balance at that moment: the balance left after every earlier payment, less restricted cash (locked stay security). A levy served that day is processed first: it attaches the reachable balance. Scheduled obligations with a fixed due date are processed next, in the order they were incurred. These are Slope's installments by automatic debit, agreed settlement installments, and interest payable in cash. The day's operating outflows are processed next. A scheduled obligation is paid in full or not at all. Operating outflows are paid up to the balance. Whatever is unpaid becomes an arrear in its class: Slope, settlement, notes interest, operating. This same-day order is a convention; its effect is measured once against operating outflows first (§5).

**Arrears.** A failed Slope debit is retried on the line's retry dates. An unpaid settlement installment stays owed to the claimant; the settlement terms carry no acceleration. Unpaid operating outflows are carried as vendor credit: receipts and spending continue as the operating forecast has them. When receipts exceed the day's obligations, the surplus pays the other classes' arrears, oldest first. Slope's arrears are collected only by its debit, on the line's retry dates.

**State given to Jev.** Available cash, the operating need, and from the first unpaid obligation: the arrears by class and amount, and the days since the first obligation went unpaid.

### 2.3 The notes

Principal ($44.0M) and interest terms; each Event of Default continuing on the path (§7.01(i) judgment default, §7.01(b) delisting, §7.01(j)(v) general nonpayment); whether the notes are due, and how (declared acceleration, or automatic under §7.02); the repurchase date where a Fundamental Change has occurred. The notes' balance is always stated apart from the judgment's.

### 2.4 The listing

The bid-price deficiency, the 21 Oct 2024 compliance deadline, and on the decision date: listed, hearing requested (suspension stayed until the panel decides), suspended, or delisted.

### 2.5 Stay security

One of four types, each with its terms: full bond collateral (the amount); reduced cash security (the amount of cash above the month's operating need on the approval day); non-cash security or waiver (the non-cash scenario only); none available. A reduced-security amount of zero is never used to mean that full collateral is available.

### 2.6 Equity: channels and share capacity

**Share capacity (Record, Arithmetic).** One ledger of authorized shares that are neither issued nor reserved: 175,000,000 authorized, less 98,669,282 outstanding (8 May 2024), 9,341,825 reserved for conversion, 3,031,625 for options, and the 5,000,000 registered for interest paid in shares. That leaves 58,957,268, an upper bound because the equity-plan reserves are not stated. At-the-market sales and underwritten offerings draw on this ledger at their declared prices, and a channel stops when it cannot cover the issuance. Interest paid in shares draws on its own 5,000,000.

**At-the-market sales (Scenario).** The program the company re-activated on 13 May sells from 14 May on each trading day while the stock is listed and no petition has been filed. Volume: 20% of the average daily dollar volume from 15 Mar to 14 May 2024 (about $337k), with 10% and 25% as sensitivities. Price: the 14 May close. Proceeds are net of the agents' commission (up to 3%). They settle two business days after the sale for sales before 28 May 2024, and one business day after it from that date. No Jev question: the company's decision to sell is on the record; the pace is the declared assumption.

**Underwritten offerings (N1).** An offering is available while the stock is listed, no petition has been filed, no other offering is pending, and capacity covers it. Its proposed size is the January 2024 offering's net proceeds, or less where capacity binds: the shares available times the offering price. For the ledger, an offering's shares are counted at the 14 May close less the January offering's discount to the prior close, and at-the-market shares at the 14 May close. These prices serve only the share count: the market sets the actual price, and no question states one. There is no fixed count of offerings per path.

---

## 3. Legal scenarios held along a path

### 3.1 Which judgment starts the §7.01(i) period

**Base: the judgment as entered.** The default becomes available 60 days after the Rule 62(a) automatic stay ends (entry + 30 days), where the judgment remains unpaid and undischarged and execution is not effectively stayed throughout. It continues while those conditions hold. A post-trial ruling does not restart the period; where it changes the judgment, the holders' decision is asked again on the changed judgment, with the default already available (H1).

**Sensitivity: the post-trial ruling.** The period runs from the order disposing of the last tolling motion, on the surviving amount. There is no earlier opportunity.

### 3.2 The holders' petition under §7.06

§7.06 bars a holder's proceeding unless the trustee has not acted within 60 days of the holders' written request. The bar does not apply to an Event of Default under §7.01(j) or (k). Where a (j) default is continuing, the holders' petition is available at once. Elsewhere: **base**, the request is made at acceleration and the petition is available 60 days later; **sensitivity**, available at acceleration. The petition question's timing follows the scenario it is asked under.

### 3.3 General nonpayment, §7.01(j)(v)

"Generally is not paying its debts as they become due" asks whether nonpayment is the company's general practice, by extent and over time; one late payment is not. It is met on a day when, over the preceding 30 days, arrears (§2.2) have been outstanding throughout and the obligations left unpaid amount to at least a quarter of all obligations that fell due in those days. Sensitivities: a 15- or 60-day window; half of the obligations due. Code tests it daily. Where it is met: an Event of Default under (j); the notes become due immediately under §7.02 without any declaration; §7.06 does not bar a holders' petition. No Jev question decides whether it is met.

### 3.4 Which rules apply

The 2024 texts of the Federal Rules, the Nasdaq Rule 5800 series and the indenture of 9 June 2022.

---

## 4. The questions

Fields for each question: **Event** (what occurs, its interval and eligibility), **Answers**, **Situation and grouping**, **Record**, **Consequences**, **Build**.

### 4.1 The verdict and the judgment

#### J1 · The jury's liability answers · `forecast_verdict_finding`

- **Event:** the jury's answer to one liability item of the final verdict form (D.I. 580), conditional on its earlier answers on the path. Items: 1(a) trade secrets (a "yes" in both columns for at least one trade secret, as the form defines it); 2(a) and 2(b) conspiracy; 3(a) false advertising; 5(a) patent infringement. Question 4 (UDTPA) is not asked: D.I. 590 bars its damages.
- **Order:** the form's order. 3(a) and 5(a) are asked on every path where the trade-secret and conspiracy amounts leave the total award below the J1b top line, or no award. Above the top line their amounts change no cash.
- **Answers:** yes, the jury answers the item "yes"; no, the jury answers "no". A "no" to 1(a) is stated as the form's answer ("Question 1(a): No"), not as a finding of no trade-secret liability.
- **Situation:** the form's question quoted, the earlier answers as the form records them. No cash.
- **Record:** the form (D.I. 580); the summary-judgment ruling and its reasons (D.I. 545); validity (D.I. 557); the exclusion of defense technical opinions (D.I. 546); claim construction (D.I. 152); each side's statement of intended proof (D.I. 543-1), attributed; D.I. 590.
- **Consequences:** the award is the sum of the amounts for the items found: 5(a) yes, $279,808; 3(a) yes, $1,146,604; trade secrets and conspiracy, from J1b.
- **Build:** change. The lower award books only the claims found (today every lower branch books $1,426,412), and the liability record is routed.

#### J1b · The jury's amounts · replaces `forecast_verdict_measure`

- **Event:** the amount the jury enters on the item exceeds $X, asked for 1(b) (trade secrets) and separately for 2(c) (conspiracy, actual loss). Each threshold is asked conditional on the band already established.
- **Cut points:** the lines where cash behaves differently. The reach line is the most Akoustis can pay from cash on the entry day. The top line is the lowest amount above which every award books the same cash on every trajectory through 10 Nov 2024, with every raise and levy included. Both are measured on the built trajectories. Between the reach line and the top line, and below the reach line, there is one further cut in each band. The first cut is at nothing ("is any amount entered?"), so no award is its own outcome. No band of the total judgment straddles the notes' $10,000,000 judgment-default threshold; where one would, the threshold is a cut. The thresholds reach Jev as figures only.
- **Exemplary damages:** 1(c) (willful and malicious) and 1(d) (the exemplary amount, at most twice 1(b) under the DTSA) are their own questions, asked in the form's order, only where the 1(b) band leaves the total within a band that exemplary damages could move across a line.
- **Answers:** yes, the amount exceeds $X; no, it is $X or less. At the first cut, no means nothing is entered.
- **Situation:** the form's words, the earlier answers. No cash.
- **Record:** Qorvo's damages method and figures (D.I. 543-1; the admitted opinion, D.I. 553); the revenue base left to the jury; the defense's avoided-cost opinion as admitted; the court's limiting rulings; the form. For 2(c), the record on actual loss; it does not take 1(b)'s reasoning because Qorvo claims the same figure.
- **Consequences:** no award books nothing. A positive band below the top line books its midpoint, with its ends as the sensitivity. The band above the top line books Qorvo's claimed amount; by the line's definition every amount in it books the same cash, which the build verifies.
- **Build:** new. The measure questions retire.

#### D1 · Akoustis files post-trial motions · `forecast_post_trial_motions`

- **Event:** Akoustis files a timely Rule 50(b) or Rule 59 motion (28 days from entry) challenging the judgment.
- **Answers:** no, no such motion by the deadline; the judgment is final as entered and the time to appeal runs from entry.
- **Situation:** the judgment as entered.
- **Standard:** Rules 50(b) and 59. A party that moved under Rule 50(a) at trial must renew under 50(b) to keep a sufficiency challenge for appeal (*Unitherm Food Sys. v. Swift-Eckrich*, 546 U.S. 394 (2006)).
- **Record:** the Rule 50(a) motions made at trial and their disposition (D.I. 590); the company's statements on contesting the claims.
- **Consequences:** yes, the ruling date is code-owned (briefing plus the judge's measured pace); no, finality at entry.
- **Build:** keep the booking; add the standard and the record.

#### J2 · The court rules on the post-trial motions · `forecast_post_trial_ruling`

- **Event:** how the court disposes of the pending motions as to the money judgment.
- **Answers:**
  - unchanged: the judgment stands as entered;
  - reduced: a surviving amount, asked only where the reduction moves the award across a cash line (the J1b lines), with the surviving amount's band;
  - set aside: no money judgment remains, and the claim stays in dispute (a new trial, or an appeal by Qorvo).
- **Remittitur:** where a reduction is a conditional remittitur, Qorvo's election (accept the reduced amount, or a new trial) is its own question (C3), asked on the same terms.
- **Situation:** the judgment as entered, the motions and the grounds preserved at trial. No cash. No reading of amount finality taken before the verdict.
- **Record:** the Rule 50(a) motions and D.I. 590; the court's pre-verdict rulings on the damages evidence (D.I. 553, D.I. 546).
- **Consequences:** unchanged, nothing booked. Reduced, the amount owed becomes the surviving amount on the ruling date. Set aside, nothing owed, the adverse judgment ends, any stay security is released, and legal spend continues.
- **Build:** keep the set-aside booking. Add the reduced outcome and C3. Remove the 13 May amount-finality reading.

#### C3 · Qorvo elects on a remittitur · new

- **Event:** Qorvo accepts the remitted amount, where the court conditions a new trial on its refusal.
- **Answers:** no, Qorvo takes the new trial; the judgment is set aside and the claim stays in dispute.
- **Record:** Qorvo's claims and the relief it seeks (D.I. 543-1), attributed.
- **Consequences:** yes, the reduced judgment as J2; no, as J2 set aside.
- **Build:** new, only where J2's reduced outcome is asked.

#### D5 · Akoustis appeals · `forecast_appeal`

- **Event:** Akoustis files a notice of appeal by the deadline that applies on the path: 30 days from entry where no timely tolling motion was filed; 30 days from the order disposing of the last tolling motion otherwise (FRAP 4(a)(1)(A), 4(a)(4)).
- **Answers:** no, no notice by that deadline.
- **Asked:** only where an appeal changes cash inside the horizon (a stay pending appeal, or enforcement timing).
- **Record:** Akoustis's statement of intended proof contesting each claim (D.I. 543-1), and its statements on contesting the claims.
- **Build:** keep; route the record; state the one applicable deadline.

### 4.2 Settlement

#### D3 · Akoustis offers the proposed settlement · `forecast_settlement_offer`

- **Event:** Akoustis offers the settlement on the stated terms during the stage in which it is asked (before the verdict; entry to the post-trial ruling; ruling to the appeal deadline; while stayed).
- **Terms, stated as facts:** the amount (the cash above the month's operating need on the settlement date, capped at the amount Qorvo claims before a judgment and at the amount owed after one), paid in 12 monthly installments from the settlement date, first payment on that date, the claim released on the settlement date, no security.
- **Answers:** no, Akoustis does not make this offer in the stage.
- **Eligibility:** only trajectories where the offer amount is positive. Groups split where offers differ materially in size relative to the amount owed.
- **Situation:** before the verdict, Akoustis is the defendant, no judgment exists, and the cap is the claimed amount.
- **Record:** the company's statements on settlement; its own claims against Qorvo; the parties' competitive relationship.
- **Consequences:** the settlement books through the existing rule: installments from the settlement date, release on that date, only where the amount is positive. Its installments are scheduled obligations under §2.2.
- **Build:** keep `settle()` and its positive-amount guard. Change the eligible population and the stated terms (the cap is described as the claimed amount before judgment).

#### C2 · Qorvo accepts the proposed settlement · `forecast_settlement_accept`

- **Event:** Qorvo accepts the stated terms (as D3) by the settlement date.
- **Answers:** no, Qorvo does not accept these terms in the stage.
- **Eligibility and situation:** as D3, the same population and terms.
- **Record:** the relief Qorvo seeks, including a permanent injunction (D.I. 543-1 ¶¶41, 62); the parties' competitive relationship; the company's disclosures bearing on collectability; each attributed.
- **Build:** keep the booking; change the population, the terms stated, and the record.

### 4.3 Stay and enforcement

#### C1 · Qorvo initiates enforcement · `forecast_execution_pending_motions` (before the ruling), `forecast_enforcement_after_final` (after it)

- **Event:** Qorvo initiates execution on the unpaid, unstayed judgment during the stated interval, where execution is permitted.
- **Answers:** yes, Qorvo applies for a writ (and, before finality, for registration elsewhere, J4); no, Qorvo does not initiate enforcement in the interval.
- **Status stated:** motions pending; ruled with an appeal pending; or the appeal period expired. Where enforcement is already under way on the path, it continues by the levy rule and is not asked again.
- **Record:** Qorvo's claims, the relief it seeks and its statements on the litigation; the parties' competitive relationship; the company's disclosures.
- **Consequences (Scenario, the levy rule):** a writ levies the lesser of the enforceable amount and reachable cash on the levy day, the levy lag after initiation.
- **Build:** keep; change the status wording and the record.

#### J4 · The court authorizes registration in another district before finality · `forecast_1963_good_cause`

- **Event:** the court grants Qorvo's application to register the judgment in another district before it is final (28 U.S.C. §1963, good cause).
- **Precondition:** Qorvo has applied (C1 yes, before finality).
- **Answers:** no, registration is not authorized before finality.
- **Record:** the company's properties (headquarters in Huntersville, North Carolina; the fab in Canandaigua, New York; incorporated in Delaware, with no operations there), from the FY2023 10-K. Nothing is stated about where cash is held.
- **Consequences:** yes, registration is permitted from the order. The levy that follows is C1's enforcement under the levy rule, not an effect of the order.
- **Build:** keep the booking. Change the question: the levy leaves the question text and appears as the levy rule. Route the properties record.

#### D4 · Akoustis moves for a stay · `forecast_stay_motion`

- **Event:** Akoustis moves for a stay of execution offering the stated security (§2.5), by the stated day.
- **Answers:** no, no such motion by that day.
- **Grouping:** split by security type. Where no security of any type is available under the scenario, the question is not asked.
- **Situation:** the bond required, the collateral required, and the security offered with its type and amount: the same terms J3 judges.
- **Record:** the company's statements on its liquidity and its ability to post security.
- **Build:** keep the booking; change the stated terms.

#### J3 · The court grants the stay · `forecast_stay_approved`

- **Event:** the court grants the requested stay on the stated terms (Rule 62(b): "bond or other security").
- **Answers:** no, the stay is not granted.
- **Situation:** the security type and amount as D4, the bond and collateral required, the judgment.
- **Scope:** in the cash-only scenario, the request offers full collateral or positive reduced cash security. Non-cash security or a waiver is its own scenario. This is the model's scope, stated as the scenario, not as the court's rule.
- **Consequences:** effective on approval. Full collateral is locked, or the reduced cash security is locked; nothing is locked under non-cash security. Released when the dispute ends.
- **Build:** keep `stay_security()` and its effectiveness rule. Change the typed security (no zero standing for full coverage) and the stated scenario.

### 4.4 The company's financing and filing

#### D2 · Akoustis responds to the judgment · `forecast_judgment_response`

- **Event:** what Akoustis does on the decision day: the entry day; each levy day, before the levy; the day the judgment default becomes available.
- **Answers:**
  - pay the judgment balance in full;
  - initiate an underwritten offering (N1 follows);
  - file a voluntary petition;
  - none of these on that day.
- **Grouping:** "pay" is offered only to a group whose available cash covers the balance on every trajectory; groups split on that. "Initiate an offering" is offered where an offering is available (§2.6).
- **After "none":** later questions state that Akoustis did not pay, raise or file on that day. No state of seeking a sale or financing exists. A sale of the company is not modelled.
- **Record:** the company's going-concern and bankruptcy statements; its financing routes and their status; its statements on the trial.
- **Consequences:** pay, the balance is paid and the dispute ends (under a new trial it continues); initiate, N1; file, a petition on the day; none, nothing booked.
- **Build:** change. "Continue" splits into "initiate an offering" and "none", and the seeking tag retires.

#### D7 · Akoustis at its cash floor · `forecast_financing_at_floor`

- **Event:** on a day available cash falls below the month's operating need, Akoustis initiates an underwritten offering, files a voluntary petition, or does neither.
- **Asked:** at the first such day, and again at each later day cash falls below the need after having recovered above it.
- **Answers:** neither, nothing booked. At-the-market sales continue, and the next decision is at the first unpaid obligation (D8).
- **Situation:** available cash, the month's operating need, at-the-market proceeds to date, the judgment's standing, the notes' status, the listing status, and the share capacity left. Where an offering is unavailable, the state says why: delisted, a petition filed, an offering pending, or no capacity.
- **Record:** as D2.
- **Build:** change. The raise becomes an initiation followed by N1; the fixed raise amount and its 30-day inflow retire.

#### N1 · The offering raises its proceeds · new

- **Event:** the proposed offering raises at least its proposed net amount by its close date.
- **Proposed terms, stated as facts:** an underwritten public offering of common stock on the company's shelf, at the price the market sets; the size under §2.6; the close date, as many days after initiation as the January 2024 offering took from launch to close. It is not described as committed.
- **Answers:** yes, the proposed net amount is received by the close date; no, it is not received by that date.
- **Asked:** after every initiation (D2, D7, D8), including after an earlier offering on the path did not close. The situation states any earlier attempt and its outcome.
- **Situation:** the judgment and its standing; available cash and any arrears; at-the-market proceeds to date; the listing status and deadline; the notes' status and any default continuing; the share capacity left.
- **Record:** the January 2024 offering (the 8-K of 29 Jan 2024 and its prospectus supplement: 23.0M shares at $0.50, 29% below the prior close, underwritten by Roth), as evidence of what the market did then, not a guarantee; the at-the-market program and its re-activation on 13 May; the 10-Q's liquidity and going-concern text; counsel's statement on the ability to raise money; the shelf's capacity.
- **Consequences:** yes, the net amount on the close date, and its shares drawn from capacity. No, nothing; the next decision point on the path follows, where an offering can be initiated again.
- **Build:** new.

#### D8 · Akoustis files when it cannot pay an obligation · `forecast_petition_cash_out`

- **Event:** on the first day an obligation goes unpaid (§2.2), Akoustis files a voluntary petition, initiates an underwritten offering where one is available (§2.6; N1 follows), or does neither.
- **Answers:** neither, no petition and no offering that day; nothing more.
- **Situation:** it did not file at the cash floor; available cash nil; the obligation unpaid that day and its class; the notes' status; the judgment's standing.
- **Consequences:** file, a petition on the day; initiate, N1, with processing under §2.2 continuing until the close date; neither: processing under §2.2 continues. Slope's debits clear only when the balance covers them, arrears accrue, and after 30 days of continuous arrears §3.3 makes the notes due, with D9 and H3 following.
- **Build:** change. The trigger becomes the first unpaid obligation, the offering is added where available, and the state after "neither" follows §2.2. Keep the conditioning on the floor decision. There is no monthly re-ask.

#### D9 · Akoustis files on the notes · `forecast_petition_on_notes`

- **Event:** Akoustis files a voluntary petition when the notes' principal and interest become due: by declaration, by automatic acceleration under §3.3, or on an unpaid repurchase.
- **Answers:** no, no petition then. The notes stay due and unpaid, in default. No forbearance or restructuring is booked, and the holders' petition (H3) remains open.
- **Situation:** the notes' balance due, stated apart from the judgment; the cash state and arrears as D8; for a §3.3 acceleration, the days of continuous arrears.
- **Grouping:** where D8 and D9 fall on the same day on a path, one question asks the filing.
- **Build:** change the state and the answer's meaning; keep the petition booking.

### 4.5 The notes

#### H1 · Holders accelerate on the judgment default · `forecast_holders_act_judgment`

- **Event:** with the §7.01(i) default available (§3.1), the trustee or holders of at least 25% in principal give notice and declare the notes due, by the stated day. Notice and declaration are one event: notice without a declaration changes no cash.
- **Asked:** where the default becomes available; again at the post-trial ruling where the ruling changes the judgment and the holders have not acted, with the default still available. Not asked where the notes are already due.
- **Answers:** no, no declaration by that day; the default continues while its conditions hold.
- **Situation:** the judgment, its standing and the days it has gone unpaid and unstayed; the issuer's cash state; the notes' terms.
- **Record:** the indenture's default, acceleration and suit terms; the notes' interest terms and payment record; the holders of record where filings show them.
- **Consequences:** yes, the notes are due at the declaration (the notice lag after availability); D9 and H3 follow.
- **Build:** change the legal clock to §3.1 (base: as entered). Keep the `jd_acted` conditioning.

#### H2 · Holders accelerate on the delisting default · `forecast_holders_act_delisting`

- **Event:** after the delisting default (§7.01(b)), the trustee or holders of at least 25% declare the notes due by the stated day.
- **Answers:** no, no declaration by that day.
- **Situation:** includes each holder's individual repurchase right and its repurchase date. That date falls after 10 Nov 2024 on every path, and it bears on the holders' choice.
- **Consequences:** yes, as H1. Repurchase books nothing inside the horizon.
- **Build:** change. The question asks only the declaration; the repurchase date enters the state; the "repurchase only" branch retires.

#### H3 · Noteholders file an involuntary petition · `forecast_holders_involuntary`

- **Event:** qualifying creditors (§303(b)), identified as noteholders with claims for the notes due, file an involuntary petition against Akoustis on the day the §7.06 route (§3.2) first allows it.
- **Eligibility:** the notes are due and unpaid; Akoustis has not filed; the §7.06 route under §3.2 (at once where a (j) default is continuing).
- **Answers:** no, no petition on that day.
- **Situation:** the timing text follows the route under which it is asked.
- **Asked:** only where the petition can fall inside the horizon.
- **Consequences:** the petition stops Slope's collections from its date (§362). An order for relief and the company's operations are separate from the filing; nothing else is booked from the petition.
- **Build:** change the eligibility (the (j) exception) and synchronise the timing text with `holder_route_days()`. Drop the asks beyond the horizon.

### 4.6 The listing

#### D6a · Akoustis regains bid-price compliance · replaces `forecast_listing_kept`

- **Event:** by 21 Oct 2024, the end of its second compliance period, Akoustis regains compliance with the $1.00 minimum bid price (a closing bid of at least $1.00 for 10 consecutive business days), by any route: a reverse stock split or a rise in the price.
- **Answers:** no, compliance is not regained by 21 Oct; the staff's delisting determination follows.
- **Situation:** the bid price on the review date; the judgment's standing; the company's cash and the notes' status; the time a reverse split takes: stockholder approval of a charter amendment under DGCL §242, called and noticed under the bylaws and the proxy rules.
- **Record:** the deficiency notices and the compliance periods; the company's stated options to regain compliance; the latest stockholder vote on a charter amendment; the authorized and outstanding shares.
- **Consequences:** yes, the stock stays listed and the listing chain ends. No cash is booked; the share ledger keeps its dollar capacity. No, D6b.
- **Build:** new.

#### D6b · Akoustis requests a hearing · new

- **Event:** where compliance is not regained, Akoustis requests a hearing before a Nasdaq Hearings Panel within 7 days of the staff's delisting determination.
- **Answers:** no, no timely request; suspension follows on the rule's date.
- **Consequences:** a timely request stays suspension until the panel's decision, dated by code under the 2024 Rule 5815. Suspension, delisting and the indenture's Eligible Market condition are dated separately; the delisting default passes to H2 only when the Eligible Market condition fails.
- **Record:** the deficiency notices; the company's statements on its listing.
- **Build:** new. D6a and D6b replace the bundled listing question.

---

## 5. Checks on the build

Each check takes its expected value from outside the code:

- The §7.01(i) availability day under §3.1 equals the hand computation from the entry day (entry + 30 + 60 days).
- A reduced judgment's surviving amount equals the record computation for its class.
- Every amount in the band above the J1b top line books the same cash on every trajectory.
- The composed probabilities of every node sum to 1.
- The cash facts each question states equal the engine's state on its decision date.
- Date order: every step's cash equals the full run's cash on its day.
- For one trajectory that exhausts its cash without filing, the arrears by class, the day §3.3 is met and the notes' due date equal a hand computation from its receipts, outflows and schedule.
- For one month, at-the-market proceeds and the shares drawn equal a hand computation from §2.6; the share ledger never goes below zero.
- The same-day order's effect on Slope's collections, measured once against operating outflows first, is reported before the page is built.

After the build, the questions are frozen. Stability is measured with the evidence held constant: equivalent paraphrases, option order, and a group's answer re-asked at two other representative trajectories. It measures stability, not calibration.

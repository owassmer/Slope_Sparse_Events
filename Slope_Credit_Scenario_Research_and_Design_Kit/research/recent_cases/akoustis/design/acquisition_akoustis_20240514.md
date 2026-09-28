# Worker C findings: Akoustis as publicly knowable on or before 14 May 2024

Cutoff: 14 May 2024 (the day after the Q3 FY24 10-Q and earnings call). Each fact is tagged **[PRE]** (public on or before 14 May 2024) or **[POST]** (later; reveal-only, must not inform the forecast).
"Source says" is quoted. My own reasoning is marked **INFERENCE**.

Primary source for sections 1(c), 3 and 4: Akoustis Form 10-Q for the quarter ended 31 Mar 2024, filed 13 May 2024 (accession 0001213900-24-041897).
URL: https://www.sec.gov/Archives/edgar/data/1584754/000121390024041897/ea0204504-10q_akoustis.htm **[PRE]**

Section 5 is a one-table summary. Section 6 is the isolation log.

---

## 1. Amounts at stake before the verdict

**Headline result, revised after reading D.I. 543-1.** Qorvo's full itemised damages claim was **public from 22 Apr 2024**. It is in "EXHIBIT A.2 TO PRETRIAL ORDER — QORVO, INC.'S STATEMENT OF INTENDED PROOF AND DETAILS OF DAMAGES CLAIMED AND OTHER RELIEF SOUGHT", attached to the public version of the proposed pretrial order: D.I. 543-1, filed 22 Apr 2024, pages 12–25 of 234 (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.543.1.pdf). **[PRE]** The figures are unredacted there, although the same figures are blacked out in the February *Daubert* briefs:

| Claim | Amount (verbatim) | ¶ in D.I. 543-1 Ex. A.2 |
|---|---|---|
| Patent ('755 and '018) | "Qorvo is owed damages of $279,808 for Akoustis' infringement of the '755 and '018 Patents for the period of October 5, 2021 through June 30, 2024." Also: "entitling Qorvo to treble damages" (¶¶16, 18) | ¶19 |
| Trade secrets (DTSA and NCTSPA) | "Akoustis has been unjustly enriched by at least $66.1 million as a result of its 55-month head start." Also: "Qorvo is entitled to enhanced damages under both the DTSA and the NCTSPA." (¶32) | ¶31 |
| False advertising (Lanham Act) | "Qorvo is also owed damages of $1,146,604 for corrective advertising that addresses Akoustis's false claims." | ¶42 |
| UDTPA (employee poaching) | "Qorvo is owed damages of $809,772 for the cost to recruit, hire, and train additional employees." | ¶49 |
| UDTPA (unjust enrichment) | "Qorvo is owed $66.1 million, representing the amount by which Akoustis has been unjustly enriched by its unfair and deceptive trade practices." | ¶50 |
| Civil conspiracy | "Qorvo is owed damages of $66.1 million for Akoustis's conspiracy to misappropriate Qorvo's trade secrets for Akoustis's benefit." Also: "Qorvo is also entitled to punitive damages based upon Akoustis's civil conspiracy." (¶57) | ¶56 |
| Catch-all | "Qorvo intends to present fact and expert testimony to establish its entitlement to compensatory damages, punitive damages, treble damages, exemplary damages, reasonably royalty damages, and unjust enrichment damages." Relief sought includes "pre- and post-judgment interest, attorneys' fees, disgorgement of any ill-gotten gains (including unjust enrichment damages), and all other just and proper relief." | ¶¶61–62 |
| Dropped counts | "Qorvo will not pursue these two counts at trial" (false marking and RICO) | ¶59 |

- Qorvo's proposed verdict form, D.I. 535, filed 12 Apr 2024 (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.535.0.pdf), Question 4: "what amount of exemplary damages, if any, do you award to Qorvo against Akoustis? The amount you award, if any, cannot exceed three times the amount of damages listed in response to Question 2." [PRE]
- Akoustis's Statement of Intended Proofs (D.I. 543-1, Ex. A.4) gives **no counter-figure**. It only says that Akoustis "intends to refute that Qorvo is entitled to any damages". Akoustis's $305k avoided-cost number was **not public** before the cutoff; see 1(c).
- **INFERENCE: scenario arithmetic from these figures.** The three $66.1M items are the same unjust-enrichment amount pleaded under alternative theories, so they are not additive.
  - Non-duplicative compensatory claim: 66.1M + 0.280M + 1.147M + 0.810M ≈ **$68.3M**.
  - Exemplary add-on: Qorvo's form proposed a cap of up to 3× the trade-secret damages. The DTSA itself caps exemplary damages at 2×.
  - Before D.I. 590, UDTPA trebling of $66.1M was a live claim. D.I. 590 (14 May) removed it from the jury (1(d)).
  - Theoretical ceiling before interest and fees: 66.1 + 3×66.1 (NC punitive) + 3×0.28 (patent) + 1.15 + 0.81 ≈ **$266M**.
  - Realistic "Qorvo full win" band: **$68M compensatory, up to about $200M** with 2× DTSA exemplary damages.
  - These are scenario bounds, not source statements.
- I found no press report of a demanded amount before 15 May 2024 (see 1(f)).

### 1(a) Qorvo's pleadings: the prayer, with no dollar figure [PRE]

- Original complaint, D.I. 1, filed 4 Oct 2021 (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.1.0.pdf). Prayer items include: "2) Award Qorvo damages in an amount sufficient to compensate it for Akoustis' infringement of the Patents-in-Suit, together with pre-judgment and post-judgment interest and costs, and all other damages permitted under 35 U. S. C. § 284; ... 4) Enter a judgment awarding Qorvo treble damages as a result of Akoustis' willful and deliberate infringement of the Patents-in-Suit, pursuant to 35 U.S.C. § 284; 5) Find that Akoustis' actions render this an exceptional case under 35 U.S.C. § 285 and award Qorvo its attorneys' fees, costs and expenses". ¶132: "Pursuant to N.C.G.S. § 75-1.16 and N.C.G.S. § 75-1.6.1 Qorvo is entitled to treble damages and attorney fees for Akoustis' unfair and deceptive trade practices".
- Second Amended Complaint. The sealed version is D.I. 125, filed 8 Feb 2023; the **public version is D.I. 133**, filed 17 Feb 2023 (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.133.0.pdf). Prayer items include:
  - "12) Award Qorvo damages, including exemplary damages, punitive damages, attorneys' fees, and costs and expenses in view of Akoustis' violations under the North Carolina Trade Secrets Protection Act ... and/or under the Defense of Trade Secrets Act";
  - "19) Award Qorvo damages, including treble damages, punitive damages, attorneys' fees ... under 18 U.S.C. § 1961 et seq." (RICO, later dismissed at SJ);
  - "23) Award Qorvo damages, including treble damages, punitive damages, and attorneys' fees, in view of Akoustis' violation of N.C. Gen. Stat. § 75-1.1";
  - "25) Order a full and complete accounting to Qorvo for Akoustis' profits, gains, advantages or the value of business opportunities received";
  - "26) Award Qorvo all actual, compensatory, consequential, liquidated, treble, and special damages to which it is entitled in an amount to be determined at trial; 27) Assess punitive damages against Akoustis; 28) Award Qorvo all attorneys' fees and costs ...; 29) Award pre-judgment and post-judgment interest".
  - In the body: "Qorvo is therefore entitled to exemplary damages and attorney fees and costs."
  - **No dollar amount is stated.**

### 1(b) Pretrial order: body lists categories; Ex. A.2 (D.I. 543-1) itemises the figures [PRE]

- The body of the [PROPOSED] Final Pretrial Order, public version D.I. 543, was filed 22 Apr 2024 (confidential version D.I. 530 filed 8 Apr) (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.543.0.pdf). Section 6, "Alleged Damages": "Qorvo seeks damages to compensate for Akoustis's patent infringement, false advertising, misappropriation of trade secrets, unfair and deceptive trade practices, and civil conspiracy. Qorvo also seeks prejudgment and post-judgment interest and costs, attorneys' fees, and enhanced damages to the full extent permitted by the law applicable to each of Qorvo's claims. Qorvo also seeks an injunction preventing Akoustis from further infringing the patents-in-suit, from further falsely advertising the properties of its BAW filters, from further misuse of Qorvo's trade secrets, and from further use of unfair and deceptive trade practices. Qorvo also seeks punitive damages." **No figure appears in the body.** The figures are in Exhibit A.2, D.I. 543-1 (see the headline table above).
- The Final Pretrial Order as entered (D.I. 549, signed 26 Apr 2024) is **[SEALED]** on the docket. I found no public version before the cutoff.

### 1(c) Expert damages figures in public *Daubert* papers [PRE]

- **D.I. 476**, filed 23 Feb 2024: REDACTED VERSION of D.I. 457, "Opening Brief in Support of the Akoustis Defendants' Motion to Exclude Certain Damages Opinions of Melissa A. Bennis", dated 9 Feb 2024, marked "[PUBLIC VERSION]" (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.476.0.pdf). Verbatim, with the blanks as they appear in the public PDF:
  > "Ms. Bennis opines that Qorvo is entitled to recover the following alleged damages:
  > o Unjust enrichment for trade secret misappropriation under a "head-start" theory in the amount of $66.1 million;
  > o Unjust enrichment and damages for unfair competition under North Carolina law in the amount of $[redacted] for trade secret misappropriation, false advertising damages of $[redacted] for corrective advertising; and $[redacted] in damages for employee poaching; and
  > o Patent damages in the form of a reasonable royalty through June 30, 2024 in the amount of $[redacted]
  > (Elkins Decl. Ex. A (Bennis Report) at 90.)"

  The same brief: "Her new, untested variation of a "head-start" analysis is Qorvo's sole support for all trade secret misappropriation damages and for the trade secret portion of unfair competition damages." It attacks the use of revenue delayed by "55 months".
- **D.I. 496**, filed 4 Mar 2024: Qorvo's redacted opposition (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.496.0.pdf). It states the theory but no amount: "Akoustis's theft of Qorvo's trade secrets gave it a 55-month head start"; "Bennis determined the economic benefit to Akoustis from the 55-month head start was gaining ...".
- **D.I. 508**, filed 12 Mar 2024: Akoustis's redacted reply (https://storage.courtlistener.com/recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727.508.0.pdf). "She then shifted all of Akoustis' revenue from 2016 through June 30, 2024 – 55 months in the future – and used the WACC to calculate its time value as of June 30, 2024." No amount.
- **D.I. 471**, filed 23 Feb 2024: Qorvo's redacted brief to exclude Irwin (Akoustis's expert, avoided costs). No Akoustis-side dollar figure is visible.
- **D.I. 553**, filed 30 Apr 2024: the court's *Daubert* order (kit: ded_21cv1417_d553_order.pdf). "Defendants' Motion to Exclude the Testimony of Melissa Bennis on "Head-Start" Damages is DENIED." It gives the basis as "the Akoustis products were brought to market 55 months earlier than they would have without the alleged trade secrets", and reserves the poaching opinion for supplemental briefing due 2 May 2024. The order contains **no dollar figures**.
- **D.I. 545** (25 Apr; SJ: RICO and false marking dismissed) and **D.I. 557** (2 May; '018 and '755 validity granted for Qorvo) contain no damages figures. I grepped the kit PDFs for "$" and "million".
- **Akoustis's own damages figure (Irwin avoided costs, about $305,000) is [POST] as a public fact.** It first appears in the trial transcript (13–15 May, Tr. 2341) and in D.I. 613 (17 Jun). The transcript was not publicly available on 14 May. I found no pre-cutoff public filing stating it.
- **INFERENCE:** the amounts redacted in D.I. 476 are the ones itemised in D.I. 543-1 (22 Apr): $66.1M NC unfair-competition unjust enrichment, $1,146,604 corrective advertising, $809,772 poaching, and $279,808 patent royalty. They were redacted in February but public in April. Qorvo's expert also produced an alternative $50.3M figure on updated FY24 revenue (Tr. 1699–1700, 13 May), but the transcript was not public at the cutoff, so treat $50.3M as [POST] unless a same-day press report carried it. I found none.

### 1(d) Exemplary, enhanced, trebled damages and fees requested before the verdict [PRE]

- Requested categorically, never with an amount: SAC prayer 12, 19, 23, 26–28 (above); the pretrial order ("enhanced damages ... punitive damages ... attorneys' fees").
- Statutory caps: **INFERENCE (law, not a source statement)**. DTSA exemplary damages are capped at 2× under 18 U.S.C. § 1836(b)(3)(C). NC punitive damages are capped at the greater of 3× or $250k under N.C.G.S. § 1D-25. Patent enhancement is up to 3× under § 284. UDTPA trebling is automatic under § 75-16. On a $66.1M base, the theoretical DTSA ceiling is $66.1M + $132.2M = $198.3M, and the theoretical UDTPA-trebled figure is $198.3M. These are ceilings only.
- **UDTPA trebling was cut back before the cutoff.** Qorvo's bench memorandum, D.I. 587, filed 13 May 2024 (kit PDF), argued: "[m]isappropriation of trade secrets constitutes an unfair or deceptive act or practice as a matter of law" and "such disgorgement is proper under the UDTPA". The court's order, D.I. 590, filed **14 May 2024** (kit PDF), ruled: "North Carolina Courts have allowed for disgorgement of profits by defendants to serve as damages where they are proxies for injury by plaintiff, but has not recognized head start benefits to the Defendants as such a proxy." And: "Plaintiffs base their damages assessment exclusively on the DTSA's head start benefit damages provision." And: "Because Plaintiff's only basis for proving damages to a reasonable certainty is inconsistent with the damages cognizable under the UDTPA, the Defendants' 50(a) Motion is GRANTED IN PART AND DENIED IN PART. The jury has no reasonable basis to find compensable damages under the UDTPA, but has sufficient basis to find injury."
- **Timing caveat:** D.I. 590 was docketed on the cutoff day (14 May). It is [PRE] under the "on or before 14 May" rule, but it is doubtful that a lender reading the 10-Q would have seen it the same day.
- Qorvo's $93.9M trebling motion (D.I. 611) and $12.1M fee motion (D.I. 618) are [POST] (17 Jun 2024).

### 1(e) Akoustis's own disclosure [PRE]

- 10-Q of 13 May 2024, Note 14: "The plaintiff seeks an injunction enjoining the Company from the alleged infringement and damages, including punitive and statutory enhanced damages, in an unspecified amount." The same sentence is repeated in the Part II Item 1 legal-proceedings section.
- 10-Q Note 14: "The trial for Qorvo Inc. vs. Akoustis Technologies, Inc., DE Case 1:21-cv-01417-JPM commenced on May 6, 2024. The Company can provide no assurance as to the outcome of such trial, and the result may include judgments against the Company for an injunction, significant damages or other relief, such as future royalty payments to Qorvo or restrictions on certain of the Company's activities."
- 10-Q Note 14: "On April 30, 2024, the court denied each party's Motion to Exclude the Expert Witness Testimony of the other party's damages expert."
- **No accrual or range is disclosed.** I searched the full 10-Q text for "accru", "estimab", "reasonably possible", "probable", "loss contingenc" and "ASC 450": nothing appears in the Qorvo discussion, and there is no loss-contingency accrual. **INFERENCE:** this is consistent with management treating a loss as not reasonably estimable.

### 1(f) Press before 15 May 2024

- **I found no press report giving a Qorvo damages demand before 15 May 2024.** Searches run: Law360, Reuters, Bloomberg Law, and general web, for "Qorvo Akoustis trial", "$66" and "head start".
  - Law360's May 2024 archive pages for Delaware (https://www.law360.com/delaware/archive/2024/05) and IP (https://www.law360.com/ip/archive/2024/05), as fetched, list only one Akoustis/Qorvo story: "Qorvo Wins $38.6M In Akoustis Trade Secrets And Patent Trial" (Ryan Davis, 17 May 2024, 10:31 PM EDT) [POST].
  - Caveat: the archive fetch is one page per section and Law360 is paywalled. A short in-trial Law360 item could exist unindexed. **Not found ≠ proven absent.**
- Later coverage, all [POST] and reveal-only: Bloomberg Law, "Akoustis Floats Bankruptcy on $39 Million Qorvo Verdict Loss" (20 May); Benzinga (22 May); Sheppard Mullin release (21 May: "a two-week jury trial"); Qorvo release (20 May).
- **INFERENCE:** the press added nothing before the cutoff. The public numbers came from the docket (D.I. 476, 23 Feb; D.I. 543-1, 22 Apr) and from Akoustis's own call.

### 1(g) Akoustis's own quantitative signal on the 13 May 2024 call [PRE]

Source: Q3 FY24 earnings call, 13 May 2024, 8:00 am ET. Two transcripts:
- Earnings Whispers, machine transcript ("Corvo" is a mis-transcription of Qorvo): https://beta.earningswhispers.com/transcript/AKTS/Q32024
- Insider Monkey, published 14 May 2024 08:21: https://www.insidermonkey.com/blog/akoustis-technologies-inc-nasdaqakts-q3-2024-earnings-call-transcript-1303277/. Its search snippet corroborates the trial-timing wording. I could not load its full text (bot wall), but it was the top hit for the exact phrase "eight-figure verdict".

Drew Wright, Corporate General Counsel (verbatim from Earnings Whispers, with the Qorvo spelling corrected in brackets):
> "Now, after two and a half years, The lawsuit has moved to the trial phase, which began last week and is expected to finish this week. ... Last week, [Qorvo] presented its fact witnesses and several expert witnesses. [Qorvo] is expected to present its final expert witness and rest its case this morning. This week, the company will have its opportunity to tell its side of the story. The trial is expected to go to the jury before the end of the week, and the jury is expected to issue a verdict in the next several days. We are and will continue to vigorously defend against [Qorvo]'s claims. However, the outcome of any trial is unpredictable. In particular, the theories used by each party's experts result in very disparate estimates of the damages alleged by [Qorvo]. We believe the theories used by our experts are better grounded in the facts and the law. However, if the jury adopts the theories of [Qorvo]'s experts, it is possible the jury will issue an eight-figure verdict. For that reason, this litigation may have a significant impact on the company. including its value, operations, and ability to raise money."

**INFERENCE:** "eight-figure" means $10M–$99.9M. That is consistent with Qorvo's public $66.1M claim (plus about $2.2M of other items) and implies that management did not expect the jury to add exemplary damages taking the total above $100M.

---

## 2. Trial schedule and expected verdict timing (all from the CourtListener docket capture, kit `ded_21cv1417_docket_20240620.html`)

| Date | Entry (verbatim) | Tag |
|---|---|---|
| 15 Mar 2023 | Scheduling order: "Proposed Pretrial Order due by 3/11/2024. A Pretrial Conference is set for 4/8/2024 ... A Jury Trial is set for 4/22/2024 at 09:30 AM before Judge Jon P McCalla." | [PRE] superseded |
| 10 May 2023 | Minute entry: "The Jury Trial is reset to Monday, May 6, 2024 at 9:30 AM (ET)". D.I. 198, AMENDED SCHEDULING ORDER: "Dispositive Motions due by 2/1/2024. Proposed Pretrial Order due by 4/3/2024. A Pretrial Conference is set for 4/19/2024 ... A Jury Trial is set for 5/6/2024 at 09:30 AM". | [PRE] |
| 2 Apr 2024 | Minute entry (Daubert motion hearing): "The Video Pretrial Conference remains set for Friday, April 19, 2024 ... The Jury Trial is set in Delaware on Monday, May 6 2024 at 9:00 AM (ET)." | [PRE] |
| 22 Apr 2024 | D.I. 543, [Proposed] Final Pretrial Order (public version), §X.A "Length & Time": "This matter is scheduled for a jury trial beginning at 9:30 a.m. ET on May 6, 2024 ... The trial will be timed. The Court will allocate to counsel for each party ____[fn 3] hours in which to present their respective cases, inclusive of time for opening and closing argument but exclusive of time for voir dire." Footnote 3: "Subject to the Court's availability, the parties propose no more than 25 hours per side." "The jury shall be composed of at least eight (8) members." | [PRE] |
| 19 Apr 2024 | Pretrial conference minute entry: "The Court reviewed jury trial schedule, procedures, and jury selection process ... Jury Selection will begin on Monday, May 6, 2024 at 9:00 AM (EST). Opening statements are anticipated by Tuesday morning, May 7, 2024. Pretrial Order to be entered." | [PRE] |
| 26 Apr 2024 | D.I. 549: "[SEALED] FINAL PRETRIAL ORDER. Signed by Judge Jon P McCalla on 4/26/2024." The allotted hours as entered are not public. | [PRE] (sealed) |
| 6–14 May 2024 | Minute entries "Day 1" (6 May) through "Day 7 of a Jury Trial ... held on 5/14/2024". | [PRE] |
| 13 May 2024 | 10-Q: "The trial ... commenced on May 6, 2024." Going-concern note: "the Delaware Trial is currently in process." | [PRE] |
| 15–16 May | Days 8 and 9. | [POST] |
| 17 May 2024 | "Minute Entry for the Final Day of a Jury Trial ... Jury Trial completed on 5/17/2024"; verdict; judgment (D.I. 602) entered 20 May. | [POST] |

- **Stated trial length:** no pre-cutoff source states a trial length in days. The only quantitative statement is the parties' proposal of "no more than 25 hours per side" (D.I. 543 n.3). The hours actually allotted (D.I. 549) are sealed.
- **Management's own timing statement, 13 May 2024 [PRE]:** "The trial ... began last week and is expected to finish this week ... [Qorvo] is expected to present its final expert witness and rest its case this morning ... The trial is expected to go to the jury before the end of the week, and the jury is expected to issue a verdict in the next several days." (Earnings Whispers transcript; Insider Monkey snippet published 14 May has the same wording.) **This is the best pre-cutoff statement of the expected verdict date: jury deliberation by Fri 17 May, verdict about 17–22 May.**
- Qorvo rested on 13 May, Day 6. **INFERENCE:** with the parties' proposed cap of 25 hours per side, Akoustis's case would take about Days 6–8.
- **INFERENCE (expected verdict timing as of 14 May):**
  - 50 hours of presentation (25 per side), plus jury selection and openings (6–7 May), instructions and closings, at roughly 5–6 trial hours a day, is about 9–11 court days.
  - Counting from 6 May, the case would reach the jury around **Thu 16 – Fri 17 May**, with a verdict **16–21 May** (a weekend deliberation spill would push it to Mon 20 May).
  - By 14 May the defense had already made its Rule 50(a) motions (D.I. 587 opposition filed 13 May; D.I. 590 ruling 14 May), which implies Qorvo's case in chief had closed by 13 May and the defense case was under way.
  - The actual outcome, final day 17 May, fits the centre of this window.
  - Judgment entry: Rule 58 judgment typically follows within days. After that, Rule 62(a) gives an automatic 30-day stay of execution.

---

## 3. Nasdaq minimum bid price

| Fact | Verbatim | Source, date | Tag |
|---|---|---|---|
| Date of the notice | "On October 24, 2023, the Company received notification from the Listing Qualifications Department of The Nasdaq Stock Market, or Nasdaq, stating that the Company did not comply with the minimum $ 1.00 bid price requirement for continued listing set forth in Nasdaq Listing Rule 5550(a)(2) (the "Bid Price Requirement")." | 10-Q Note 12, 13 May 2024. The original disclosure is the 8-K (Item 3.01) filed 27 Oct 2023, accession 0001013762-23-007328: https://www.sec.gov/Archives/edgar/data/1584754/000101376223007328/ea187341-8k_akoustis.htm | [PRE] |
| First 180-day period | "the Company was afforded 180 calendar days (until April 22, 2024) to regain compliance with the Bid Price Requirement (the "Initial Compliance Period")." | 10-Q Note 12 | [PRE] |
| Cure test | "the closing bid price of the Company's common stock must meet or exceed $ 1.00 per share for a minimum of ten consecutive business days during this additional 180-day period" | 10-Q Note 12 | [PRE] |
| Second period granted | "Since the Company did not regain compliance by April 22, 2024, the Company requested, and was granted, an additional 180 calendar days for the Company to regain compliance with Bid Price Requirement expiring October 21, 2024." | 10-Q Note 12 | [PRE] |
| Planned cure | "The Company intends to monitor the closing bid price of its common stock and consider available options to regain compliance with the Bid Price Requirement which could include seeking to effect a reverse stock split." | 10-Q Note 12 | [PRE] |
| Price and consequences (risk factor) | "The closing price of our common stock was $0.60 on May 1, 2024. There can be no assurance that we will regain compliance with the Bid Price Requirement by the second compliance period deadline of October 21, 2024. Our inability to regain compliance with the Bid Price Requirement would materially impair our ability to raise capital. Moreover, our common stock would likely then trade only in the over-the-counter market..." | 10-Q Part II, Item 1A | [PRE] |

- 8-K of 27 Oct 2023, Item 3.01, verbatim: "because the closing bid price for the Company's common stock has fallen below $1.00 per share for 30 consecutive trading days ... Pursuant to Nasdaq Marketplace Rule 5810(c)(3)(A), the Company has been provided with a compliance period of 180 calendar days, or until April 22, 2024 ... If the Company does not regain compliance by April 22, 2024, the Company may be eligible for an additional grace period. To qualify, the Company must, as of the final day of the Initial Compliance Period, meet the applicable market value of publicly held shares requirement for continued listing and all other applicable standards for initial listing on the Capital Market (except the bid price requirement) ... and must notify Nasdaq of its intent to cure this deficiency." [PRE]
- I found no 8-K announcing the grant of the second period. The EDGAR filing index for Apr–May 2024 lists no Item 3.01 8-K. The first public statement of the 21 Oct 2024 deadline is therefore the 10-Q of 13 May 2024, which is still before the cutoff.
- **INFERENCE:** 21 Oct 2024 falls inside the 14 May–10 Nov 2024 horizon. If the price has not cured by then, the mid-2024 Nasdaq rules (see the kit's RESEARCH.md R2d) provide a Delisting Determination, and a hearing request that ordinarily stays suspension. Suspension or delisting before 10 Nov 2024 is therefore unlikely unless a hearing is not requested, or the $0.10 rule (ten consecutive days at or below $0.10) is triggered. Under the 2022 indenture, delisting is a Fundamental Change that gives holders a put.

---

## 4. Financing and liquidity as of 14 May 2024

### 4.1 Cash, runway, going concern (10-Q, 13 May 2024) [PRE]

- "As of March 31, 2024, the Company had cash and cash equivalents of $ 15.2 million and working capital of $ 10.3 million. In the absence of additional liquidity, the Company anticipates that its existing cash resources, with a continued focus on cash conservation, is sufficient to fund its operations into the third quarter of fiscal 2025." (Note 2) **INFERENCE:** fiscal Q3 2025 is Jan–Mar 2025.
- "Furthermore, an adverse judgment against the Company in the trial with respect to Qorvo Inc. vs. Akoustis Technologies, Inc. DE Case 1:21-cv-01417-JPM ... (the "Delaware Trial"), would reduce the amount of time that the Company could continue operating with its current cash resources, and create an urgent need for additional liquidity or result in the Company's curtailing or ceasing operations and seeking protection by filing a voluntary petition for relief under the Bankruptcy Code. These matters raise substantial doubt about the Company's ability to continue as a going concern within one year from the date of this filing." (Note 2)
- "Additionally, the Delaware Trial is currently in process. To the extent that the outcome of the trial includes judgments against the Company for significant damages or other relief, the Company's liquidity will be additionally and severely constrained." (Note 2)
- "the Company estimates that approximately $ 0.7 million of additional cash is needed to complete construction in progress assets that are currently not in service, which construction has been paused as part of these cost-savings initiatives." (Note 2)
- **Internal inconsistency in the same filing.** The MD&A also says: "The Company estimates that cash on hand will be sufficient to fund its operations as currently conducted, including current capital expense commitments, for at least the next twelve months from the date of filing of this Form 10-Q." (MD&A, Balance Sheet and Working Capital). That contradicts "into the third quarter of fiscal 2025" and the going-concern language. **INFERENCE:** this is most likely stale carried-over text. A lender should use the going-concern and "into Q3 FY2025" statements, which appear in both the Note and the MD&A overview.
- Cash burn: "The decrease is primarily due to cash used in operations of $32.2 million, and cash used for investing activities of $6.1 million. These uses of cash were partially offset by cash proceeds related to the January 2024 equity raise of $10.4 million." (MD&A: 9 months to 31 Mar 2024, cash fell from $43.1M to $15.2M.)
- Obligations named: "(v) interest and principal payments related to its $ 44.0 million aggregate principal amount of outstanding convertible notes and $ 4.0 million promissory note." (Note 2)

### 4.2 Shelf registration and ATM status at 14 May [PRE]

**Correction to the brief.** The S-3 filed around the cutoff was filed **13 May 2024**, not 16 May, so it is [PRE]. It is a **resale** registration for selling stockholders and raises no money for the company:
- S-3 (accession 0001213900-24-042407), filed 13 May 2024: https://www.sec.gov/Archives/edgar/data/1584754/000121390024042407/ea0205746-s3_akoustis.htm. It covers "up to 5,028,291 shares ... by the persons described in this prospectus, whom we call the "Selling Stockholders" ... 5,000,000 of the Shares (the "Note Shares") represent shares that may be issued, at our election, as payment of accrued interest on our 6.0% Convertible Senior Notes due 2027 (the "Notes"), as make-whole payments made in connection with certain conversions of the Notes or as payments made in connection with qualifying fundamental changes ... which exceed the estimate of shares ... included ... in the Company's previously filed Registration Statement on Form S-3 (File No. 333-267394)." It also says: "We will not receive any of the proceeds from the sale or other disposition of such securities offered by the Selling Stockholders."
- The SEC upload (16 May) and the company's CORRESP (17 May) are [POST]. So is the EFFECT notice: it lists this S-3 as effective 21 May 2024 16:30 (EDGAR index).
- **INFERENCE:** registering 5.0M more note shares shows that on 13 May the company was preparing to pay note interest (the next payment falls on 15 Jun 2024) in stock.

**The primary shelf that existed before the cutoff:** Form S-3, File No. **333-262540**, filed 4 Feb 2022 and declared effective 15 Feb 2022 (EDGAR: S-3 accession 0001213900-22-005538; EFFECT 2022-02-15). The January 2024 offering used it: "Filed Pursuant to Rule 424(b)(5) Registration No. 333-262540 PROSPECTUS SUPPLEMENT (to the Prospectus dated February 15, 2022)". That offering sold 20,000,000 shares at $0.50 for $10.0M gross: 424B5 of 29 Jan 2024, https://www.sec.gov/Archives/edgar/data/1584754/000121390024007157/ea192165-424b5_akoustistech.htm.
- **INFERENCE:** under Rule 415(a)(5), a non-WKSI shelf is usable for three years from effectiveness, so this one was live until about 15 Feb 2025. The ATM sits on the same shelf, verified: the 424B5 of 2 May 2022 reads "Filed Pursuant to Rule 424(b)(5) Registration No. 333-262540 ... Common Stock Having an Aggregate Offering Price of up to $50,000,000", with agent compensation "up to 3.0%" (https://www.sec.gov/Archives/edgar/data/1584754/000121390022022908/ea159115-424b5_akoustistech.htm). The amount remaining on the shelf, and the capacity under the "baby-shelf" rule (General Instruction I.B.6, which applies if public float is below $75M), are **not stated** in any pre-cutoff text I read. At about 98.7M shares and $0.60, market cap is about $59M, so I.B.6 would likely cap primary shelf sales at one-third of public float per 12 months, net of the January 2024 $10M. This is unverified: the 424B5 of 23 May [POST] states the actual I.B.6 computation.
- The January 2024 lock-up had lapsed: "We, our directors and executive officers have agreed ... for a period of 90 days after the closing of the offering, not to offer, sell ... any shares of common stock" (424B5, 29 Jan 2024; closing about 29 Jan). **INFERENCE:** it expired about 28 Apr 2024, before the cutoff.

**The ATM was re-activated on 13 May 2024:**
- "On May 2, 2022, the Company entered into an ATM Sales Agreement ... with Oppenheimer & Co. Inc., Craig-Hallum Group LLC and Roth Capital Partners, LLC ... the Company may sell from time to time through the Sales Agents shares of Common Stock having an aggregate offering price of up to $50,000,000 ... Effective May 24, 2022, the Company suspended sale under the Sales Agreement after making sales of $2.0 million of Common Stock thereunder. Effective with the filing of this Quarterly Report on Form 10-Q, the Company is re-activating its at-the-market offering program under the Sales Agreement." (10-Q Part II, Item 5)
- "Except for the $ 48.0 million of common stock remaining available to be sold under its ATM Sales Agreement ... the Company has no commitments or arrangements to obtain any additional funds, and there can be no assurance such funds will be available on acceptable terms or at all." (Note 2)
- "However, the sales agents are under no obligation to make any sales of our Common Stock under the at-the-market offering program and we may not be able to access all, or any portion, of the potential funding represented by the facility, or access sufficient funding quickly enough to meet our short-term liquidity needs." (MD&A)
- The ATM suspension of 22 May 2024 (8-K) and the $0.20 registered direct offering of 22–24 May are [POST].

**Authorized share headroom [PRE]:** "Common stock, $ 0.001 par value; 175,000,000 shares authorized; 98,654,282 ... shares issued and outstanding at March 31, 2024" (10-Q balance sheet). The cover says "As of May 8, 2024, there were 98,669,282 shares ... issued and outstanding." **INFERENCE:** about 76M shares were authorized but unissued, before reserves for options, RSUs, note conversion (about 9.3M at 212.3142 per $1,000 on $44M) and note interest or make-whole shares. The unreserved figure was not stated before the cutoff. The "about 3.0M unreserved" figure comes from the 424B5 of 23 May [POST], after the 50M-share offering.

### 4.3 Debt service to 10 Nov 2024 [PRE]

- **Convertible notes:** "6.0% convertible senior notes 06/15/2027 6.00 % $ 4.71 $ 44,000" (10-Q Note 10, face $44.0M). The FY2023 10-K says: "The Notes bear interest at a rate of 6.0% per year until maturity on June 15, 2027, payable semi-annually beginning on December 15, 2022. At the Company's option, interest may be paid in cash and/or shares of Common Stock." **INFERENCE:** $44.0M × 6% ÷ 2 = **$1.32M due 15 Jun 2024**, inside the horizon. The next payment, 15 Dec 2024, falls after 10 Nov 2024. Either can be paid in shares, subject to share registration and availability.
- **GDSI seller note:** "The Company's wholly-owned subsidiary, Akoustis, Inc. issued a secured promissory note ... in the original principal amount of $ 4.0 million to the Sellers' representative in connection with the Company's acquisition of GDSI in January 2023. The Sellers' representative is a current employee of the Company. The Promissory Note does not bear interest, is subject to partial prepayment (reduction of the outstanding principal amount down to $ 1.3 million) on the second anniversary of the Closing Date, and is payable in full on the third anniversary of the Closing Date. ... if GDSI's President is terminated for cause or due to disability or resigns without good reason prior to maturity the Promissory Note will be cancelled in its entirety. The Promissory Note is secured by certain of the Purchaser's and GDSI's assets. In the event of certain events of default, including failure to pay amounts due under the Promissory Note and certain bankruptcy events, the outstanding principal amount of the Promissory Note will become immediately due." (10-Q Note 10)
  - Closing Date: "On January 1, 2023 (the "Closing Date")" (FY2023 10-K Note 7, filed 6 Sep 2023). The second anniversary is therefore **1 Jan 2025** (a $2.7M prepayment) and the third is **1 Jan 2026** (the $1.3M balance). **Neither falls inside the horizon to 10 Nov 2024.** Acceleration on a bankruptcy event would pull the full $4.0M forward.
  - Accounting: "The Promissory Note will be recognized on a straight line basis over the term of the Promissory Note as compensation expense." The balance sheet carries "Promissory note payable 1,667" (current) at 31 Mar 2024. That is an accrued compensation liability, not the $4.0M face.
- Leases: "For the year ending June 30, 2024 $ 150 2025 606 ..." (10-Q Note 13; thousands, undiscounted). The amounts are small.
- The 10-Q discloses no other borrowed-money debt. OCIDA lease/PILOT benefits of "approximately $ 0.4 million ... subject to claw back ... upon certain recapture events, including certain events of default" (Note 14).

### 4.4 Management's cost plan, breakeven and CHIPS credits: 13 May 2024 call and release [PRE]

- **Press release (8-K Ex. 99.1, 13 May 2024)**, kit `akts_2024_05_13_release.html`; GlobeNewswire https://www.globenewswire.com/en/news-release/2024/05/13/2880323/0/en/Akoustis-Reports-Third-Quarter-FY24-Results.html:
  - "the Company expects between $7.0 to $7.5 million in sales revenue in the June quarter."
  - "We continue to take significant expense reductions and cost-saving measures that have resulted in a 31% reduction in our operating cash flow burn rate for the March quarter as compared to December. Given the top-line projections, the refund from the CHIPS Act investment tax credit (ITC), and our continued cost savings, we currently expect operating cash flow to be breakeven within the next nine months."
  - Balance sheet: "Promissory note payable 1,667" (current); cash $15,200K.
- **Call, Jeff Shealy (CEO), verbatim (Earnings Whispers):** "the CHIPS Act of 2022 includes a provision for a 25% refundable investment tax credit, or CHIPS ITC, on investments in facilities that manufacture semiconductors that were placed in service after December 31st, 2022. We currently estimate the amount of the refundable tax credit applicable to [Akoustis] to be between $2.8 and $4 million over the next nine to 12 months."
  - He also referred to a DoD ME Commons hub proposal "potentially worth several million dollars annually, beginning in the second half of calendar 2024" (still awaiting a decision).
- **Call, Ken Boller (CFO), verbatim:**
  - "cash use and operating activities was $7.8 million, representing a 31% reduction over the December quarter"
  - "we continue to undertake aggressive expense reduction and cost-saving measures, as well as pursuing the investment tax credits, or ITCs, all of which we estimate will reduce our operating cash flow burn rate by an additional 30% sequentially in the June quarter. Given the top-line projections, the current timing of the CHIP's ITC refunds, and the full impact of recent cost savings, we expect to be operating cash flow breakeven within the next nine months."
  - In Q&A: "I do anticipate it coming down to the $10 to $11 million per quarter range on OPEX on the expense line. We have identified over $20 million in annualized savings on our OPEX." And: "we've stated that our operating cash flow will break even ... in the $11 to $15 million of revenue per quarter, depending on mix, and that will come with a roughly 25% margin on those sales".
  - "We are guiding the June quarter revenue to be flat to down 5%."
- **10-Q on the CHIPS ITC:** "during the quarter ended March 31, 2024, the Company claimed $644 thousand of investment tax credit which will be realized over the useful life of the claimed property." And on the grant programme: "We have received feedback on our pre-application from the DoC and we expect to file a final application in calendar year 2024." (MD&A, Recent Legislation)
- **INFERENCE: burn arithmetic for the model.**
  - Q3 operating cash use was $7.8M. A further 30% cut gives about $5.5M in the June quarter.
  - Obligations on top of that: $1.32M note coupon on 15 Jun 2024 (cash or shares) and $0.7M to finish paused construction.
  - At $15.2M of cash on 31 Mar, with no raise and no adverse judgment, cash lasts to about Q3 FY25 (Jan–Mar 2025), which matches management's statement.
  - CHIPS ITC of $2.8–4.0M "over the next nine to 12 months" (May 2024–May 2025). Timing of receipt is uncertain; it depends on a tax-return or elective-pay refund. Part may land inside the 10 Nov 2024 horizon, but no source dates it.

### 4.5 Registration and insurance odds and ends [PRE]

- No insurance covering the Qorvo litigation is disclosed (kit RESEARCH.md R7, which I did not re-verify).
- The 13 May S-3 cover shows "Non-accelerated filer þ Smaller reporting company þ".

---

## 5. Summary for the modeller (what was knowable on 14 May 2024)

| Item | Value known on 14 May | Best source | Tag |
|---|---|---|---|
| Qorvo's itemised claim | TS unjust enrichment "at least $66.1 million" (55-month head start); patent $279,808; corrective advertising $1,146,604; poaching $809,772; UDTPA and conspiracy $66.1M each (the same money under alternative theories); plus enhanced, exemplary, punitive and treble damages, fees and interest, all unquantified | D.I. 543-1 Ex. A.2 (22 Apr 2024); D.I. 476 (23 Feb 2024) | PRE |
| Exemplary cap proposed by Qorvo | ≤3× trade-secret damages | D.I. 535 (12 Apr 2024) | PRE |
| UDTPA trebling | Removed from the jury: "no reasonable basis to find compensable damages under the UDTPA" | D.I. 590 (14 May 2024) | PRE (same day) |
| Akoustis's damages number | Not public. Counsel: an "eight-figure verdict" is possible if the jury adopts Qorvo's theories | 13 May call | PRE |
| Akoustis's disclosure | "in an unspecified amount"; no accrual | 10-Q 13 May | PRE |
| Trial start | 6 May 2024 (jury selection); openings by 7 May | Docket, 10-Q | PRE |
| Trial length | Parties proposed ≤25 hours per side; the entered order is sealed | D.I. 543 n.3; D.I. 549 | PRE |
| Expected verdict | "go to the jury before the end of the week ... verdict in the next several days", i.e. about 17–22 May | 13 May call | PRE |
| Actual verdict | 17 May; judgment 20 May | Docket | POST |
| Nasdaq notice | 24 Oct 2023 (8-K 27 Oct 2023); first deadline 22 Apr 2024 | 8-K; 10-Q | PRE |
| Nasdaq second period | Granted; expires **21 Oct 2024**; reverse split under consideration; price $0.60 on 1 May 2024 | 10-Q | PRE |
| Primary shelf | S-3 333-262540, effective 15 Feb 2022 (3-year life to about Feb 2025) | 424B5s of May 2022 and Jan 2024 | PRE |
| ATM | $50M Oppenheimer, Craig-Hallum and Roth agreement; $2.0M sold; **$48.0M remaining; re-activated with the 10-Q on 13 May 2024**; no sales obligation; practical capacity limited by price, volume and, if it applies, the I.B.6 baby-shelf cap (not stated before the cutoff) | 10-Q | PRE |
| S-3 of 13 May 2024 | **Resale** of 5,028,291 shares (5.0M note-interest and make-whole shares); no proceeds to the company | S-3 | PRE (effective 21 May = POST) |
| Cash and runway | $15.2M at 31 Mar; "into the third quarter of fiscal 2025"; going-concern doubt; adverse judgment → "urgent need for additional liquidity" or bankruptcy | 10-Q | PRE |
| Cost plan | Further 30% burn cut in the June quarter; opex $10–11M a quarter; >$20M annualized savings; breakeven within 9 months at $11–15M quarterly revenue | Call and release | PRE |
| CHIPS ITC | $2.8–4.0M over 9–12 months | Call | PRE |
| Debt service to 10 Nov 2024 | 6% notes: $1.32M coupon on 15 Jun 2024 (cash or shares). GDSI $4.0M note: 0% interest, secured; step-down to $1.3M on **1 Jan 2025**, balance on 1 Jan 2026. **No GDSI cash is due inside the horizon** unless accelerated (bankruptcy event default) | 10-Q; FY23 10-K | PRE |

## 6. Isolation log

- All sources used for the facts marked [PRE] carry a filing or publication date on or before 14 May 2024: 10-Q and S-3 of 13 May; 8-K and release of 13 May; the earnings call of 13 May (Insider Monkey copy published 14 May 08:21); D.I. 1, 133, 476, 496, 508, 471, 535, 543, 543-1, 545, 546, 553, 557, 587, 590; 424B5s of 2 May 2022 and 29 Jan 2024; 8-K of 27 Oct 2023; FY2023 10-K.
- D.I. 590 is dated 14 May, the cutoff day itself. It is flagged in 1(d).
- The docket capture (kit, dated 20 Jun 2024) was read **only for entries dated ≤ 14 May 2024** when stating pre-cutoff facts. Entries dated 15–17 May are cited only as [POST] outcome.
- [POST] items used only as reveal or context: verdict and judgment (17/20 May), Law360 17 May, Sheppard Mullin 21 May, Bloomberg Law 20 May, EFFECT 21 May, SEC UPLOAD and CORRESP 16/17 May, trial transcript figures ($50.3M, $305k), D.I. 611, 613 and 618.
- Downloaded working copies (not repository files): `/Users/owenwassmer/.hermes/profiles/connor/cache/scratch/8/` (10-Q, S-3, 424B5s, RECAP PDFs 1, 133, 471, 476, 496, 508, 535, 543, 543-1, 543-2, and the call-transcript HTML).

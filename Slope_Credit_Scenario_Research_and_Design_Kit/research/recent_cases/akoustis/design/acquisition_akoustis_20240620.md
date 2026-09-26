# Acquisition round: Akoustis v. Qorvo, D = 20 Jun 2024

Builder-side acquisition, 25 Sep 2026. It collects every public source dated on or before 20 Jun 2024 that bears on an item still unresolved at D. The source texts are in `sources_akoustis_20240620/`. Court PDFs are in `recap/`, named by docket entry number.

**Scope.** The spec's source list (`Slope_Model_Extensions_Spec.md` §1.1) covers court filings and loan terms. This round covers everything that could move a forecast:
- the counterparty's own statements;
- the governing contracts;
- the court documents that were public;
- the company's other communications;
- market facts that move cash or set off contract terms (no share price or volume);
- press coverage.

**Isolation.** Every source below is dated on or before D. Items found during the search but dated after D are listed at the end as reveal-only.

## 1. Unresolved at D, and what the public record says

### 1.1 Post-trial ruling (JMOL, new trial, remittitur)

| Evidence | Source, date |
|---|---|
| Akoustis's grounds are public even though its briefs are sealed. On damages: an inconsistent verdict form; the unjust-enrichment method of Qorvo's expert (Bennis) is unreliable; the expert relied on a speculative 55-month head start. On the patents: the simulations do not prove the claimed improvement across the whole pass band. | D.I. 613 (motion), 17 Jun |
| **Remittitur has a stated figure:** $305,000, the unjust-enrichment amount from Akoustis's own damages expert. | D.I. 613, 17 Jun |
| Trial transcript excerpts (10, 13 and 15 May) show the damages theory: the time value of revenue earned 55 months early. Also attached: the Sedona Conference commentary and Restatement §45 on trade-secret remedies, and Akoustis's proposed redline of the verdict form. | D.I. 616-1 to 616-4, 17 Jun |
| The judgment itemises $31,315,215 unjust enrichment, $7,000,000 exemplary and $279,808 patent damages. The jury found 37 trade secrets misappropriated, **willfully and maliciously**. | D.I. 602, 20 May |
| The briefing schedule is set by signed order: answering briefs 25 Jul, replies 8 Aug. No ruling can come before 8 Aug. | D.I. 605, 7 Jun |

### 1.2 Amount at risk: components beyond the $38.6M verdict

| Component | Public at D? | Evidence |
|---|---|---|
| Attorneys' fees | **Yes: $12,116,123.30 requested** | D.I. 618, 17 Jun. The basis is DTSA and NCTSPA fees for willful and malicious misappropriation, which the jury found. It also asks for fees under the unfair-trade-practices statute, contingent on the trebling motion. |
| **Trebling under the North Carolina unfair-trade-practices statute (UDTPA)** | **Yes: Qorvo asks to amend the judgment to $93,945,645 in compensatory damages** | D.I. 611, 17 Jun. The jury answered "No" on the UDTPA question; Qorvo argues that a trade-secret violation is a UDTPA violation as a matter of law, and that trebling is automatic. That is **+$62.6M above the verdict**. The spec does not list this component. |
| Pre- and post-judgment interest | The motion is sealed (D.I. 615), but **the amount is bounded by public law** | North Carolina's legal rate is 8% (N.C.G.S. §24-1). In a non-contract action, compensatory damages bear interest from the date the action was commenced (§24-5(b)); the action was filed 4 Oct 2021. So $31,315,215 × 8% × 959/365 days to the 20 May judgment ≈ **$6.58M** on the trade-secret damages. Patent prejudgment interest on $279,808 is small. Post-judgment interest runs at the federal rate under 28 U.S.C. §1961: the weekly one-year Treasury yield for the week before judgment, about 5.1% in May 2024 (look up the exact figure). Exemplary damages normally carry no prejudgment interest under §24-5(b). |
| Enhanced patent damages; ongoing royalty | Intended, per the schedule order | D.I. 605 lists "a motion for enhanced damages" and "a permanent injunction and/or an ongoing royalty". No separately docketed enhanced-damages motion appears among D.I. 603–621, which is evidence Qorvo did not file one. |

### 1.3 Injunction and its effect on sales and costs

| Evidence | Source, date |
|---|---|
| **The proposed injunction's terms are public.** A worldwide ban on using Qorvo trade-secret information, including BAW designs and **trimming methods**, and on selling any product made with it. A US ban on 19 listed part numbers. Redesigns must be certified within 14 days with documentary proof. | D.I. 608-1, 17 Jun |
| **Cash costs in the proposed order:** a Qorvo-approved e-discovery vendor at Akoustis's expense to find and quarantine Qorvo material within 60 days; audits by a Qorvo-chosen third party for five years, up to twice a year, costs split 50/50, or 100% on Akoustis if a violation is found. | D.I. 608-1 |
| Akoustis says design updates remove the patented features and have been rolled out to all products still in production. It says it is "well-prepared to move forward notwithstanding any injunction" and expects no effect on marketing. | 8-K and press release, 22 May |
| Qorvo: the verdict "enables Qorvo to pursue an injunction against Akoustis products". | Qorvo 8-K Ex. 99.1, 20 May |

### 1.4 Funding: can the borrower raise cash, and at what cost?

| Evidence | Source, date |
|---|---|
| Cash $15.2M at 31 Mar 2024. Management says cash lasts "into the third quarter of fiscal 2025" (Jan–Mar 2025). This was written before the verdict. Going-concern doubt; an adverse verdict "would reduce the amount of time". | 10-Q, 13 May |
| After the verdict: without new financing the company "will be required to curtail or cease operations and/or seek protection under applicable bankruptcy laws". | 8-K, 20 May |
| **ATM program suspended** effective 22 May. | 8-K, 22 May |
| Registered direct offering: 50M shares and pre-funded warrants at **$0.20**, about $10M gross, 6% placement fee; closed 24 May. The company agreed not to issue securities for 45 days after closing (to about 8 Jul), and officers and directors are locked up for the same period. Roth has a six-month right of first refusal. | 8-K and SPA, 24 May; 424B5, 23 May |
| **Only about 3.0M authorized, unissued and unreserved shares remain** after the offering. Without a shareholder vote to increase them, the company is "limited to non-equity financing structures", and the notes "impose certain limitations on our ability to incur additional debt obligations". | 424B5 risk factors, 23 May |
| Management's cost plan: a further 30% cut in operating cash burn targeted for the June quarter; operating expenses of $10–11M a quarter; cash-flow breakeven within about nine months at $11–15M of quarterly revenue. | Earnings call, 13 May (transcript public) |
| **CHIPS Act refundable tax credits of $2.8–4M expected over the next 9–12 months.** This is a receipt within the horizon. | Earnings call, 13 May |

### 1.5 The convertible notes: default, repurchase and interest

| Evidence | Source, date |
|---|---|
| Judgment default: a **final** judgment over $10.0M, excluding insured amounts, undischarged, unpaid or unstayed for 60 days. It needs **notice from the trustee or holders of 25%**. | Indenture §7.01(i), June 2022 8-K Ex. 4.1 |
| Cross-default: an accelerated or payment-defaulted debt of $2.5M or more. | Indenture §7.01(h) |
| **Delisting is a Fundamental Change**, which gives holders the right to require repurchase. It is not listed among the §7.01 events of default. Failure to repurchase, or to give the Fundamental Change notice on time, is. | Indenture, "Fundamental Change" (d); §7.01(f) |
| **Interest of $1.32M is due 15 Dec 2024,** inside the horizon. It is payable in cash or shares at the company's election, but the share route is constrained by the authorized-share shortfall above. The 15 Jun 2024 payment fell five days before D. The 21 May prospectus registers 5,000,000 shares for interest and make-whole payments, which suggests shares; how it was paid is unverified. | Indenture; 424B3, 21 May |
| A $4.0M secured GDSI seller note, interest-free, due January 2026; partial prepayment down to $1.3M is due on the second anniversary, January 2025, just after the horizon. | 10-Q, 13 May |

### 1.6 Nasdaq listing

| Evidence | Source, date |
|---|---|
| Bid-price deficiency notice 24 Oct 2023. A second 180-day period was granted to **21 Oct 2024**. The company is considering a reverse split. | 8-K, 27 Oct 2023; 424B5, 23 May |
| Consequences stated by the company: loss of listing "would materially impair our ability to raise capital". For the notes, delisting is a Fundamental Change (§1.5). | 424B5 |

### 1.7 Settlement and leverage

| Evidence | Source, date |
|---|---|
| **Akoustis's own suit against Qorvo:** *Akoustis v. Qorvo*, E.D. Tex. 2:23-cv-00180 (Cornell '360 patent, exclusively licensed). It is at claim construction; a motion to add Cornell as co-plaintiff is pending. | 10-Q, 13 May; CourtListener docket 67232852 |
| Two **inter partes review petitions** against the '360 patent: one by a Qorvo wafer supplier on 1 Mar 2024, one by Qorvo on 17 Apr 2024. Institution decisions normally come about six months after filing, so September–October 2024, inside the horizon. | 10-Q, 13 May; earnings call, 13 May |
| A separate subpoena dispute: IQE KC (a Qorvo wafer supplier) moved to quash Akoustis's subpoena (D. Mass., transferred to E.D. Tex. on 22 May). | CourtListener docket 68555191 |
| Neither party discloses any settlement discussion. Akoustis says it will "vigorously contest the verdict". | 8-K, 20 May |

### 1.8 Operations, customers and receipts (the bank feed and the loan draws)

| Evidence | Source, date |
|---|---|
| Q3 FY24 revenue $7.5M. Guidance for the June quarter: flat to down 5%. A major Wi-Fi 6E carrier program ended abruptly. | Earnings call, 13 May |
| Customer concentration: two customers above 10% of revenue; the top two are Asia-based and together make up 36% of sales. The top ten make up 64%. | Earnings call, 13 May |
| Nine Wi-Fi 7 design wins tracked; production ramps in the second half of calendar 2024. Orders are already received for September-quarter production. | Earnings call, 13 May; press releases, 8 Feb, 12 Feb and 1 May |
| Supplier-invoice categories: wafers and substrates, process gases, metals, and Tai-Saw contract manufacturing for RFMi. Also an $8.1M goodwill impairment in Q3. | 10-Q, 13 May |
| Litigation spend: G&A was flat, but professional fees and property tax rose $1.9M year on year in Q3. | 10-Q, 13 May |

### 1.9 Governance and other signals

- A director resigned on 2 Jun "not involv[ing] any disagreement" (8-K, 5 Jun).
- The SEC staff corresponded with the company on the May S-3 (UPLOAD 16 May; CORRESP 17 May).
- There were no Form 4 insider trades from April to 20 June.
- The conflict-minerals report was filed 31 May. It was downloaded but not read for supplier names.

### 1.10 Press coverage before D

- Law360, 17 May: "Qorvo Wins $38.6M In Akoustis Trade Secrets And Patent Trial" (paywalled; headline public).
- Bloomberg Law, 20 May: "Akoustis Floats Bankruptcy on $39 Million Qorvo Verdict Loss".
- Semiconductor Today, 21 May: Qorvo's statement.
- Benzinga, 22 May: coverage of the offering day.

These restate the filings. They add no new facts.

## 2. What remains truly unavailable at D

- The briefs behind every post-trial motion (D.I. 607, 609, 610, 612, 614, 615, 617, 619–621) are sealed at D. Their grounds are stated in the public motions (§1.1–1.2).
- The exact amount of prejudgment interest Qorvo asked for (D.I. 615, sealed). It is bounded by statute (§1.2).
- The jury verdict form (D.I. 601) is public but image-only. It needs OCR, which was not done; D.I. 602 carries its numbers.
- Most E.D. Tex. filings are not free on RECAP.
- Akoustis's 30 June balance. The spec's decision D1 still applies. The pre-D evidence for the April–June quarter is the 13 May guidance: a 30% burn cut, revenue flat to down 5%, the $9.3M net offering, and CHIPS Act credits.

## 3. Market facts (approved: facts that move cash or set off contract terms; no price or volume)

- The $0.20 offering price and its roughly $9.3M net proceeds (a cash event).
- The ATM suspension, the 45-day issuance lock-up, and the six-month right of first refusal.
- The authorized-share shortfall (about 3.0M shares) and the shareholder vote needed to lift it.
- The Nasdaq bid-price deadline of 21 Oct 2024, and delisting as a Fundamental Change under the notes.
- Interest on the notes payable in cash or shares.

The 424B3 of 21 May quotes a last sale price of $0.1377. It is incidental text in a filing and is not recorded as a market input.

## 4. Found in the search but dated after D (reveal only)

These must not enter the snapshot:
- press releases of 27 Jun ($2M Wi-Fi 7 orders), 9 Jul ($8M), 8 Aug (leadership transition) and 13 Aug ($13M);
- D.I. 622–624 and every later docket entry;
- the 8-Ks of October and November 2024;
- the Chapter 11 materials.

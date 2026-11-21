"""Rebuild compact case indexes from already-acquired primary documents.

Run from the research-kit root. Does not download or change source documents.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'research/revision_v2/data'
LEAD_CUTOFF = '2024-08-13'

def source(sid, filename, url, date, events, basis, index_url=None, accepted=None):
    p = ROOT / 'research/recent_cases/synergy_chc' / filename
    return {
        'source_id': sid, 'case_id': 'synergy_chc_2024', 'primary_url': url,
        'original_relative_path': p.relative_to(ROOT).as_posix(),
        'package_relative_path': p.relative_to(ROOT).as_posix(),
        'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size,
        'availability': {'date': date, 'precision': 'day' if date else 'unknown',
                         'basis': basis, 'verification_url': index_url or url,
                         'accepted_at_as_displayed': accepted,
                         'intraday_runtime_policy': 'Conservative end of stated date in America/New_York; do not invent an acceptance time.'},
        'event_dates': events,
        'relative_to_lead_cutoff': 'eligible' if date and date <= LEAD_CUTOFF else 'outcome' if date else 'unknown',
        'mission_membership': {'synergy_20240813': 'eligible' if date and date <= LEAD_CUTOFF else 'outcome' if date else 'unknown',
                               'barfresh_20241025': 'out_of_case'},
    }

base='https://www.sec.gov/Archives/edgar/data/1562733/'
sources = [
 source('synergy_s1a_20240813','2024_s1a.html',base+'000121390024068424/ea0208324-04.htm','2024-08-13',
        [{'type':'interim_measurement','date':'2024-06-30'}, {'type':'updated_cash_measurement','date':'2024-08-12'}, {'type':'employee_measurement','date':'2024-08-07'}],
        'Cover expressly says filed August 13, 2024. Intraday SEC index retrieval failed; date is verified from document.'),
 source('synergy_merchant_agreement_20240501','2024_05_01_webbank_shopify_loan.html',base+'000121390024056991/ea020832401ex10-32_synergy.htm','2024-06-28',
        [{'type':'agreement_date','date':'2024-05-01'},{'type':'effective_funding_date','date':None,'precision':'unknown'}],
        'SEC filing index lists agreement as Exhibit 10.32 within June 28, 2024 S-1.',base+'000121390024056991/0001213900-24-056991-index.htm','2024-06-28 11:32:30'),
 source('synergy_court_20230227','2023_02_27_court_order.pdf','https://www.govinfo.gov/content/pkg/USCOURTS-med-2_22-cv-00301/pdf/USCOURTS-med-2_22-cv-00301-0.pdf','2023-02-27',
        [{'type':'court_filing','date':'2023-02-27'},{'type':'alleged_operational_events','date_range':['2020','2021'],'precision':'year'}],
        'Public court document 31 bears Filed 02/27/23 on page 1. This is court filing availability; GovInfo upload date is not established.'),
 source('synergy_annual_2024','2024_annual_report.html',base+'000121390025026254/ea0235758-10k_synergy.htm','2025-03-31',
        [{'type':'financial_year_end','date':'2024-12-31'}],
        'SEC filing index.',base+'000121390025026254/0001213900-25-026254-index.htm','2025-03-31 16:10:25'),
 source('synergy_credit_agreement_20250530','2025_05_30_credit_agreement.html',base+'000121390025050984/ea024464201ex10-1_synergy.htm','2025-06-04',
        [{'type':'agreement_date','date':'2025-05-30'}],
        'SEC June 4, 2025 8-K index identifies agreement as Exhibit 10.1.',base+'000121390025050984/0001213900-25-050984-index.htm','2025-06-04 08:37:50'),
 source('synergy_quarterly_20250630','2025_06_30_quarterly_report.html',base+'000121390025076060/ea0252562-10q_synergy.htm','2025-08-14',
        [{'type':'quarter_end','date':'2025-06-30'}],
        'SEC filing index.',base+'000121390025076060/0001213900-25-076060-index.htm','2025-08-14 08:05:51'),
]

periods={'brfh_2023_10k':'2023-12-31','brfh_2024q1_10q':'2024-03-31','brfh_2024q2_10q':'2024-06-30',
         'brfh_2024q2_release':'2024-06-30','brfh_2024q3_10q':'2024-09-30','brfh_2024q3_release':'2024-09-30',
         'brfh_2024_10k':'2024-12-31','brfh_2025q1_10q':'2025-03-31'}
for x in json.loads((ROOT/'research/barfresh_case/acquired_sources.json').read_text()):
    if not x['id'].startswith('brfh_'): continue
    p=ROOT/'research/barfresh_case'/Path(x['path']).name
    date=x.get('publication_date'); metadata=x.get('role')=='metadata'
    sources.append({
        'source_id':x['id'],'case_id':'barfresh_schreiber_2024','primary_url':x['url'],
        'original_relative_path':p.relative_to(ROOT).as_posix(),'package_relative_path':p.relative_to(ROOT).as_posix(),
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,
        'availability':{'date':date,'precision':'second' if x.get('accepted_at') else 'day' if date else 'unknown',
                        'accepted_at':x.get('accepted_at'), 'basis':x.get('date_verification','Issuer release dated in URL and document.'),
                        'verification_metadata':'research/barfresh_case/brfh_sec_submissions.json' if 'SEC submissions' in x.get('date_verification','') else None},
        'event_dates':[{'type':'financial_period_end','date':periods[x['id']]}] if x['id'] in periods else
                      [{'type':'financing_announcement','date':'2024-08-13'}] if 'litigation_funding' in x['id'] else [],
        'relative_to_lead_cutoff':'metadata_only' if metadata else 'eligible' if date and date<=LEAD_CUTOFF else 'outcome' if date else 'unknown',
        'mission_membership':{'synergy_20240813':'out_of_case','barfresh_20240815':'metadata_only' if metadata else 'eligible' if date and date<='2024-08-15' else 'outcome' if date else 'unknown',
                              'barfresh_20241025':'metadata_only' if metadata else 'eligible' if date and date<='2024-10-25' else 'outcome' if date else 'unknown'},
    })

index={
 'schema_version':'1.0','case_selection':{'lead':'synergy_chc_2024','transfer':'barfresh_schreiber_2024'},
 'missions':{'synergy_20240813':{'cutoff':'2024-08-13T23:59:59-04:00','financial_measurement':'2024-06-30','latest_cash_measurement':'2024-08-12'},
             'barfresh_20240815':{'cutoff':'2024-08-15T23:59:59-04:00'},
             'barfresh_20241025':{'cutoff':'2024-10-25T23:59:59-04:00','financial_measurement':'2024-09-30'}},
 'rules':['Event, agreement, measurement and public-availability dates are different fields.',
          'Outcome is a relationship to a mission cutoff, never a permanent property of a document.',
          'Other-case records are excluded regardless of chronological eligibility.',
          'Indexes and metadata must not expose later filing contents to a historical agent.',
          'Original and package relative paths are relative to the research-kit root; preserve this directory layout.'],
 'sources':sources}
(OUT/'sources.json').write_text(json.dumps(index,indent=2)+'\n')

facts=[]
def fact(fid,value,unit,source_id,section,anchor,measurement=None,notes=None,precision='exact',kind='disclosed'):
    facts.append({'fact_id':fid,'value':value,'unit':unit,'kind':kind,'value_precision':precision,
                  'measurement_date':measurement,'source_id':source_id,
                  'locator':{'section':section,'anchor':anchor},'notes':notes or []})
S='synergy_s1a_20240813'; M='synergy_merchant_agreement_20240501'
cashnotes=['June 30 financial statement measurement; do not use as fresh August cash.']
fact('synergy_cash_20240630_unrestricted',87293,'USD',S,'Condensed consolidated balance sheets, F-2; Liquidity and Capital Resources','Cash and cash equivalents','2024-06-30',cashnotes)
fact('synergy_cash_20240630_restricted',100000,'USD',S,'Condensed consolidated balance sheets, F-2; Liquidity and Capital Resources','Restricted cash','2024-06-30',['Credit-card collateral, excluded from freely available cash.'])
fact('synergy_cash_20240812_approx',2000000,'USD',S,'Prospectus summary and Liquidity and Capital Resources','As of August 12, 2024','2024-08-12',['Management reports approximate cash; restricted/unrestricted composition is not reconciled here.','Bridge settlement and other debt payments since June 30 before forecasting from this cash observation.'],precision='approximate')
for suffix,value,anchor in [('receivables',3268086,'Accounts receivable, net'),('inventory',1920290,'Inventory, net'),('related_party_receivable',4424547,'Loan receivable (related party)'),('equity',-25878273,"Total stockholders’ deficit"),('current_assets',10873698,'Total Current Assets'),('current_liabilities',14711896,'Total Current Liabilities')]:
    fact('synergy_'+suffix+'_20240630',value,'USD',S,'Condensed consolidated balance sheets, F-2',anchor,'2024-06-30')
fact('synergy_debt_carrying_20240630',31458899,'USD',S,'Note 11, Notes Payable, summary table','31,458,899','2024-06-30',['Carrying amount includes accounting discounts; not interchangeable with gross contractual cash owed.','Includes settlement obligations; do not add them again to balance-sheet debt.'])
fact('synergy_revenue_2023',42777633,'USD',S,'Consolidated statements of operations, F-31','Revenue','2023-12-31')
fact('synergy_revenue_h1_2024',17436703,'USD',S,'Condensed consolidated statements of income, F-3','Revenue','2024-06-30')
fact('synergy_operating_income_h1_2024',3391921,'USD',S,'Condensed consolidated statements of income, F-3','Income from operations','2024-06-30')
fact('synergy_ebitda_h1_2024',3463701,'USD',S,'Summary financial data; Non-GAAP financial measures','EBITDA','2024-06-30',['Non-GAAP EBITDA is not cash available for debt service.'])
fact('synergy_operating_cashflow_h1_2024',-1140005,'USD',S,'Condensed consolidated statements of cash flows, F-5','Net cash used by operating activities','2024-06-30',['Includes working-capital movements; do not extrapolate as constant recurring burn.'])
fact('synergy_employee_count',25,'people',S,'Human Capital Management','25 full','2024-08-07')
fact('synergy_hvl_lawsuit_identity','Synergy CHC Corp. v. HVL, LLC d/b/a Atrium Innovations, 2:22-cv-00301-JAW','text',S,'Note 13, Commitments and Contingencies, Litigation','Atrium Innovations','2024-06-30',['Note 13 explicitly links this lawsuit to the December 2023 settlement loan in Note 11.'])
fact('synergy_hvl_settlement_date','2023-12-28','date',S,'Note 11, $5,450,000 December 28, 2023 Loan','confidential settlement agreement','2023-12-28')
fact('synergy_hvl_2023_noncash_gain',2235986,'USD',S,'Note 11, December 28, 2023 Loan; Note 13, Litigation','reduction of cost of sales','2023-12-31',['Accounting gain from settlement, not cash received.','Normalize FY2023 historical profitability if used; do not remove from H1 2024 income where it is not included.'])
fact('synergy_hvl_balance_20240630',4802445,'USD',S,'Note 11, December 28, 2023 Loan','outstanding loan balance at both June','2024-06-30',['Includes 352445 interest; do not add that amount twice.'])
fact('synergy_hvl_interest_included',352445,'USD',S,'Note 11, December 28, 2023 Loan','including interest','2024-06-30')
fact('synergy_hvl_stated_interest_rate',0.05,'fraction_per_year',S,'Note 11, December 28, 2023 Loan','5% per annum','2024-06-30',['Source says interest payable with last payment; exact settlement contract not acquired.'])
bridge=['Amount scheduled for H2 2024 as measured June 30; NOT verified still unpaid on August 13.','Subtract actual July 1–August 12 settlement payments in a bridge before using August 12 cash.','Exact installment dates unknown; choose explicit timing scenarios, not invented historical dates.']
fact('synergy_hvl_h2_2024_payment',2000000,'USD',S,'Note 11, December 28, 2023 Loan, future payment table','2024','2024-06-30',bridge)
fact('synergy_hvl_2025_payment',2000000,'USD',S,'Note 11, December 28, 2023 Loan, future payment table','2025','2024-06-30')
fact('synergy_hvl_2026_payment',802445,'USD',S,'Note 11, December 28, 2023 Loan, future payment table','2026','2024-06-30')
supplier_identity=['The March 27, 2024 Loan note describes the counterparty only as "a supplier".',
                   'The June 30 notes-payable table labels the same $2,920,824 balance "VitBest" (fact synergy_supplier_march_table_label_20240630). Linking that label to this settlement is an inference from the matching balance; report it as a cited inference, not as a stated fact.',
                   'The full legal entity (Vitabest Nutrition, Inc.), the named settlement agreement and every other fact from the May 2025 credit agreement are outcome evidence; do not backfill them into this run.']
fact('synergy_supplier_march_settlement_date','2024-03-27','date',S,'Note 11, $3,020,824 March 27, 2024 Loan','March 27, 2024 Loan','2024-03-27',supplier_identity+['A settlement does not independently prove a filed lawsuit.'])
fact('synergy_supplier_march_balance_20240630',2920824,'USD',S,'Note 11, March 27, 2024 Loan','outstanding loan balance','2024-06-30')
fact('synergy_supplier_march_table_label_20240630','VitBest','text',S,'Note 11, Notes Payable, summary table (June 30, 2024 and December 31, 2023)','VitBest','2024-06-30',
     ['Row label as printed in the decision-date filing: $2,920,824 at June 30, 2024 and none at December 31, 2023.',
      'The table does not say which note the row belongs to. Its balance equals the March 27, 2024 settlement loan balance, so the link is an inference from matching amounts that must cite both passages.',
      'The label is not a verified legal-entity name. The full legal entity appears only in 2025 outcome evidence.'])
fact('synergy_supplier_march_paid_h1_2024',100000,'USD',S,'Note 11, March 27, 2024 Loan','payments of $100,000','2024-06-30')
fact('synergy_supplier_march_h2_2024_payment',600000,'USD',S,'Note 11, March 27, 2024 Loan, future payment table','2024','2024-06-30',bridge)
fact('synergy_supplier_march_2025_payment',1460412,'USD',S,'Note 11, March 27, 2024 Loan, future payment table','2025','2024-06-30')
fact('synergy_supplier_march_2026_payment',860412,'USD',S,'Note 11, March 27, 2024 Loan, future payment table','2026','2024-06-30')
fact('synergy_lodc_paid_may_2024',True,'boolean',S,'Note 13, Litigation, L.O.D.C. Group paragraph','During May 2024','2024-05',['Distinct Texas lawsuit 4:23-cv-691.','Settlement amount is undisclosed; allegation over $1m is not settlement amount.','No incremental future payment for this settled obligation in the August run.'],precision='month')
for suffix,value,unit,section,anchor in [
 ('advance',370000,'USD','Cover; section 2.3','Loan Amount'),('total_repayment',418100,'USD','Cover; section 1','Total Payment Amount'),
 ('cost_of_funds',48100,'USD','Cover','Cost of Funds'),('receipts_fraction',0.25,'fraction','Section 1, Daily Payment and Daily Payment Percentage','Daily Payment Percentage'),
 ('term_months',18,'months','Section 1, Term','eighteen (18)'),('6m_min_fraction',0.30,'fraction_of_total_repayment','Section 1, Minimum Payment; section 4.1.1','thirty (30)'),
 ('12m_min_fraction',0.60,'fraction_of_total_repayment','Section 4.1.1; section 4.2.1(b)','at least 60 percent'),
 ('second_window_min_fraction',0.30,'fraction_of_total_repayment','Section 4.1.1; section 4.2.1(b)','beginning of Month seven')]:
    fact('synergy_webbank_'+suffix,value,unit,M,section,anchor,'2024-05-01',
         ['Agreement date is not confirmed effective funding date.','Daily percentage applies to defined Shopify Account Credits, not consolidated sales.'])
    if suffix == '12m_min_fraction':
        facts[-1]['notes'].extend(['The 60% cumulative amount is a necessary floor, not the complete second-period minimum rule.',
                                  'Section 4.2.1(b) measures the second Minimum Payment against Month 7–12 Daily Payments. Excess paid during Months 1–6 does not offset that period minimum unless the entire loan is paid off.'])
    if suffix == 'second_window_min_fraction':
        facts[-1]['notes'].append('Track a separate Month 7–12 payment ledger. The second six-month period requires an additional 30% of Total Payment Amount; cap obligations by the outstanding total amount and stop after full payoff.')
fact('synergy_webbank_lender','WebBank','text',M,'Opening paragraph','Utah-chartered industrial bank','2024-05-01')
fact('synergy_webbank_effective_date_rule','date lender delivers funding','text',M,'Section 1, Effective Date; section 2.3','Effective Date','2024-05-01')
fact('synergy_webbank_gross_receipts_rule','Apply the percentage to defined gross Shopify Account Credits; refunds, returns and cancellations do not reduce Daily Payments.','text',M,'Section 1, Shopify Account Credits; section 4.1.1','gross sales','2024-05-01',['This account-specific contract definition is not equivalent to net sales or consolidated company revenue.'])
fact('synergy_webbank_minimum_payment_ledger_rule','Maintain separate Month 1–6 and Month 7–12 minimum-payment ledgers. The second period resets its Daily Payment measurement; prior-period excess does not offset its 30% requirement unless the total loan is already paid in full.','text',M,'Section 4.1.1; section 4.2.1(b)','beginning of Month seven','2024-05-01',['A cumulative-only 30%/60%/100% implementation is incomplete.','Cap any collection by the remaining Total Payment Amount; full repayment extinguishes further payment obligations.'])
fact('synergy_webbank_nonbusiness_payment_rule','Amounts due on non-business days transfer on the following business day.','text',M,'Section 4.2.1(a)','non-Business Day','2024-05-01')
fact('synergy_webbank_voluntary_payment_rule','Manual payments reduce outstanding total payment amount; no assumed pro-rata fee rebate.','text',M,'Section 4.1.2','Manual Payments','2024-05-01')
fact('synergy_webbank_carrying_balance_20240630',333454,'USD',S,'Note 11, $418,100 May 1, 2024 Loan','outstanding loan balance','2024-06-30',['Accounting balance is not the verified remaining contractual total payment amount.','Actual remittances through August 13 require borrower/servicer ledger.'])
fact('synergy_other_shopify_may22_advance',105000,'USD',S,'Note 11, $118,650 May 22, 2024 Loan','received $105,000','2024-05-22',['Separate obligation; do not sum two 25% remittance rates without mapping the accounts/stores.'])
fact('synergy_other_shopify_may22_total_repayment',118650,'USD',S,'Note 11, $118,650 May 22, 2024 Loan','total payments','2024-05-22')
fact('synergy_other_shopify_may22_carrying_balance_20240630',93736,'USD',S,'Note 11, $118,650 May 22, 2024 Loan','outstanding loan balance','2024-06-30')
fact('synergy_shopify_january_loan_repaid_20240630',True,'boolean',S,'Note 11, $141,250 January 29, 2024 Loan','outstanding loan balance at June','2024-06-30',['Note narrative says January 21 but heading says January 29; do not infer a new loan from this typo.'])

unknowns=[
 {'unknown_id':'synergy_hvl_paid_20240701_20240812','unit':'USD','required_for':'Bridge June 30 settlement liability to August decision date.','allocation_bounds_for_2024_bucket_only':[0,2000000],'resolution':'Borrower bank transactions and settlement payment ledger.','rule':'Do not hardcode zero historical payments. Any prepayment of later-year obligations needs a separate bridge; the bucket allocation cap is not a cap on possible actual payments.'},
 {'unknown_id':'synergy_supplier_march_paid_20240701_20240812','unit':'USD','required_for':'Bridge June 30 settlement liability to August decision date.','allocation_bounds_for_2024_bucket_only':[0,600000],'resolution':'Borrower bank transactions and settlement payment ledger.','rule':'No additional August outflow for payments already reflected in August cash. Any prepayment of later-year obligations needs a separate bridge.'},
 {'unknown_id':'synergy_exact_settlement_installment_dates','required_for':'Payment calendar within disclosed annual buckets.','resolution':'Executed settlement agreements or borrower schedules.','rule':'Show conditional timing scenarios until obtained.'},
 {'unknown_id':'synergy_august_cash_composition','required_for':'Unrestricted cash available at August 13 decision.','resolution':'Latest bank balances and restriction documentation.','rule':'Approximately $2m cash is not a verified $2m unrestricted balance.'},
 {'unknown_id':'synergy_shopify_account_credits_daily','required_for':'Actual daily merchant loan remittances.','resolution':'Eligible store/account transaction history.','rule':'Do not substitute consolidated revenue or brand revenue.'},
 {'unknown_id':'synergy_shopify_effective_funding_date','required_for':'Calendar dates of 6-, 12- and 18-month milestones.','resolution':'Funding receipt/servicer record.','rule':'May 1 agreement date is not automatically the funding date.'},
 {'unknown_id':'synergy_shopify_remittances_to_cutoff','required_for':'Remaining contractual repayment balance and milestone headroom.','resolution':'Servicer ledger plus bank reconciliation.','rule':'Do not treat accounting carrying balance as remaining gross contractual repayment.'},
 {'unknown_id':'synergy_shopify_account_mapping','required_for':'Interaction of May 1 and May 22 loans.','resolution':'Account/store IDs and servicing agreement.','rule':'Do not automatically apply 50% aggregate deduction to one receipts stream.'},
 {'unknown_id':'synergy_liens_and_new_borrowing_permissions','required_for':'Eligibility of incremental secured financing.','resolution':'Existing debt contracts, lien search and any waivers.','rule':'No invented first-lien status or available new debt permission.'},
 {'unknown_id':'synergy_full_existing_debt_calendar','required_for':'Complete borrower cash-availability model.','resolution':'Reconcile Note 11 to executed agreements and current borrower debt schedule.','rule':'Do not model settlement obligations alone while ignoring other existing payments.'},
 {'unknown_id':'synergy_supplier_march_identity_at_cutoff','required_for':'Confirmed legal identity of the March 2024 settlement counterparty, if relevant to the operating mechanism.','resolution':'Decision-date settlement agreement.','rule':'At the cutoff the evidence gives only the notes-payable label "VitBest" and its balance-matched link to the March settlement, which is an inference. The full legal entity and confirmation of the link come from 2025 documents and are outcome evidence only.'},
 {'unknown_id':'synergy_proposed_slope_offer','required_for':'Candidate advance comparisons.','resolution':'Explicit analyst-selected amount, term, price and policy parameters.','rule':'This is a modeled offer; no historical Slope application or relationship is established.'},
]
payload={'schema_version':'1.0','case_id':'synergy_chc_2024','mission_id':'synergy_20240813',
 'as_of':'2024-08-13T23:59:59-04:00','currency':'USD','source_index':'sources.json',
 'scope':'Decision-date factual anchors and unresolved inputs, not a full historical transaction ledger or live lender underwriting output.',
 'facts':facts,'known_unknowns':unknowns,
 'modeling_rules':['Record legal settlements as payment schedules for existing liabilities, not new duplicate liabilities.',
                   'Merchant-loan 30%/60%/100% cumulative milestones alone do not implement the contract: the second six-month period has an additional 30% minimum measured against its own period payments, subject to remaining total repayment and full payoff.',
                   'Use defined gross Shopify Account Credits without deducting refunds, returns or cancellations; never substitute consolidated revenue.',
                   'June 30 amounts and August 12 cash require a bridge; $2.6m cannot be called verified remaining August debt service.',
                   'Treat a settlement gain as an accounting normalization, not a cash inflow.',
                   'Conditional scenario probabilities and candidate-loan economics are assumptions unless separately supported.',
                   'No 2025 refinancing proceeds are committed August 2024 funding.',
                   'The February 2023 court document is a recommended decision on a motion to dismiss, not a merits judgment.'],
 'outcome_source_ids':['synergy_annual_2024','synergy_credit_agreement_20250530','synergy_quarterly_20250630']}
(OUT/'facts_synergy.json').write_text(json.dumps(payload,indent=2)+'\n')
outcomes={'case_id':'synergy_chc_2024','runtime_use':'Evaluator only after decision is locked; excluded from August 13 agent context.',
          'conflicts_to_preserve':[{'subject':'May 1 loan December 31, 2024 carrying balance','source_values':[{'source_id':'synergy_annual_2024','value':269488},{'source_id':'synergy_quarterly_20250630','value':280732}],'unit':'USD','locator':'Note 11, May 1, 2024 Loan','rule':'Do not silently reconcile or infer exact payment timing/default from this conflict.'}]}
(OUT/'outcome_checks_synergy.json').write_text(json.dumps(outcomes,indent=2)+'\n')
print(json.dumps({'sources':len(sources),'facts':len(facts),'known_unknowns':len(unknowns),'output_files':['sources.json','facts_synergy.json']}))

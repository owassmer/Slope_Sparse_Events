"""Acquire the Akoustis / Qorvo case sources and register them in the kit catalog.

Run from the repository root: `uv run python Slope_Credit_Scenario_Research_and_Design_Kit/research/recent_cases/akoustis/acquire.py`.
SEC filings come from EDGAR (acceptance time from the submissions JSON); court documents are the free RECAP copies on
CourtListener for Qorvo v. Akoustis, D. Del. 1:21-cv-01417 (availability = the docket entry's filing date, day
precision, end of day in New York). The docket report is rebuilt from CourtListener's docket page, keeping only
entries filed and entered on or before 20 Jun 2024 and numbered at most 621 (a later-docketed artifact, D.I. 730,
carries an earlier date and is dropped). The committed copy was assembled in the same format from CourtListener
captures of that docket taken on 25 Sep 2026, because the page sat behind a bot challenge on 26 Sep; those captures
lack D.I. 535-537 and 588-589, which a re-fetch (delete the file and re-run) fills.

Left out:
- D.I. 601 (the jury verdict form): the RECAP copy is image-only, so no text can be extracted. D.I. 602 carries its
  figures.
- Pace entries without a free RECAP copy (D.I. 224, 273, 398, 470, 488, 491, 492, 494): their dates and descriptions
  are in the docket report.
- The 13 May 2024 earnings-call transcript is not on EDGAR or RECAP (a Stage 3 gap).

Membership for the mission `akoustis_20240620` is `eligible` when public by the cutoff and `outcome` after it. Three
outcome sources are registered only so the isolation probes can be shown to be real later facts: the FY2024 10-K
(the 26 Jun 2024 customer note), the amended final judgment D.I. 717 (fees and pre-judgment interest) and the
15 May 2025 8-K (the asset sale). Re-running is idempotent: files already present are not fetched again.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CATALOG = KIT / "research/revision_v2/data/sources.json"
CASE_ID = "akoustis_qorvo_2024"
MISSION = "akoustis_20240620"
CUTOFF = "2024-06-20T23:59:59-04:00"
D = "2024-06-20"
UA = {"User-Agent": "Owen Wassmer owassmer1@gmail.com"}
AKTS, QRVO = "1584754", "1604778"
RECAP = "recap/gov.uscourts.ded.76727/gov.uscourts.ded.76727"
CASE = "Qorvo v. Akoustis (D. Del. 1:21-cv-01417)"
DOCKET_ID = "60622376"
DOCKET_SID = "ded_21cv1417_docket_20240620"
LAST_ENTRY = 621

SEC = [  # (source_id, cik, accession, document, accepted_at, kind, title, membership)
    ("akts_2022_06_notes_8k", AKTS, "0001213900-22-032130", "ea161358-8k_akoustistech.htm", "2022-06-10T09:18:27Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, June 2022 (6.0% convertible senior notes due 2027)", "eligible"),
    ("akts_2022_06_notes_indenture", AKTS, "0001213900-22-032130", "ea161358ex4-1_akoustistech.htm",
     "2022-06-10T09:18:27Z", "contract", "Indenture for the 6.0% convertible senior notes due 2027 (Form 8-K Exhibit 4.1, "
     "June 2022)", "eligible"),
    ("akts_fy2023_10k", AKTS, "0001213900-23-074139", "f10k2023_akoustistech.htm", "2023-09-06T07:31:03Z",
     "sec_annual_report", "Akoustis Technologies Form 10-K for fiscal year ended June 30, 2023", "eligible"),
    ("akts_2023_10_27_8k", AKTS, "0001013762-23-007328", "ea187341-8k_akoustis.htm", "2023-10-27T17:00:09Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, 27 October 2023 (Item 3.01, Nasdaq notice)", "eligible"),
    ("akts_2024_01_29_8k", AKTS, "0001213900-24-007401", "ea192187-8k_akoustis.htm", "2024-01-29T16:21:08Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, 29 January 2024 (underwritten offering)", "eligible"),
    ("akts_2024q3_10q", AKTS, "0001213900-24-041897", "ea0204504-10q_akoustis.htm", "2024-05-13T07:15:56Z",
     "sec_quarterly_report", "Akoustis Technologies Form 10-Q for the quarter ended March 31, 2024", "eligible"),
    ("akts_2024_05_13_8k", AKTS, "0001213900-24-041902", "ea0205822-8k_akoustis.htm", "2024-05-13T07:34:40Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, 13 May 2024 (Item 2.02)", "eligible"),
    ("akts_2024_05_13_release", AKTS, "0001213900-24-041902", "ea020582201ex99-1_akoustis.htm", "2024-05-13T07:34:40Z",
     "press_release", "Akoustis fiscal third quarter 2024 results press release (Form 8-K Exhibit 99.1)", "eligible"),
    ("akts_2024_05_17_corresp", AKTS, "0001213900-24-044681", "filename1.htm", "2024-05-17T15:05:14Z",
     "sec_correspondence", "Akoustis Technologies correspondence with the SEC staff, 17 May 2024", "eligible"),
    ("akts_2024_05_20_8k", AKTS, "0001213900-24-044913", "ea0206519-8k_akoustis.htm", "2024-05-20T08:00:55Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, 20 May 2024 (Item 8.01)", "eligible"),
    ("akts_2024_05_22_8k", AKTS, "0001213900-24-045977", "ea0206696-8k_akoustistech.htm", "2024-05-22T17:20:11Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, 22 May 2024 (Items 7.01, 8.01)", "eligible"),
    ("akts_2024_05_22_ex991", AKTS, "0001213900-24-045977", "ea020669601ex99-1_akoustis.htm", "2024-05-22T17:20:11Z",
     "press_release", "Akoustis press release, 22 May 2024 (Form 8-K Exhibit 99.1)", "eligible"),
    ("akts_2024_05_22_ex992", AKTS, "0001213900-24-045977", "ea020669601ex99-2_akoustis.htm", "2024-05-22T17:20:11Z",
     "press_release", "Akoustis press release, 22 May 2024 (Form 8-K Exhibit 99.2)", "eligible"),
    ("akts_2024_05_23_424b5", AKTS, "0001213900-24-046328", "ea0206753-424b5_akoustis.htm", "2024-05-23T17:12:53Z",
     "sec_prospectus", "Akoustis Technologies prospectus supplement (Form 424B5), 23 May 2024", "eligible"),
    ("akts_2024_05_23_424b3", AKTS, "0001213900-24-046345", "ea0206469-424b3_akoustis.htm", "2024-05-23T17:35:25Z",
     "sec_prospectus", "Akoustis Technologies prospectus (Form 424B3), filed 24 May 2024", "eligible"),
    ("akts_2024_05_24_8k", AKTS, "0001213900-24-046649", "ea0206845-8k_akoustis.htm", "2024-05-24T16:15:47Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, 24 May 2024 (Items 1.01, 9.01)", "eligible"),
    ("akts_2024_05_24_ex41", AKTS, "0001213900-24-046649", "ea020684501ex4-1_akoustis.htm", "2024-05-24T16:15:47Z",
     "contract", "Form of pre-funded warrant (Form 8-K Exhibit 4.1, 24 May 2024)", "eligible"),
    ("akts_2024_05_24_ex101", AKTS, "0001213900-24-046649", "ea020684501ex10-1_akoustis.htm", "2024-05-24T16:15:47Z",
     "contract", "Securities purchase agreement (Form 8-K Exhibit 10.1, 24 May 2024)", "eligible"),
    ("akts_2024_05_24_ex102", AKTS, "0001213900-24-046649", "ea020684501ex10-2_akoustis.htm", "2024-05-24T16:15:47Z",
     "contract", "Placement agency agreement (Form 8-K Exhibit 10.2, 24 May 2024)", "eligible"),
    ("akts_2024_05_31_sd", AKTS, "0001213900-24-048445", "ea020712401ex1-01_akoustis.htm", "2024-05-31T16:05:26Z",
     "sec_specialized_disclosure", "Akoustis Technologies conflict minerals report (Form SD Exhibit 1.01), 31 May 2024",
     "eligible"),
    ("akts_2024_06_05_8k", AKTS, "0001213900-24-050064", "ea0207358-8k_akoustis.htm", "2024-06-05T16:30:10Z",
     "sec_current_report", "Akoustis Technologies Form 8-K, 5 June 2024 (Item 5.02)", "eligible"),
    ("qorvo_2024_05_20_8k", QRVO, "0000950103-24-006908", "dp211397_8k.htm", "2024-05-20T20:47:13Z",
     "sec_current_report", "Qorvo, Inc. Form 8-K, 20 May 2024 (Items 5.02, 8.01, 9.01)", "eligible"),
    ("qorvo_2024_05_20_release", QRVO, "0000950103-24-006908", "dp211397_ex9901.htm", "2024-05-20T20:47:13Z",
     "press_release", "Qorvo press release, 20 May 2024 (Form 8-K Exhibit 99.1)", "eligible"),
    # Outcome sources: registered so the isolation probes can be checked against real later text.
    ("akts_fy2024_10k", AKTS, "0001213900-24-085984", "ea0209774-10k_akoustis.htm", "2024-10-07T17:42:41Z",
     "sec_annual_report", "Akoustis Technologies Form 10-K for fiscal year ended June 30, 2024", "outcome"),
    ("akts_2025_05_15_8k", AKTS, "0001213900-25-044032", "ea0242256-8k_atech.htm", "2025-05-15T14:16:54Z",
     "sec_current_report", "ATech (Parent) Resolution Corp. Form 8-K, 15 May 2025 (Item 2.01)", "outcome"),
]

COURT = [  # (source_id, docket document, filed date, title, membership)
    ("ded_21cv1417_d015_reply", "15.0", "2021-12-15", "Defendants' reply brief in support of the motion to dismiss (D.I. 15)", "eligible"),
    ("ded_21cv1417_d047_reply", "47.0", "2022-04-01", "Defendants' reply brief in support of the motion to dismiss the "
     "first amended complaint (D.I. 47)", "eligible"),
    ("ded_21cv1417_d067_order", "67.0", "2022-05-10", "Order denying the motions to dismiss (D.I. 67)", "eligible"),
    ("ded_21cv1417_d112_joint_statement", "112.0", "2022-10-07", "Joint claim construction and prehearing statement "
     "(D.I. 112)", "eligible"),
    ("ded_21cv1417_d152_claim_construction", "152.0", "2023-03-15", "Claim construction order (D.I. 152)", "eligible"),
    ("ded_21cv1417_d313_order", "313.0", "2023-09-01", "Order following the 22 August 2023 discovery disputes hearing "
     "(D.I. 313)", "eligible"),
    ("ded_21cv1417_d487_reply", "487.0", "2024-02-27", "Plaintiff's reply brief in support of summary judgment of validity (D.I. 487)",
     "eligible"),
    ("ded_21cv1417_d545_order", "545.0", "2024-04-25", "Order granting in part and denying in part defendants' motion for "
     "summary judgment (D.I. 545)", "eligible"),
    ("ded_21cv1417_d546_order", "546.0", "2024-04-25", "Order on the motion to exclude the testimony of Dr. Michael Lebby "
     "(D.I. 546)", "eligible"),
    ("ded_21cv1417_d553_order", "553.0", "2024-04-30", "Order denying the motions to exclude the opinions of Carolyn Irwin "
     "and Melissa Bennis (D.I. 553)", "eligible"),
    ("ded_21cv1417_d557_order", "557.0", "2024-05-02", "Order granting plaintiff's motion for summary judgment on validity "
     "(D.I. 557)", "eligible"),
    ("ded_21cv1417_d587_brief", "587.0", "2024-05-13", "Qorvo bench memorandum opposing the Rule 50(a) motion on "
     "unfair-trade-practices damages (D.I. 587)",
     "eligible"),
    ("ded_21cv1417_d590_order", "590.0", "2024-05-14", "Order on the Rule 50(a) motions on unfair-trade-practices damages "
     "(D.I. 590)", "eligible"),
    ("ded_21cv1417_d602_judgment", "602.0", "2024-05-20", "Judgment (D.I. 602)", "eligible"),
    ("ded_21cv1417_d604_stipulation", "604.0", "2024-05-31", "Stipulated proposed order on post-trial briefing (D.I. 604)",
     "eligible"),
    ("ded_21cv1417_d605_schedule", "605.0", "2024-06-07", "Stipulated order on the post-trial briefing schedule (D.I. 605)",
     "eligible"),
    ("ded_21cv1417_d608_injunction_motion", "608.0", "2024-06-17", "Qorvo motion for a permanent injunction (D.I. 608)",
     "eligible"),
    ("ded_21cv1417_d608_1_proposed_injunction", "608.1", "2024-06-17", "Proposed permanent injunction order "
     "(D.I. 608-1)", "eligible"),
    ("ded_21cv1417_d611_amend_motion", "611.0", "2024-06-17", "Qorvo motion to alter or amend the 20 May 2024 judgment (D.I. 611)",
     "eligible"),
    ("ded_21cv1417_d613_new_trial_motion", "613.0", "2024-06-17", "Akoustis motion for a new trial or remittitur "
     "(D.I. 613)", "eligible"),
    ("ded_21cv1417_d613_1_proposed_order", "613.1", "2024-06-17", "Proposed order on the motion for a new trial or "
     "remittitur (D.I. 613-1)", "eligible"),
    ("ded_21cv1417_d616_declaration", "616.0", "2024-06-17", "Omnibus declaration in support of Akoustis's post-trial opening "
     "briefs (D.I. 616)", "eligible"),
    ("ded_21cv1417_d616_1_exhibit", "616.1", "2024-06-17", "Exhibit A to D.I. 616 (trial transcript excerpts)",
     "eligible"),
    ("ded_21cv1417_d616_2_exhibit", "616.2", "2024-06-17", "Exhibit B to D.I. 616 (Sedona Conference commentary)", "eligible"),
    ("ded_21cv1417_d616_3_exhibit", "616.3", "2024-06-17", "Exhibit C to D.I. 616 (Restatement excerpt)", "eligible"),
    ("ded_21cv1417_d616_4_exhibit", "616.4", "2024-06-17", "Exhibit D to D.I. 616 (correspondence and verdict form redline)", "eligible"),
    ("ded_21cv1417_d618_fees_motion", "618.0", "2024-06-17", "Qorvo motion for attorneys' fees (D.I. 618)", "eligible"),
    # Outcome: the amended final judgment (fees and pre-judgment interest fixed).
    ("ded_21cv1417_d717_amended_judgment", "717.0", "2024-11-21", "Amended final judgment (D.I. 717)", "outcome"),
]


def fetch(url: str, headers: dict | None = None) -> bytes:
    for attempt in range(8):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or UA), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if attempt == 7 or e.code not in (429, 500, 502, 503, 504):
                raise
            wait = e.headers.get("Retry-After")
            time.sleep(int(wait) + 1 if wait and wait.isdigit() else 30 * (attempt + 1))
        except OSError:
            if attempt == 7:
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(url)


DOCKET_UA = {"User-Agent": "Mozilla/5.0 (research; contact via github)"}  # the docket page refuses non-browser agents
ROW = re.compile(r'<div class="row (?:odd|even)[^"]*"\s*id="entry-\d+"\s*>(.*?)(?=<div class="row (?:odd|even)|$)', re.S)
COLS = re.compile(r'<div class="col-xs-1 text-center"><p>(.*?)</p></div>\s*<div class="col-xs-3 col-sm-2"><p>(.*?)</p></div>'
                  r'\s*<div class="col-xs-8 col-lg-7">\s*(?:<p>(.*?)</p>)?', re.S)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _docket_page(after: str, before: str, page: int) -> list[tuple[str, str, str]]:
    q = urllib.parse.urlencode({"filed_after": after, "filed_before": before, "order_by": "asc", "page": page})
    for _ in range(8):  # an empty body is the site's bot challenge: wait and ask again
        body = fetch(f"https://www.courtlistener.com/docket/{DOCKET_ID}/qorvo-inc-v-akoustis-technologies-inc/?{q}",
                     DOCKET_UA).decode("utf-8", "replace")
        if body:
            break
        time.sleep(120)
    else:
        raise RuntimeError(f"docket page {after}..{before} p{page} stayed empty")
    rows = []
    for m in ROW.finditer(body):
        c = COLS.search(m.group(1))
        if c:
            rows.append((_text(c.group(1)), _text(c.group(2)), _text(c.group(3) or "")))
    return rows


def docket_report() -> bytes:
    """The docket as it stood at D: every entry filed and entered on or before D and numbered at most 621, fetched
    from CourtListener's docket page in quarterly windows (anonymous pagination stops after three pages)."""
    from datetime import date, datetime, timedelta

    rows: dict[tuple[str, str, str], None] = {}
    start = date(2021, 10, 1)
    while start <= date.fromisoformat(D):
        end = min(date(start.year + (start.month + 2) // 12, (start.month + 2) % 12 + 1, 1), date(2024, 6, 21))
        for page in (1, 2, 3):
            got = _docket_page((start - timedelta(days=1)).strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y"), page)
            rows.update(dict.fromkeys(got))
            time.sleep(30)  # the docket page answers bursts with a bot challenge
            if len(got) < 80:
                break
        start = end

    def entered_ok(text: str) -> bool:
        m = re.search(r"\(Entered: (\d\d)/(\d\d)/(\d{4})\)", text)
        return not m or f"{m[3]}-{m[1]}-{m[2]}" <= D

    kept = []
    for num, when, text in rows:
        filed = datetime.strptime(when.replace(".", ""), "%b %d, %Y").date().isoformat()
        if filed <= D and (int(num) if num else 0) <= LAST_ENTRY and entered_ok(text):
            kept.append((filed, int(num) if num else 0, text))
    kept.sort()
    out = [f"<html><head><title>{CASE}: docket report through 20 June 2024</title></head><body>",
           f"<h1>{CASE}: docket report, entries filed through 20 June 2024</h1>",
           "<p>Qorvo, Inc. v. Akoustis Technologies, Inc. and Akoustis, Inc. United States District Court for the District "
           "of Delaware. Entries as recorded in the RECAP archive (CourtListener), one paragraph per entry: docket "
           "item (D.I.) number, date filed, docket text.</p>"]
    year = None
    for filed, num, text in kept:
        if filed[:4] != year:
            year = filed[:4]
            out.append(f"<h2>Entries filed in {year}</h2>")
        label = f"D.I. {num}" if num else "Unnumbered entry"
        out.append(f"<p>{label}. Filed {filed}. {html.escape(text)}</p>")
    out.append("</body></html>")
    return ("\n".join(out) + "\n").encode()


def entry(sid, url, rel, data, availability, kind_date, membership):
    return {"source_id": sid, "case_id": CASE_ID, "primary_url": url, "original_relative_path": rel,
            "package_relative_path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "availability": availability, "event_dates": [kind_date], "relative_to_lead_cutoff": membership,
            "mission_membership": {MISSION: membership}}


def court_availability(filed: str) -> dict:
    return {"date": filed, "precision": "day",
            "basis": "Federal docket entry filing date (CourtListener RECAP copy of the PACER document)",
            "intraday_runtime_policy": "Conservative end of stated date in America/New_York."}


def main() -> None:
    rows, display = [], {}
    for sid, cik, acc, doc, accepted, kind, title, membership in SEC:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        path = HERE / f"{sid}.html"
        if not path.exists():
            path.write_bytes(fetch(url))
            time.sleep(0.5)
        rel = str(path.relative_to(KIT))
        rows.append(entry(sid, url, rel, path.read_bytes(),
                          {"date": accepted[:10], "precision": "second", "accepted_at": accepted.replace("Z", ".000Z"),
                           "basis": "SEC submissions JSON acceptanceDateTime"},
                          {"type": "sec_filing", "date": accepted[:10]}, membership))
        display[sid] = {"title": title, "document_kind": kind, "publisher": "SEC EDGAR"}
    for sid, doc, filed, title, membership in COURT:
        url = f"https://storage.courtlistener.com/{RECAP}.{doc}.pdf"
        path = HERE / f"{sid}.pdf"
        if not path.exists():
            path.write_bytes(fetch(url))
            time.sleep(3)
        rel = str(path.relative_to(KIT))
        rows.append(entry(sid, url, rel, path.read_bytes(), court_availability(filed),
                          {"type": "court_filing", "date": filed}, membership))
        display[sid] = {"title": f"{CASE}, {title}", "document_kind": "court_filing",
                        "publisher": "U.S. District Court (PACER via RECAP)"}
    path = HERE / f"{DOCKET_SID}.html"
    if not path.exists():
        path.write_bytes(docket_report())
    rel = str(path.relative_to(KIT))
    rows.append(entry(DOCKET_SID, f"https://www.courtlistener.com/docket/{DOCKET_ID}/", rel, path.read_bytes(),
                      court_availability(D), {"type": "docket_report", "date": D}, "eligible"))
    display[DOCKET_SID] = {"title": f"{CASE}, docket report, entries filed through 20 June 2024",
                           "document_kind": "court_docket", "publisher": "U.S. District Court (PACER via RECAP)"}

    catalog = json.loads(CATALOG.read_text())
    catalog["missions"][MISSION] = {"cutoff": CUTOFF, "financial_measurement": "2024-03-31"}
    catalog.setdefault("case_selection", {})["lead"] = CASE_ID
    catalog["case_selection"]["secondary"] = "synergy_chc_2024"
    new_ids = {r["source_id"] for r in rows}
    kept = [s for s in catalog["sources"] if s["source_id"] not in new_ids]
    for s in kept:
        s.setdefault("mission_membership", {}).setdefault(MISSION, "out_of_case")
    for r in rows:
        for m in catalog["missions"]:
            if m != MISSION:
                r["mission_membership"][m] = "out_of_case"
    catalog["sources"] = kept + rows
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    (HERE / "display.json").write_text(json.dumps(display, indent=2) + "\n")
    print(f"registered {len(rows)} sources ({sum(r['relative_to_lead_cutoff'] == 'eligible' for r in rows)} eligible)")


if __name__ == "__main__":
    main()

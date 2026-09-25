"""Acquire the ChromaDex / Elysium case sources and register them in the kit catalog.

Run from the repository root: `uv run python Slope_Credit_Scenario_Research_and_Design_Kit/research/recent_cases/chromadex/acquire.py`.
SEC filings come from EDGAR (acceptance time from the submissions JSON); court documents are the free RECAP copies on
CourtListener (availability = the docket entry's filing date, day precision, end of day in New York).
Two RECAP copies were left out because their text cannot be extracted: D. Del. Dkt. 373 (image-only final
judgment) and Dkt. 379 (Federal Circuit mandate with a broken font encoding). Their facts appear in Dkt. 399
and the 10-K. Membership for the
mission `chromadex_20240903` is `eligible` when public by the cutoff and `outcome` after it. Re-running is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CATALOG = KIT / "research/revision_v2/data/sources.json"
CASE_ID = "chromadex_elysium_2024"
MISSION = "chromadex_20240903"
CUTOFF = "2024-09-03T23:59:59-04:00"
UA = {"User-Agent": "Slope sparse-events research owassmer1@gmail.com"}
CIK = "1386570"

SEC = [  # (source_id, accession, primary document or None for exhibit 99.1, accepted_at, kind, title, membership)
    ("cdxc_2023_10k", "0001628280-24-009380", "cdxc-20231231.htm", "2024-03-06T21:01:37Z", "sec_annual_report",
     "ChromaDex Corp. Form 10-K for fiscal year 2023", "eligible"),
    ("cdxc_2023_12_credit_8k", "0001628280-23-041428", "cdxc-20231208.htm", "2023-12-13T21:00:16Z", "sec_current_report",
     "ChromaDex Corp. Form 8-K, December 2023 (Items 1.01, 2.03)", "eligible"),
    ("cdxc_2024q1_10q", "0001628280-24-021645", "cdcx-20240331.htm", "2024-05-08T20:01:05Z", "sec_quarterly_report",
     "ChromaDex Corp. Form 10-Q for the quarter ended March 31, 2024", "eligible"),
    ("cdxc_2024q2_10q", "0001628280-24-035591", "cdcx-20240630.htm", "2024-08-07T20:01:36Z", "sec_quarterly_report",
     "ChromaDex Corp. Form 10-Q for the quarter ended June 30, 2024", "eligible"),
    ("cdxc_2024q2_release", "0001628280-24-035593", None, "2024-08-07T20:02:12Z", "press_release",
     "ChromaDex second quarter 2024 results press release (Form 8-K Exhibit 99.1)", "eligible"),
    ("cdxc_2024q3_10q", "0001628280-24-044540", "cdcx-20240930.htm", "2024-10-31T20:00:43Z", "sec_quarterly_report",
     "ChromaDex Corp. Form 10-Q for the quarter ended September 30, 2024", "outcome"),
    ("cdxc_2024_11_8k", "0001628280-24-048830", "cdxc-20241120.htm", "2024-11-21T21:01:28Z", "sec_current_report",
     "ChromaDex Corp. Form 8-K, November 2024 (Items 1.01, 9.01)", "outcome"),
    ("cdxc_2024_12_8k", "0001628280-24-049951", "cdxc-20241204.htm", "2024-12-04T22:26:23Z", "sec_current_report",
     "ChromaDex Corp. Form 8-K, December 2024 (Item 8.01)", "outcome"),
    ("cdxc_2024_10k", "0001628280-25-009774", "cdxc-20241231.htm", "2025-03-04T21:01:06Z", "sec_annual_report",
     "ChromaDex Corp. Form 10-K for fiscal year 2024", "outcome"),
    ("niagen_2026q2_10q", "0001386570-26-000043", "cdcx-20260630.htm", "2026-08-04T20:04:26Z", "sec_quarterly_report",
     "Niagen Bioscience Form 10-Q for the quarter ended June 30, 2026", "outcome"),
]

COURT = [  # (source_id, recap path, filed date, title, membership)
    ("ded_18cv1434_d001_complaint", "recap/gov.uscourts.ded.66360/gov.uscourts.ded.66360.1.0.pdf", "2018-09-17",
     "ChromaDex v. Elysium Health (D. Del. 1:18-cv-01434), Complaint for patent infringement (Dkt. 1)", "eligible"),
    ("ded_18cv1434_d369_opinion", "recap/gov.uscourts.ded.66360/gov.uscourts.ded.66360.369.0.pdf", "2021-09-21",
     "ChromaDex v. Elysium Health (D. Del. 1:18-cv-01434), Memorandum Opinion (Dkt. 369)", "eligible"),
    ("ded_18cv1434_d399_fee_order", "recap/gov.uscourts.ded.66360/gov.uscourts.ded.66360.399.0.pdf", "2024-03-25",
     "ChromaDex v. Elysium Health (D. Del. 1:18-cv-01434), Memorandum Order granting attorney fees and costs (Dkt. 399)",
     "eligible"),
    ("cacd_16cv2277_d001_complaint", "recap/gov.uscourts.cacd.666790.1.0.pdf", "2016-12-29",
     "ChromaDex v. Elysium Health (C.D. Cal. 8:16-cv-02277), Complaint (Dkt. 1)", "eligible"),
    ("cacd_16cv2277_d065_counterclaim", "recap/gov.uscourts.cacd.666790.65.0.pdf", "2017-10-11",
     "ChromaDex v. Elysium Health (C.D. Cal. 8:16-cv-02277), Second Amended Counterclaim (Dkt. 65)", "eligible"),
    ("cacd_16cv2277_d560_verdict_minutes", "recap/gov.uscourts.cacd.666790/gov.uscourts.cacd.666790.560.0.pdf",
     "2021-09-27", "ChromaDex v. Elysium Health (C.D. Cal. 8:16-cv-02277), Minutes of jury trial, day 5 (Dkt. 560)",
     "eligible"),
    ("cacd_16cv2277_d579_bench_order", "recap/gov.uscourts.cacd.666790/gov.uscourts.cacd.666790.579.0.pdf",
     "2021-12-27", "ChromaDex v. Elysium Health (C.D. Cal. 8:16-cv-02277), Order on remaining bench-trial counterclaims "
     "and post-trial briefing (Dkt. 579)", "eligible"),
    ("cacd_16cv2277_d594_stay", "recap/gov.uscourts.cacd.666790/gov.uscourts.cacd.666790.594.0.pdf", "2022-09-28",
     "ChromaDex v. Elysium Health (C.D. Cal. 8:16-cv-02277), Joint status report and stipulation regarding stay (Dkt. 594)",
     "eligible"),
    ("cacd_16cv2277_d618_judgment", "recap/gov.uscourts.cacd.666790/gov.uscourts.cacd.666790.618.0.pdf", "2024-08-13",
     "ChromaDex v. Elysium Health (C.D. Cal. 8:16-cv-02277), Judgment (Dkt. 618)", "eligible"),
    ("cacd_16cv2277_d671_amended_judgment", "recap/gov.uscourts.cacd.666790/gov.uscourts.cacd.666790.671.0.pdf",
     "2024-12-27", "ChromaDex v. Elysium Health (C.D. Cal. 8:16-cv-02277), Stipulated Amended Judgment (Dkt. 671)", "outcome"),
]


def fetch(url: str) -> bytes:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(url)


def exhibit_991(acc: str) -> str:
    base = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{acc.replace('-', '')}"
    index = json.loads(fetch(f"{base}/index.json"))
    names = [i["name"] for i in index["directory"]["item"]]
    return next(n for n in names if "ex99" in n.lower() and n.lower().endswith(".htm"))


def entry(sid, url, rel, data, availability, kind_date, membership):
    return {"source_id": sid, "case_id": CASE_ID, "primary_url": url, "original_relative_path": rel,
            "package_relative_path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "availability": availability, "event_dates": [kind_date], "relative_to_lead_cutoff": membership,
            "mission_membership": {MISSION: membership}}


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    rows, display = [], {}
    for sid, acc, doc, accepted, kind, title, membership in SEC:
        base = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{acc.replace('-', '')}"
        doc = doc or exhibit_991(acc)
        url = f"{base}/{doc}"
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
    for sid, recap, filed, title, membership in COURT:
        url = f"https://storage.courtlistener.com/{recap}"
        path = HERE / f"{sid}.pdf"
        if not path.exists():
            path.write_bytes(fetch(url))
            time.sleep(0.5)
        rel = str(path.relative_to(KIT))
        rows.append(entry(sid, url, rel, path.read_bytes(),
                          {"date": filed, "precision": "day",
                           "basis": "Federal docket entry filing date (CourtListener RECAP copy of the PACER document)",
                           "intraday_runtime_policy": "Conservative end of stated date in America/New_York."},
                          {"type": "court_filing", "date": filed}, membership))
        display[sid] = {"title": title, "document_kind": "court_filing", "publisher": "U.S. District Court (PACER via RECAP)"}

    catalog = json.loads(CATALOG.read_text())
    catalog["missions"][MISSION] = {"cutoff": CUTOFF, "financial_measurement": "2024-06-30"}
    catalog.setdefault("case_selection", {})["lead"] = CASE_ID
    catalog["case_selection"]["secondary"] = "synergy_chc_2024"
    catalog["case_selection"].pop("transfer", None)
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

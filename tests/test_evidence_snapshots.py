"""Isolation and provenance of the dated evidence snapshots (spec §9 step 2 exit condition):
future and other-case source IDs are inaccessible, and original table context is retrievable."""

import json
from datetime import datetime

import pytest

from app.evidence import SNAPSHOTS, snapshot
from app.evidence.store import EvidenceAccessError, EvidenceStore


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("evidence")
    manifests = {sid: snapshot.build_snapshot(sid, out_dir=out) for sid in SNAPSHOTS}
    stores = {sid: EvidenceStore(sid, out / f"{sid}.sqlite") for sid in SNAPSHOTS}
    return out, manifests, stores


def test_only_admissible_sources_enter_each_snapshot(built):
    _, manifests, stores = built
    catalog = json.loads(snapshot.SOURCES_JSON.read_text())["sources"]
    for sid, store in stores.items():
        present = {s["source_id"]: s["access"] for s in store.list_sources()}
        expected = {s["source_id"]: s["mission_membership"].get(sid) for s in catalog
                    if s["mission_membership"].get(sid) in ("eligible", "metadata_only")
                    and s["availability"].get("date")}
        assert set(present) == set(expected)
        assert all((acc == "full") == (expected[k] == "eligible") for k, acc in present.items())
        limit = datetime.fromisoformat(manifests[sid]["cutoff"])
        assert all(datetime.fromisoformat(s["available_at"]) <= limit for s in store.list_sources())


def test_catalog_research_annotations_stay_out_of_the_agent_db(built):
    out, _, _ = built
    for sid in SNAPSHOTS:
        raw = (out / f"{sid}.sqlite").read_bytes()
        for hint in (b"event_dates", b"updated_cash_measurement", b"effective_funding_date",
                     b"alleged_operational_events", b"mission_membership"):
            assert hint not in raw


def test_excluded_source_ids_are_absent_and_unreadable(built):
    out, manifests, stores = built
    for sid, manifest in manifests.items():
        raw = (out / f"{sid}.sqlite").read_bytes()
        store = stores[sid]
        for x in manifest["excluded"]:
            assert x["source_id"].encode() not in raw  # not even the ID is stored
            for probe in (x["source_id"], f"{x['source_id']}#s0000", f"{x['source_id']}#t0000"):
                with pytest.raises(EvidenceAccessError, match="not available in this snapshot"):
                    store.read(probe)
            with pytest.raises(EvidenceAccessError):
                store.search("settlement", source_ids=[x["source_id"]])
    reasons = {x["source_id"]: x["reason"] for x in manifests["synergy_20240813"]["excluded"]}
    assert reasons["synergy_credit_agreement_20250530"] == "membership_outcome"
    assert reasons["synergy_annual_2024"] == "membership_outcome"


# Facts established only by the May 2025 credit agreement (an outcome source for 13 Aug 2024):
# the supplier's full legal entity, the named settlement agreement, and the delayed-draw loan
# that refinances the Atrium and Vitabest settlements.
FUTURE_ONLY_FACTS = ["Vitabest Nutrition, Inc.", "Vitabest Settlement Agreement", "Delayed Draw Term Loan"]


def test_2025_only_facts_are_absent_but_the_admissible_label_is_kept(built):
    out, _, stores = built
    store = stores["synergy_20240813"]
    catalog = {s["source_id"]: s for s in json.loads(snapshot.SOURCES_JSON.read_text())["sources"]}
    later = (snapshot.KIT / catalog["synergy_credit_agreement_20250530"]["package_relative_path"]).read_text(
        errors="ignore")
    raw = (out / "synergy_20240813.sqlite").read_bytes().lower()
    for fact in FUTURE_ONLY_FACTS:
        assert " ".join(fact.split()) in " ".join(later.split()), f"probe {fact!r} is not in the 2025 source"
        assert fact.lower().encode() not in raw
        assert not any(fact.split()[0] in r["snippet"] and fact.split()[-1] in r["snippet"]
                       for r in store.search(fact))

    # The decision-date filing's own table label stays: the agent may cite it and infer the link.
    hit = next(h for h in store.search("VitBest") if h["kind"] == "table")
    row = next(r for r in store.read(hit["id"])["rows"] if r[0] == "VitBest")
    assert row[1] == "2,920,824"


# Facts public only after 3 Sep 2024 (ChromaDex outcome sources): the fee judgment's amount and date, Elysium's
# 11 Sep 2024 appeal, the appeal bond, the December settlement judgment and the company's 2025 rename.
CHROMADEX_FUTURE_FACTS = ["Niagen Bioscience", "Stipulated Amended Judgment", "9.2 million", "October 28, 2024",
                          "September 11, 2024", "Appeal Bond"]


def test_chromadex_outcome_facts_are_absent(built):
    out, _, stores = built
    catalog = [s for s in json.loads(snapshot.SOURCES_JSON.read_text())["sources"]
               if s["mission_membership"].get("chromadex_20240903") == "outcome"]
    later = " ".join(" ".join((snapshot.KIT / s["package_relative_path"]).read_bytes().decode("latin-1").split())
                     for s in catalog if s["package_relative_path"].endswith(".html")).lower()
    raw = (out / "chromadex_20240903.sqlite").read_bytes().lower()
    for fact in CHROMADEX_FUTURE_FACTS:
        if fact != "Stipulated Amended Judgment":  # that one is a PDF caption; its absence is still checked below
            assert fact.lower() in later, f"probe {fact!r} is not in an outcome source"
        assert fact.lower().encode() not in raw
    # The decision-date judgment itself is admissible: Elysium ordered to pay USD 2.5 million.
    assert "$2,500,000" in stores["chromadex_20240903"].read("cacd_16cv2277_d618_judgment#s0002")["text"]


def test_settlement_schedule_table_keeps_its_original_context(built):
    _, _, stores = built
    store = stores["synergy_20240813"]
    # The S-1/A repeats this schedule in the FY2023 audited notes; take the June 30, 2024 interim note.
    hits = [h for h in store.search("required to make future payments 802,445")
            if h["kind"] == "table" and "UNAUDITED" in h["heading_path"][0]]
    table = store.read(hits[0]["id"])
    assert ["2026", "802,445"] in table["rows"]
    assert table["caption"] == "The Company is required to make future payments as follows:"
    assert "December 28, 2023" in table["context_before"] and "settlement" in table["context_before"]
    assert "Note 11 — Notes Payable" in table["heading_path"]
    assert table["source"]["source_id"] == "synergy_s1a_20240813"
    # The table sits inside its section text, so reading the section gives the full passage.
    assert table["table_id"] in store.read(table["section_id"])["text"]


def test_financial_table_headers_align_with_values(built):
    _, _, stores = built
    store = stores["synergy_20240813"]
    hit = next(h for h in store.search("notes payable Atrium Knight Sanders") if h["kind"] == "table")
    rows = store.read(hit["id"])["rows"]
    assert rows[0] == ["", "June 30, 2024", "December 31, 2023"]
    assert ["Atrium", "4,802,445", "4,802,445"] in rows

    # Multi-row header with a spanning "Common stock" cell: each value sits under its own label.
    hit = next(h for h in store.search("Statement of Stockholders Deficit Balance as of June 30, 2023")
               if h["kind"] == "table")
    t = store.read(hit["id"])
    labels = [" / ".join(dict.fromkeys(col)) for col in zip(*t["rows"][:t["header_rows"]], strict=True)]
    row = next(r for r in t["rows"] if r[0] == "Balance as of December 31, 2022")
    by_label = dict(zip(labels, row, strict=True))
    assert by_label["Accumulated Deficit"] == "$(52,691,039)"
    assert by_label["Total Stockholders’ Deficit"] == "$(33,519,867)"
    assert by_label["Common stock / Shares"] == "89,889,074"


def test_mdna_is_not_labelled_as_financial_statement_notes(built):
    _, _, stores = built
    hits = [h for h in stores["barfresh_20241025"].search("Liquidity and Capital Resources", source_ids=["brfh_2024q3_10q"])
            if h["kind"] == "section" and h["heading_path"][-1] == "Liquidity and Capital Resources"]
    assert hits
    for h in hits:
        path = " > ".join(h["heading_path"])
        assert "Item 2" in path and "Notes to" not in path


def test_catalog_inconsistency_and_hash_mismatch_stop_the_build(monkeypatch, tmp_path):
    catalog = json.loads(snapshot.SOURCES_JSON.read_text())
    s1a = next(s for s in catalog["sources"] if s["source_id"] == "synergy_s1a_20240813")

    s1a["sha256"] = "0" * 64
    monkeypatch.setattr(snapshot, "_catalog", lambda: catalog)
    with pytest.raises(snapshot.CatalogError, match="Hash mismatch"):
        snapshot.build_snapshot("synergy_20240813", out_dir=tmp_path)

    s1a["availability"]["date"] = "2024-08-14"  # marked eligible but public after the cutoff
    with pytest.raises(snapshot.CatalogError, match="available"):
        snapshot.build_snapshot("synergy_20240813", out_dir=tmp_path)

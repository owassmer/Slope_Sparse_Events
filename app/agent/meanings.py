"""Plain-language meaning of each semantic answer, for the agent's tool results and the reviewer's viewer.

A reviewer sees "Allegation or request only", not `claim_posture = alleged_or_requested, p=.94`; the raw
distribution stays one click away. None of these phrases is a probability or a financial input.
"""

from __future__ import annotations

MEANINGS: dict[str, dict[str | bool, str]] = {
    "gap_relevance": {True: "Bears on the open question", False: "Not about the open question"},
    "usable_evidence": {True: "States a citable fact, term or status", False: "Background or cross-reference only"},
    "premise_conflict": {True: "Conflicts with an assumption in the question", False: "No conflict with the question's assumptions"},
    "instruction_like_text": {True: "Contains instruction-like text aimed at the system", False: "Describes the matter only"},
    "entity_scope": {"target": "Applies to the named entity and obligation", "different": "Concerns a different entity or obligation",
                     "unknown": "Cannot tell who or what it applies to"},
    "claim_posture": {"alleged_or_requested": "Allegation or request only", "imposed_by_court_or_authority": "Imposed by a court or authority",
                      "agreed_contractually": "Agreed by contract", "reported_completed": "Reported as having happened",
                      "planned_or_expected": "Planned or expected, not done", "unknown": "Posture unclear"},
    "obligation_status": {"required": "A required payment", "claimed_or_disputed": "Claimed or disputed, not agreed",
                          "conditional": "Owed only if a condition is met", "reported_satisfied": "Reported paid or satisfied",
                          "unknown": "Status unclear"},
    "cash_access": {"access_prohibited": "Access prohibited", "access_limited": "Access limited", "restriction_released": "Restriction released",
                    "restriction_requested_only": "Restriction requested only, not imposed", "unknown": "Access not established"},
    "activity_status": {"unavailable": "Activity stopped", "limited": "Activity limited", "planned_or_conditional": "Planned or conditional, not achieved",
                        "operating_or_restored": "Operating", "unknown": "Operating status unclear"},
    "offset_status": {"committed": "Committed funding", "committed_subject_to_condition": "Committed, subject to a condition",
                      "possible_or_disputed": "Possible or disputed only", "received_or_paid_for_borrower": "Already received or paid",
                      "unknown": "Offset not established"},
    "finding_support": {"supports": "The passage supports the finding as written", "contradicts": "The passage contradicts the finding",
                        "does_not_resolve": "The passage does not settle the finding"},
    "finding_atomicity": {"one_claim": "One checkable claim", "multiple_claims": "Combines several claims", "unclear": "Claim unclear"},
    "context_sufficiency": {"enough": "Enough context to interpret", "missing_target_context": "Unclear what it refers to",
                            "missing_definition": "A needed definition or unit is missing",
                            "missing_condition_or_timing": "A condition or timing qualifier is missing", "unclear": "Context unclear"},
    "economic_role": {"existing_cash_obligation": "Existing cash obligation", "cash_access_constraint": "Limit on access to cash",
                      "operating_change": "Change in operations", "financing_or_reimbursement": "Financing or reimbursement",
                      "noncash_accounting_item": "Noncash accounting item", "no_direct_financial_premise": "No direct financial effect",
                      "unclear": "Financial role unclear"},
    "statement_relation": {"agree": "The statements agree", "conflict": "The statements conflict",
                           "different_scope": "Different entities, periods or obligations; not a conflict", "unknown": "Relation unclear"},
    "baseline_overlap": {"same": "Possibly the same obligation as a baseline item (check for double counting)",
                         "distinct": "Distinct from the baseline item", "unknown": "Overlap unclear"},
}

# Answers that must not be accepted into a finding without an explicit, reasoned override.
NOT_SETTLED = {
    "finding_support": {"contradicts", "does_not_resolve"},
    "finding_atomicity": {"multiple_claims", "unclear"},
    "context_sufficiency": {"missing_target_context", "missing_definition", "missing_condition_or_timing", "unclear"},
    "entity_scope": {"different", "unknown"},
    "claim_posture": {"unknown"},
    "economic_role": {"unclear"},
}


def meaning(question_id: str, answer: str | bool | None) -> str:
    if answer is None:
        return "No answer returned"
    return MEANINGS.get(question_id, {}).get(answer, str(answer))

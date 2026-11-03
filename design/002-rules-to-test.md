# Rules the retired Python-walk tests checked (3 November 2026)

Each is a rule of the definition to hold on the compiled walk, by an exact test of its own or by the reading of
the questions; listed from the retired tests (git show b2ad00a9:tests/<file>.py for the code).

## test_chains
Dispute model 4.0.0 chains on the Akoustis pre-D record (tests/akoustis_fixture.py): probability composition, code timing, FRAP tolling, arithmetic removal of 'pay', statutory interest, the notes' defaults, petitions consumed by the engine, evidence routing and the merits questions' state. No network: judgments are stubs.

- `test_composition_sums_to_one_and_keeps_every_path`
- `test_the_ruling_classes_are_the_chain_rule_over_the_merits`
- `test_every_merged_ruling_class_is_cash_and_date_identical_and_jev_gets_its_range`
- `test_path_facts_pool_only_trajectories_where_the_situation_holds`: Inside the analysis period, before any petition, and for a question about an unpaid judgment, still owed.
- `test_a_levy_can_come_before_stay_approval_and_none_after_it`
- `test_the_pre_ruling_response_waits_for_the_levy_early_registration_makes_possible`: T1-a steps 3 and 6, T1-d: before the ruling only the levy the J9 order makes possible reaches cash, so A4 (I1) follows J9 yes and is dated on the levy; J9 no leaves no pre-ruling A4; no petition precedes the order.
- `test_a_payment_or_petition_on_the_levy_day_pre_empts_the_levy`: The $2.0M mechanics judgment with the post-trial motions pending: paths sum to one, and on the levy day the debtor's payment or petition comes first (a levy on day p does not reach cash after a petition on day p).
- `test_no_ruling_before_the_briefing_closes_plus_the_fastest_measured_lag`
- `test_frap_tolling_moves_the_post_ruling_windows_with_the_drawn_ruling`
- `test_an_increase_is_levied_only_once_its_own_stay_ends_and_the_original_at_once`
- `test_pay_is_removed_only_where_no_trajectory_can_fund_it`
- `test_statutory_interest_is_8_percent_simple_on_surviving_compensatory_only`
- `test_the_judgment_default_ripens_under_both_readings_unpaid_unstayed_and_noticed`
- `test_an_effective_stay_before_the_ripe_date_prevents_the_default`
- `test_the_notes_petition_questions_get_the_facts_of_the_day_each_actor_may_file`: H3 on the 19 Aug acceleration is dated at the earliest day §7.06 lets the holders file (acceleration + 60), the day the engine books their petition; A5 at the acceleration. Both pool only trajectories where the holders accelerated, and the holders' earliest filing date is a dated trigger.
- `test_delisting_is_a_default_on_its_date_and_the_repurchase_date_is_code`
- `test_a_filing_path_sets_the_petition_and_the_engine_stays_the_claim`
- `test_readings_are_routed_evidence_and_merits_questions_carry_no_cash`
- `test_the_injunction_and_settlement_questions_cite_their_own_law`
- `test_the_residual_questions_agree_in_number`
- `test_coupon_shares_to_capacity_and_legal_spend_stops_on_settlement`
- `test_no_separate_coupon_is_paid_on_notes_already_accelerated`
- `test_legal_spend_stops_on_vacatur_and_continues_on_a_new_trial`
- `test_neutral_residuals_reproduce_attribution_step_two`
- `test_settlement_stay_and_contract_date_facts_state_what_the_chain_computes`: The offer and the reduced security Jev weighs are the chain's own amounts; contract dates are those on or after the decision inside the period. A stub row stands in for the chain's per-trajectory arrays.
- `test_each_court_ruling_on_a_motion_has_the_facts_of_its_own_day`: J8 and J9 are asked of the court on the day it rules (motion + briefing + the engine's ruling-lag draw): every fact row of every stay_approved and registration_early question is, on every draw, the day the engine books the approval or the order and the cash that day before the lock or the levy (and, for a stay, the reduced security the engine measures that day). The company's stay motion keeps the motion day.
- `test_the_settled_share_counts_only_draws_that_paid_a_settlement`: A 'settles' branch books nothing on a draw where the settlement amount is zero; that draw is classed by what the engine booked (Unresolved here), so a path's Settled share is the share of its draws with a positive settlement paid and no petition in the period, and the Filed shares still sum to its chance of a filing.
- `test_every_company_response_question_has_its_date_cash_and_amount_owed`: The company's response arises only where an amount is still owed on the day (a levy before the ruling can already have taken all that survives it), so each one asked carries its decision date, cash and amount owed.
- `test_the_post_ruling_response_comes_on_the_levy_day_and_again_at_the_ripe_date_after_seeking`
- `test_each_listing_question_has_the_facts_of_its_own_decision_date`

## test_views
The two views: bank data versus bank data plus the researched record. Both carry the same distress decisions and common borrower inputs on the same operating draws; research facts enter the augmented view only. No network: judgments are stubs.

- `test_the_bank_view_decides_at_the_cash_floor_and_its_probabilities_sum_to_one`
- `test_with_no_dispute_the_augmented_view_is_the_bank_view`
- `test_both_views_share_the_operating_draws`
- `test_the_bank_questions_carry_bank_facts_only`
- `test_the_coupon_is_arithmetic_on_its_quote_and_an_unstated_coupon_stays_unknown`
- `test_both_views_pay_the_same_coupon_and_none_after_a_petition`
- `test_the_bootstrap_does_not_replay_the_june_coupon`
- `test_a_stay_locks_the_collateral_where_cash_covers_it_and_else_the_cash_above_need`
- `test_no_approved_stay_locks_the_company_below_its_30_day_need`: Reduced security is the cash above the 30-day need on the approval day, so no lock takes cash below the need that day.
- `test_a_stay_lock_is_released_on_the_day_the_dispute_ends`
- `test_a_stay_on_zero_offered_security_is_effective_only_in_the_noncash_sensitivity`
- `test_a_settlement_exists_only_where_its_amount_is_positive`
- `test_monthly_installments_stop_at_a_petition_and_release_the_claim_on_the_last`
- `test_the_i3_settlement_starts_at_the_later_of_enforceability_and_the_appeal_deadline`
- `test_the_company_answers_the_post_ruling_levy_before_it_is_booked`
- `test_the_holders_petition_waits_for_section_7_06_unless_the_sensitivity_says_otherwise`
- `test_an_acceleration_before_the_vote_call_ends_the_listing_chain_and_a_due_note_has_no_delisting_default`
- `test_the_trace_carries_the_dated_triggers_the_engine_computes`
- `test_due_by_the_horizon_is_the_pages_due_tile_not_contractual`: Fix 6: metrics report due by the horizon (installments due inside the period, the page's Due tile and collections.csv's last cumulative due), split exactly into collected, past due and frozen due; the collection rate is collected / due by the horizon. Contractual adds installments due after the horizon.

## test_line_state
Integer parity of the resumable draw state with the defining daily engine.

- `test_daily_rules`
- `test_real_walked_histories`
- `test_branch_event_replacement_and_invalid_day`

## test_stay_motion_history
A pending motion exists before its approval or denial, independent of that answer.

- `test_status_classification_follows_question_consumers`
- `test_denied_motion_has_no_approval_financial_effect`
- `test_motion_answers_keep_denial_distinct_from_no_motion`
- `test_intervening_enforcement_composes_before_approval_on_actual_failed_probabilities`
- `test_denied_post_route_keeps_pending_enforcement_and_does_not_enumerate_it_twice`
- `test_saved_preapproval_enforcement_reads_motion_not_future_court_answer`
- `test_court_identity_uses_completed_before_court_row_after_intervening_levy`
- `test_earlier_listing_is_a_prerequisite_for_saved_response_options`
- `test_listing_prerequisite_uses_motion_date_not_future_security_read`
- `test_completed_listing_boundary_retains_earlier_response_and_excludes_own_answer`
- `test_completed_holders_declaration_excludes_own_acceleration_but_keeps_other_occasion`
- `test_new_motion_is_pending_after_an_earlier_denial_and_own_denial_is_not_conditioning`
- `test_settlement_terms_exclude_future_court_but_actual_lock_remains_until_release`
- `test_inactive_deferred_question_keeps_original_domains_without_conditioning_rows`
- `test_prospective_settlement_does_not_fire_later_waiting_answers`
- `test_distress_order_uses_decision_day_and_preserves_later_cash_out`
- `test_mixed_distress_order_scopes_and_restores_population`
- `test_listing_preemption_stops_before_later_holder_decision`

## test_pending_claim
The 14 May pending-claim event model (dispute model 4.1.0): composition and cash only.

- `test_the_20_jun_case_keeps_its_floor_question`: Without a case share ledger the floor decision is 4.0.0's petition_cash_floor (yes / no).
- `test_verdict_composites_are_disjoint_and_exhaustive`: Every pair of conjunctions across the three verdict branches disagrees on some node's answer, and under random answers the branches' probabilities sum to 1.
- `test_verdict_questions_ask_no_amount_and_no_cash`: Each verdict node is one jury decision on a quoted form question; its state carries no cash facts.
- `test_1_every_path_family_sums_to_one`: Composition: under random Dirichlet answers for every node, on every draw the probabilities of the dispute's paths that follow it sum to 1, and likewise the bank view's (every composite is disjoint and exhaustive, and a question asked per option group forks each draw's path once per answer of its own group).
- `test_2_a_missing_judgment_never_activates_enforcement`: After no_award or set_aside no enforcement, stay, registration, judgment-default or response step exists, and the engine books no levy or lock from then on.
- `test_3_clocks_move_with_the_modeled_verdict`: The clocks a verdict starts, on the court's calendar (app/domain/court_calendar.py, from the case's courts): the clerk enters the judgment on the first day after the verdict its office is open; the post-trial motions run 28 days and the automatic stay 30, each to the next open day where it ends on a weekend or legal holiday (FRCP 6(a)(1)(C)); execution from the day after the stay's last day.
- `test_4_branch_amounts`
- `test_an_unknown_component_amount_stays_unknown`: Critical rule: an unknown amount never becomes 0. The lower branch takes the case's declared bound, labelled as the bound; without a declared bound the analysis refuses and names the component; the claimant's branch, which declares no bound, refuses too. The enhancement kinds are the bounded claimant_enhancements term, never summed.
- `test_the_claimants_sums_exclude_the_defense_theory`: A component marked as the defense's theory is never part of the claimant's sums, known or unknown.
- `test_the_page_states_the_collection_rule_of_the_setup`: The page's cash sentence follows Setup.collection: an automatic debit (14 May central), or collections only from cash above the cash floor (the sensitivity); never the other mode's rule.
- `test_5_claimant_branch_is_cash_and_date_identical_under_its_enhancements`: The claimant's branch is beyond cash under both claimant_enhancements settings: same cash, lock, petition, days.
- `test_6_coupon_base_shares_sensitivity_cash`
- `test_7_cash_facts_equal_the_engine_on_every_trajectory`: §16.4, in date order: on every trajectory where a step's decision falls inside the period before any petition, nothing walked after it books cash or security on an earlier day, and the cash its facts read equals the loan engine's balance at processing on the decision day (QUESTIONS §2.2; orchestrator, 29 Sep 2026): the engine's end balance the day before, run forward from the line's opening state on the whole path's event cash, plus the day's receipts, less the day's encumbrance change and any levy that day; the decision's own booking is not in it. Trajectories where a booking made at or after the step changes that day's receipts, locks or levies are aside. Paths with a cash-floor decision and the listing chain are always checked.
- `test_7k_daily_processing_equals_net_until_an_obligation_goes_unpaid`: QUESTIONS_20240514 §2.2: while every obligation is paid in full, the order of the day's payments cannot matter. On each trajectory of sampled paths (dispute and bank view), the daily processor equals the net engine on the path's own event cash (cash, collections, fundings, due, outstanding) on every day before the first day either leaves an obligation unpaid. Net's is its first failed debit: it reads the whole day's net flow, including an invoice that Slope pays when the debit succeeds first. Every levy the Chain books is reached.
- `test_7b_the_floor_books_the_same_cash_wherever_it_is_walked`: The cash floor and cash exhaustion are state-triggered: each books on its own day on every trajectory, so walking them first instead of where the tree asks them leaves every trajectory's event cash, security and petition unchanged (paths with a stay, a settlement or a levy after the floor question included).
- `test_7c_an_offering_is_offered_wherever_the_whole_path_makes_it_available`: Only the model's own rules remove a branch: a financing question (D7, D8) asked without 'initiate_offering' is one where no offering the company would initiate exists at the decision on any trajectory of any path through it (listed, no petition, none pending, shares left, and its proceeds covering the shortfall: QUESTIONS §2.6, Initiation), whatever is walked after it.
- `test_7d_the_stay_questions_state_the_collateral_the_engine_locks`: ⚑ bond collateral: the stay questions are told the engine's own figure on the approval day (the amount owed plus §1961 interest at the pending rate over bond_forward_interest_years, times the collateral share), the amount a covered stay locks, not the amount owed alone.
- `test_7e_owed_facts_count_only_what_is_taken_before_the_decision`: ⚑ amount owed, in date order: each step's owed fact equals the amount owed on its day on the whole path, counting only levies and payments dated before that day (a levy dated after a decision, walked before it, is not in it); and a pre-ruling judgment default does not ripen on a judgment set aside before its ripe date.
- `test_7f_one_dispute_end_a_satisfying_levy_ends_it_and_legal_spend_never_returns`: Fix 4: a levy or payment that satisfies the judgment ends the dispute (`resolve`, as a payment does): legal spend stops that day, nothing is owed and no later step arises on it; and after a petition an ended dispute's legal spend stays stopped (the petition zeroes the estate's other event cash only).
- `test_7g_delisting_defaults_the_notes_whether_or_not_the_dispute_ended`: The notes' Event of Default and repurchase on delisting do not depend on the lawsuit: after a settlement (the dispute ended) the delisting route still arises and can accelerate the notes, before any petition.
- `test_7h_the_ordinary_view_is_the_forecast_whose_dispute_ends_on_the_review_date`: Fix 3 (spec §16.1): with no dispute events, the ordinary view's cash equals the full forecast's cash on the same floor and listing steps where the dispute ends at no cost on the review date: legal spend stops from then and nothing else differs (operations, coupon, the floor decisions, the listing chain and the notes' delisting route).
- `test_7i_the_ordinary_view_asks_the_forecasts_questions_on_the_same_record`: Spec §16.1: each ordinary-view question is the forecast's question of that node type in the matching situation, built on the same case, record items, evidence and standard (QUESTIONS_20240514 §1 state). Only the situation (its figures come from the ordinary engine run), the question's filled figures and the events assumed on the path differ: the ordinary view assumes none of the dispute's branch events, and one more, that the event has no cash effect.
- `test_7j_the_ordinary_view_carries_the_ordinary_obligations_facts_and_no_dispute_fact`: Spec §16.3 (orchestrator, 28 Sep 2026): for each node type asked in both views, the ordinary view's situation states the ordinary obligations as the forecast does (the notes' principal from the instrument's record; the listing and delisting dates of the instrument and the rule) and nothing that exists only because of the dispute: no judgment stands, and no dispute date (appeal deadline, judgment default) is stated. One fact contract per node type (design 14 May §7.12): both views fill the contract's situation keys for it.
- `test_8_settlement_is_bounded_and_ends_the_claim`: A settlement never exceeds cash above the 30-day need on its payment date, nor the amount claimed (I0) or owed; paid, it resolves the dispute (claim, lock and legal spend end).
- `test_settlement_terms_are_the_bound_in_twelve_monthly_installments`: The case's terms (Owen, 28 Sep 2026): the settlement amount (cash above the 30-day need on the settlement date, capped) paid in 12 equal monthly installments from the settlement date, those after the horizon outside it; the claim released on the settlement date. The lump sum is the sensitivity. Jev's settlement questions are told the total and the monthly schedule, and acceptance is asked on those terms.
- `test_questions_marked_cash_receive_cash_facts`: Every question outside no_cash, the I0 settlement questions included (nothing is owed before the verdict), receives the cash at its decision date, unless its answer cancels on every path (a holders' petition after the period, merged into the class where nobody files: its reach is zero).
- `test_every_reading_event_has_its_question`: DisputeProfile.read asks event_<ev> for every event in readings.events; a missing question fails every instantiate_dispute (4.1.0's trial_pending had none).

## test_notes_decision
Notes decisions must see earlier-dated events even when those are walked later.

- `test_later_walked_settlement_restores_a_live_notes_decision`
- `test_filing_answer_does_not_change_its_own_question_facts`
- `test_later_listing_answer_does_not_reweight_earlier_notes_facts`
- `test_strong_join_retains_distinct_pending_filing_continuations`
- `test_prompt_uses_actual_context_and_classes_override_stale_prefix`
- `test_notes_filing_continuations_survive_prefix_petition_shortcut`
- `test_completed_notes_classes_conserve_probability_on_each_draw`
- `test_ripe_filing_continues_to_an_earlier_ruling`
- `test_unfired_judgment_question_cannot_borrow_a_later_delisting_default`
- `test_directly_dated_delisting_origin_has_valid_before_filing_facts`
- `test_no_award_notes_context_does_not_invent_pending_motions`
- `test_active_draw_replay_matches_full_replay_cash_and_dates`
- `test_saved_history_rebuild_preserves_cash_path_and_replaces_notes_classes`
- `test_equal_finished_prefixes_can_hide_distinct_holder_continuations`
- `test_incompatible_saved_boundary_is_rejected`
- `test_offering_prior_failure_is_dated_not_traversal_order`
- `test_notes_before_answer_excludes_later_same_day_appeal`
- `test_notes_context_identity_is_not_truncated`
- `test_notes_cache_reuses_only_same_before_answer_state`

## test_chronological_walk
Chronological frontier ordering, answer domains and probability composition.

- `test_saved_root_selects_the_earliest_chain`
- `test_mixed_draws_are_partitioned_by_their_earliest_decision`
- `test_pre_ruling_response_books_earlier_offering_before_offering_answers`
- `test_no_progress_guard_reports_the_selected_draw_and_history`
- `test_ruling_enables_response_to_an_existing_post_ruling_writ`
- `test_saved_root_domains_and_synthetic_mass`
- `test_floor_after_an_offering_that_closes_before_the_stay_approval`: other5 (draw 63): the ripe-response offering closes before the stay's day-117 approval. Its proceeds are in the balance the stay's security is sized on, so the next floor is dated on the re-sized lock (day 133, before the holders' petition on 159), in the question's replay as in the chronological walk's chain.
- `test_stay_resizes_when_a_waiting_decision_or_levy_changes_its_balance`: none279 (draw 279): the stay approved on day 112 is sized on the balance after the day-77 levy and the waiting offerings dated before it. Queuing them books nothing, so the cash version alone did not re-size the stay: on a one-draw slice the stay kept its pre-levy security, and resuming the walk's cached whole-draw prefix then dated the stay-window settlement (settle I4) never, with no offer, so the walk dropped a $2.1M question. The stay's state on the draw must not depend on which other draws share the chain.
- `test_walk_order_check_finds_the_skipped_stay_settlement`: The check judges a history by its own dated facts: before the stay fix the chronological walk emitted this none279 history without the day-112 stay-window settlement, which the check reports as missing; asking it (either answer) leaves nothing pending and due.

## test_question_petition


- `test_same_day_later_petition_does_not_remove_response`
- `test_before_answer_petition_survives_subset_and_light_traces`
- `test_i3_follows_the_creditor_decision_not_the_later_levy`

## test_court_before_answer
Court snapshots precede their own result and consequent same-day distress.

- `test_supported_same_day_levy_is_after_court_answer`
- `test_selected_court_rows_match_full_and_do_not_share_mutable_cache`
- `test_court_read_keeps_independent_dated_listing_and_financial_events`
- `test_no_levy_court_read_is_independent_of_own_lock`

## test_continuation
Envelopes must cover other histories, never just the history used to bound them.

- `test_contractual_prefixes`
- `test_every_pair_of_real_walked_histories`

## test_question_futures
The checker catches future-labelled keys even when cash/facts already use as_of.

- `test_registration_collateral_excludes_later_stay`
- `test_settlement_cannot_borrow_its_interval_from_later_motions`
- `test_court_order_record_matches_its_emitted_action`
- `test_registration_uses_order_date_not_traversal_prefix`
- `test_date_local_question_keeps_native_uniforms_and_has_no_violation`

## test_decision_frontier
Answer-free frontier against the saved dated-order failures.

- `test_frontier_never_books_an_answer_or_changes_semantic_state`
- `test_already_answered_waiting_transition_remains_a_frontier_boundary`
- `test_day96_response_precedes_prospective_floor`
- `test_day74_ruling_precedes_day77_floor`
- `test_pending_levy_is_a_boundary_before_ripe_payment`
- `test_per_draw_ties_support_and_unavailable_can_reenable`
- `test_date_matches_existing_booking_for_every_answer`

## test_dated_answer_domains
A saved failing walk: an earlier offering must precede a later answer domain.

- `test_saved_floor_failure_is_rewalked_with_feasible_answer_domains`
- `test_distress_chooses_earliest_per_draw_not_floor_first`

## test_dated_writs
Outstanding writs follow event dates, including release before collection.

- `test_pending_writs_preserve_each_date_and_draw`
- `test_release_preempts_only_writs_on_or_after_release`
- `test_pending_only_cash_view_is_independent`
- `test_pending_queue_divergence_is_dated_and_order_independent`
- `test_earlier_structural_step_does_not_book_future_writ`
- `test_absent_structural_probe_does_not_preempt_later_release`
- `test_merge_comparison_ignores_walk_history`: A chain served from a state walked on more draws carries the other draws' levies at zero amount and a stay memo key built from them; neither books anything, so the merge comparison must not see them (group 4's listing merge split on exactly these, so its tasks walked different pieces).
- `test_merge_check_builds_its_chains_from_the_path`: The merge check's two chains are built from the path alone, never resumed from the prefix cache (whose states, cut from more draws, can carry other draws' bookkeeping), so the answer cannot depend on walk history. Groups 1 and 15 split the same listing branch differently across tasks while the comparison read cached states.

## test_stay_sizing_reentrancy
Stay sizing reads collateral before posting its own lock.

- `test_cached_security_and_subset_question_agree`
- `test_initial_sizing_still_releases_prior_stay_in_read`: A later stay is sized on a read in which an earlier-dated end of the dispute releases the prior stay's security: the prior lock is released in that read, the later stay's own lock is not in it, and with the dispute ended by its approval the later stay locks nothing (the prior stay's lock is unchanged).

## test_proposal_terms
Proposals project commitments, never intervening decisions' answers.

- `test_settlement_projection_excludes_unmade_registration_order`
- `test_motion_record_matches_its_own_day_replay`
- `test_motion_terms_stay_fixed_while_approval_security_changes`

## test_deferred_recurrence
Date-local deferred records and settlement on newly effective legal states.

- `test_offering_record_does_not_inherit_a_later_settlement_quote`
- `test_motion_projection_does_not_use_a_later_debtor_payment`
- `test_effective_early_stay_opens_settlement_before_ruling`
- `test_appeal_opens_its_own_settlement`

## test_decision_snapshots
Jev's conditioning precedes the answer; reading it cannot change cash flows.

- `test_answers_do_not_change_their_own_conditioning`
- `test_stay_approval_does_not_describe_its_own_approved_stay`
- `test_delisting_deadline_uses_this_trigger_and_selected_lag`
- `test_snapshot_capture_leaves_all_financial_outputs_unchanged`
- `test_waiting_snapshot_keeps_each_firing_draw_and_copies_its_values`
- `test_later_offering_does_not_appear_in_an_already_captured_draw`
- `test_arrears_elapsed_time_filters_absent_draws_and_does_not_claim_continuity`
- `test_corrected_records_render_complete_questions`
- `test_ordinary_view_carries_the_same_complete_before_answer_record`
- `test_motion_retains_its_cash_and_separate_approval_security_terms`

## test_post_entry_state
Entry, the filing deadline and finality are distinct legal occasions.

- `test_entry_window_and_deadlines_do_not_depend_on_later_filing`

## test_equity_20240514
QUESTIONS_20240514 §2.6: at-the-market proceeds, an offering's shares and the share ledger, against hand computations from the price series and the record's figures (§5: 'for one month, at-the-market proceeds and the shares drawn equal a hand computation from §2.6; the share ledger never goes below zero').

- `test_the_ledger_and_the_volume_are_the_records`
- `test_one_month_of_at_the_market_proceeds_and_shares`: June 2024, by hand: 20% of the average dollar volume a trading day at the 14 May close ($0.44), whole shares; net of 3%; received T+1 (from 28 May). Received in June: the sales of 31 May and of 3-27 June (Juneteenth closed), 19 sales; traded in June: 3-28 June less 19 June, 19 sales.
- `test_one_offerings_shares_and_the_ledger_when_capacity_binds`: The price: the share price on the initiation day (the structural price at the judgment less what was levied by then) less January's discount to its prior close ($0.50 on $0.7046); the shares, the January gross ($11.5M) over it, capped by the shares left; where capacity binds, the gross at the price and the net bearing the January costs in proportion. The ledger never below zero.
- `test_at_the_market_proceeds_at_the_post_verdict_price`: A $67.5M judgment: the 3 Jun 2024 sale (settled T+1 on 4 Jun) sells the same 153,213 shares at the day's share price, the structural price at the amount owed that day (entered plus §1961 interest; nothing levied yet), net of 3%.
- `test_an_offering_at_entry_prices_at_the_judgment_and_locks_up_the_sales`: The lower award ($1,426,412) with an offering at entry: its price is the structural price at that amount less January's discount, the January gross over it (capacity does not bind), January's net. The underwriting agreement's lock-up (§4(l), no at-the-market exception): no sale from pricing (launch + 1 day) through the 90th day after the close, every sale before and after it.

## test_appeal_state
Appeal wording and question classes use the same before-answer dates.

- `test_deadline_is_not_expired_on_or_before_its_date`
- `test_routing_tags_cannot_establish_filing_or_expiry`
- `test_only_an_appeal_filed_by_the_decision_is_stated`
- `test_mixed_deadline_statuses_split_without_changing_an_unchanged_scenario`
- `test_appeal_answers_keep_the_quiet_question_status`
- `test_filed_context_does_not_add_an_unused_deadline_partition`
- `test_class_context_reaches_the_question_renderer`
- `test_context_of_one_scenario_does_not_depend_on_its_neighbour`

## test_ripe_after_levy
Real payment paths from part1830010 / part1220001: ripe is not a levy response.

- `test_real_ripe_payment_sees_earlier_levy_with_or_without_later_listing`

## test_forecast_optimizations
Optimized grouping and row reads preserve dated questions and stored identities.

- `test_context_patterns_match_dated_trace_classification`
- `test_saved_row_decode_and_late_identity_unchanged`

## test_terminal_continuation
Leaf completion preserves financial arrays and before-answer records, including sliced rows.

- `test_leaf_completion_matches_replay`


## test_exact_question_keys (retired with the oracle, 3 November)
Numeric question identity at its limits: the keys of every node and context; the verdict form's union and selected
conjunction; a deferred question's origin identity separate from its current amount; the consumption flags (appeal
and stay status) as case data; the notes condition bits in the question order; a nonpayment record holding the
automatic due date, not a petition; a direct court security omitting only the projected owed. To be written on the
compiled walk alone.

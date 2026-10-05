# Stage 4 acceptance (tester seat)

Black-box, from `stage-4.md` (refunds, batch corrections), earlier stages and the architect's S4
DECISIONs. API only. Same oracle as stage 3 (`World.oracle`: Σ totals, available ≥ 0, statements
chain/close/order, openings sum to the seeded total, holds consistent, feed visibility); statement
ties accept the plain string order or the natural order of payment ids (p_9 before p_10).

```sh
PY=/Users/parth/Documents/kickoff/.venv/bin/python
BASE_URL=<s4> PREVIOUS_BASE_URL=<s3 build> S2_BASE_URL=<s2 build> S1_BASE_URL=<s1 build> \
  $PY -m pytest stage-4/acceptance -q -p no:cacheprovider
# regressions against the stage-4 build (PF_STAGE=4 skips the earlier stages' overshoot guards):
PF_STAGE=4 BASE_URL=<s4> $PY -m pytest stage-3/acceptance -q -p no:cacheprovider   # PREVIOUS_BASE_URL/S1_BASE_URL optional
PF_STAGE=4 BASE_URL=<s4> $PY -m pytest stage-2/acceptance/tester/api -q -p no:cacheprovider
PF_STAGE=4 BASE_URL=<s4> $PY -m pytest stage-1/acceptance -q -p no:cacheprovider
```

| Area | Tests |
|---|---|
| refund shape, refund_of everywhere, feed rule, statements/revisions | test_s4_refunds::test_refund_is_a_reverse_payment_*, test_refund_is_a_feed_payment_*, test_every_payment_shape_carries_refund_of, test_refund_in_statements_and_revisions |
| cumulative bound, current corrected amount, correction below refunded | test_cumulative_refunds_*, test_a_single_refund_above_the_payment, test_refund_bound_follows_*, test_a_correction_cannot_reduce_*, test_correction_order_stale_then_refund_exceeds |
| validation, ownership, key rules, precedence (S4-D1) | test_invalid_refund_amounts_are_422, test_missing_amount_*, test_body_must_be_json, test_only_the_receiver_may_refund, test_auth_and_key_rules, test_exceeds_comes_before_insufficient_funds |
| refund of refund, immutable refunds | test_refund_of_a_refund_is_invalid, test_refund_payments_cannot_be_corrected |
| funds (available, not total), atomic failure | test_refund_funds_are_the_receivers_available_not_total, test_a_failed_refund_changes_nothing |
| targets: direct, seeded, request, capture, settlement member; no reopen/hold restore | test_refund_targets, test_a_seeded_payment_can_be_refunded, test_refund_never_restores_a_released_hold |
| replay/reuse/concurrency | test_replay_and_key_reuse, test_concurrent_refunds_never_exceed_the_payment, test_concurrent_identical_refund_applies_once, test_concurrent_refunds_and_spending_*, test_refund_versus_correction_race, test_fifty_mixed_writers_with_refunds_conserve_money, test_snapshots_ignore_later_refunds |
| batch shape, shared recorded_at, batch id | test_s4_batches::test_basic_batch_*, test_batch_recorded_at_is_after_*, test_batch_id_is_exposed_on_batch_revisions_only |
| batch request rules (401/403/key/shape/1..32/dups/fields) | test_permissions_and_key, test_corrections_must_be_a_nonempty_array_*, test_item_count_bounds, test_duplicate_payment_ids_are_422, test_item_field_validation, test_item_fields_are_required, test_future_effective_at_is_422, test_unknown_fields_are_ignored |
| per-item input order | test_item_errors_are_reported_in_input_order, test_within_one_item_linked_precedes_stale, test_refund_exceeds_is_an_item_error, test_item_errors_precede_settlement_completeness |
| settlements: completeness, instants, offsets | test_correcting_all_members_*, test_a_partial_settlement_is_incomplete, test_members_must_share_*, test_equal_instants_in_different_offset_spellings_*, test_members_may_move_*, test_members_of_two_settlements_*, test_single_correction_of_a_member_*, test_a_refunded_member_* |
| funds and history | test_combined_affordability_*, test_insufficient_funds_rejects_the_whole_batch, test_funds_use_available_not_total, test_insufficient_funds_precedes_historical_overdraft, test_historical_overdraft |
| batch idempotency/concurrency | test_replay_returns_the_original_batch_response, test_a_rejected_batch_claims_no_key_*, test_concurrent_batches_sharing_*, test_overlapping_batches_and_single_corrections_*, test_concurrent_identical_batch_*, test_mixed_writers_with_batches_* |
| export/import v1–v4, ids/counters, snapshots | test_s4_export_scope::test_v4_round_trip_*, test_import_twice_*, test_unknown_future_schema_*, test_older_exports_* |
| later-stage surface absent | test_unspecified_surface_does_not_exist, test_no_later_fields |

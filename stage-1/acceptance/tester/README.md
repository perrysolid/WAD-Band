# Stage 1 acceptance suite (tester seat)

Black-box, written from `stage-1.md` and the architect's PLAN (R1–R36, D1–D24) only.
It talks to the running service over HTTP and nothing else.

```sh
python -m pytest stage-1/acceptance/tester --base-url http://127.0.0.1:8080 -q
# or: BASE_URL=http://127.0.0.1:8080 python -m pytest stage-1/acceptance/tester -q
```

Needs only `pytest` and `httpx` (the kickoff harness venv has both).

Every adversarial test ends with `World.oracle()`. It recomputes the invariants from the
public API: the total equals the seeded total, no wallet is negative, each user's receipts
explain their balance exactly, there are no duplicate payments in any feed, and every feed item
follows the §4 rule. `test_model.py` runs seeded random operation sequences against a reference
model (`pf_model.Model`).

## Requirement → test map

| Req | Tests |
|---|---|
| R1 health | test_runtime::test_health_is_ok |
| R2 image/limits | run by the reviewer/harness; 50-in-flight timing in test_concurrency::test_no_request_exceeds_the_timeout_under_load |
| R3 reset | test_runtime::test_reset_returns_204_and_seeds_logins, test_reset_replaces_all_state, test_repeated_resets_are_supported, test_reset_requires_no_auth; test_export_import::test_reset_clears_imported_state |
| R4 bad fixture | test_runtime::test_negative_seeded_balance_rejects_reset_and_changes_nothing; test_robustness::test_reset_with_invalid_fixture_changes_nothing, test_reset_with_garbage_is_4xx, test_fixture_api_rules_not_applied_to_seed |
| R5 seed | test_runtime::test_seeded_payments_are_not_replayed_against_balances, test_seeded_requests_keep_their_status, test_seeded_ids_do_not_collide_with_new_ids, test_currency_and_minor_units_come_from_the_fixture; test_settlements::test_operator_ids_default_empty |
| R6 conventions | test_runtime::test_responses_are_json_utf8, test_timestamps_have_explicit_offsets, test_ids_are_strings_of_at_most_64_chars, test_unknown_query_parameters_are_ignored, test_unknown_body_fields_are_ignored; test_settlements::test_unknown_fields_ignored_in_batch_and_entries |
| R7 envelope, no 5xx | every `expect_error` checks the envelope; test_robustness::* ; `assert_no_5xx` in every burst |
| R8 precedence (D1) | test_payments::test_precedence_*; test_requests::test_request_precedence, test_pay_precedence_permission_before_state_before_funds, test_pay_bad_visibility_before_state, test_third_party_permission_precedes_state; test_splits::test_split_precedence; test_settlements::test_entry_errors_in_input_order, test_entry_errors_before_insufficient_funds, test_non_operator_forbidden_before_body_validation; test_auth::test_every_wallet_endpoint_requires_a_token, test_signup_precedence; test_idempotency::test_claimed_key_beats_*; test_robustness::test_missing_key_precedes_unknown_path_resource, test_unknown_route_is_404_before_anything_else |
| R9 signup | test_auth::test_signup_shape_and_new_wallet, test_handle_is_derived_from_the_email, test_derived_handle_is_payable, test_handle_taken_creates_no_account, test_truncated_handle_collision_is_handle_taken, test_email_taken, test_password_minimum_length, test_bad_email_format, test_signup_missing_required_field, test_signup_wrong_type_is_malformed, test_email_is_case_insensitive, test_new_user_can_receive_and_be_asked_immediately, test_concurrent_signups_* |
| R10 login | test_auth::test_login_shape, test_login_failures_are_401, test_many_tokens_stay_valid_together |
| R11 bearer | test_auth::test_bad_credentials_are_401, test_every_wallet_endpoint_requires_a_token, test_bearer_scheme_is_case_insensitive |
| R12 hashing | test_runtime::test_large_seeded_reset_within_ten_seconds_and_logins_work, test_large_reset_with_distinct_passwords, test_logins_do_not_block_other_requests, test_export_does_not_contain_plaintext_passwords |
| R13 /me | test_payments::test_me_shape |
| R14 payment shape | test_payments::test_payment_shape_and_effect, test_defaults |
| R15 payment errors | test_payments::test_invalid_amounts_are_422, test_missing_amount_and_missing_handle_are_422, test_wrong_type_handle_is_malformed, test_self_payment, test_unknown_handle_is_404, test_note_length_boundary, test_non_string_note_is_422, test_bad_visibility_is_422, test_insufficient_funds_leaves_no_trace, test_body_must_be_an_object, test_invalid_utf8_body_is_malformed |
| R16 numbers | test_payments::test_integral_amount_forms_are_accepted, test_integral_amount_raw_forms, test_max_amount_is_accepted, test_huge_and_tiny_numeric_literals, test_non_finite_tokens, test_paying_the_whole_balance_reaches_zero |
| R17 notes | test_payments::test_note_round_trips_verbatim, test_note_length_boundary |
| R18 key header | test_idempotency::test_missing_or_empty_key, test_key_length_bounds; test_robustness::test_very_long_key_and_path_never_5xx, test_unicode_key_is_fine |
| R19 idempotency | test_idempotency::* (all five paths) |
| R20 create request | test_requests::test_request_shape, test_request_note_defaults_to_empty, test_request_may_exceed_payers_balance, test_request_moves_no_money_and_is_not_a_feed_item, test_request_amount_*, test_self_request, test_request_note_rules, test_request_unknown_payer, test_request_missing_and_wrong_type_payer |
| R21 pay request | test_requests::test_pay_*, test_only_the_payer_may_pay, test_concurrent_pays_of_one_request_move_money_once; test_idempotency::test_pay_request_body_empty_vs_explicit_public_are_different, test_pay_replay_after_paid_is_200_not_request_not_pending |
| R22 decline | test_requests::test_decline_by_payer_twice, test_only_payer_declines, test_decline_after_paid_or_cancelled, test_decline_cancel_unknown, test_decline_cancel_need_no_key_or_body |
| R23 cancel | test_requests::test_cancel_by_requester_twice, test_only_requester_cancels, test_cancel_after_paid_or_declined |
| R24 list requests | test_requests::test_list_only_my_requests_newest_first, test_list_direction_and_status_filters, test_list_bad_filters, test_pagination_limits_and_has_more, test_paid_request_lists_payment_id_for_both_parties |
| R25 int query params | test_requests::test_bad_limit, test_bad_offset; test_robustness::test_extreme_query_values, test_repeated_query_parameter_first_wins |
| R26 splits | test_splits::* |
| R27 shares | test_splits::test_spec_share_table, test_order_moves_the_extra_unit, test_splits_are_independent, test_split_is_not_a_feed_item_and_paid_shares_conserve |
| R28 feed | test_activity::test_feed_contract_exactly, test_feed_newest_first, test_feed_only_payments_never_requests_or_splits, test_feed_pagination, test_default_feed_limit_is_50, test_empty_feed |
| R29 one visibility | test_activity::test_visibility_is_one_value_seen_identically; test_settlements::test_members_follow_feed_visibility |
| R30 export | test_export_import::test_export_shape_and_is_read_only, test_export_is_a_snapshot_not_a_live_view, test_export_under_concurrent_writes_is_consistent |
| R31 import | test_export_import::test_import_is_replacement_and_repeatable, test_invalid_import_is_422_and_changes_nothing, test_import_unparseable_is_400, test_import_non_object, test_import_of_large_state_within_ten_seconds |
| R32 carried state | test_export_import::test_round_trip_through_a_reset, test_receipts_replay_after_import, test_failed_keys_remain_reusable_after_import, test_operators_and_settlement_membership_survive, test_new_ids_do_not_collide_after_import, test_pending_request_still_payable_after_import, test_snapshot_from_a_different_currency_restores_currency |
| R33 settlements | test_settlements::* ; test_idempotency (settlements path) |
| R34 conservation under load | test_concurrency::* ; test_requests::test_concurrent_pay_versus_cancel_and_decline; test_settlements::test_concurrent_settlements_cannot_overdraw, test_settlements_race_payments_on_the_same_wallet; test_idempotency::test_concurrent_*; test_model::* |
| R35 overshoot | test_scope::* |
| R36 unknown route/method | test_robustness::test_unknown_route_is_404_before_anything_else, test_unsupported_methods_are_404 |

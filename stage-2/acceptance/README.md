# Stage 2 acceptance (tester seat)

Black-box, written from `stage-2.md`, `stage-1.md` and the PLAN (S2-R1–R13, S2-U1–U14,
D25–D40) only. It talks to running services over HTTP (and a real Chromium) and nothing else.
Every test resets the service itself.

One command per suite, against any build:

```sh
PY=/Users/parth/Documents/kickoff/.venv/bin/python   # has pytest, httpx, playwright + chromium

# API (pytest + httpx only)
BASE_URL=http://127.0.0.1:8080 $PY -m pytest stage-2/acceptance/tester/api -q -p no:cacheprovider
# browser
BASE_URL=http://127.0.0.1:8080 $PY -m pytest stage-2/acceptance/tester/ui -q -p no:cacheprovider
# upgrade: PREVIOUS_BASE_URL is the accepted stage-1 image; SECOND_BASE_URL (optional) is a
# second stage-2 container for the D38 A -> B variant (without it, A is wiped and re-imported)
BASE_URL=http://127.0.0.1:8080 PREVIOUS_BASE_URL=http://127.0.0.1:8081 \
  SECOND_BASE_URL=http://127.0.0.1:8082 $PY -m pytest stage-2/acceptance/tester/upgrade -q -p no:cacheprovider
# stage-1 regression against the stage-2 build (PF_STAGE=2: see stage-1/acceptance/README.md)
PF_STAGE=2 BASE_URL=http://127.0.0.1:8080 $PY -m pytest stage-1/acceptance -q -p no:cacheprovider
```

`--base-url`, `--previous-base-url` and `--second-base-url` work instead of the variables.

## Invariant oracle

Every adversarial test ends with `World.oracle()` (`tester/conftest.py`). From the public API
alone it recomputes: Σ `total` = the seeded total; per user `balance == total`,
`available == total − held`, none negative; `held` = Σ `remaining_amount` of the caller's open
outgoing holds; every authorization is self-consistent (captured + remaining ≤ amount, closed ⇒
remaining 0, `payment_id` is the last of `payment_ids`, never `open` past `expires_at`) and listed
only to its two parties; every user's receipts explain their total exactly; captures in the feed
add up to `captured_amount`; no payment id twice in any feed; every feed item obeys §4. All sums
use exact Python ints. `api/test_s2_model.py` drives seeded random operation sequences
(pay, hold, capture, void, request, request pay, cancel) against a reference model written from the
requirements.

## Requirement → test map (file::test, prefixes dropped)

### API (`tester/api/`)

| Req | Tests |
|---|---|
| S2-R1 /me | test_s2_wallet::test_me_without_holds_keeps_stage1_fields_and_agrees, test_a_hold_reserves_without_moving, test_held_sums_every_open_hold, test_minor_units_zero_and_three_services_report_holds; `check_me` in every oracle |
| S2-R2 fixture | test_s2_fixture::* (stage-1 shape, empty list, ttl forms + invalid ttl, invalid/duplicate/non-array holds, Σ unexpired open > balance, equality boundary, closed/past holds excluded, available never seeded, D33 defaults, partial captured_amount, reset replaces, unknown fields) |
| S2-R3 authorize | test_s2_authorize::* (shape, defaults, ttl, both parties only, never a feed item, notes, amounts, literals, max, self, 404, visibility, available boundary, zero-balance signup, precedence, 401/key/body order, ids) |
| S2-R4 capture → payment | test_s2_capture::test_full_capture_returns_a_payment, test_capture_follows_the_feed_visibility_rule, test_capture_of_seeded_open_hold |
| S2-R5 final / extended | test_s2_capture::test_partial_final_capture_releases_*, test_explicit_final_true_*, test_second_capture_after_final_*, test_extended_capture_keeps_the_remainder_held, test_capturing_the_entire_remainder_*, test_final_false_with_omitted_amount_*, test_final_capture_after_partials_*; test_s2_void::test_void_after_partial_captures_*; test_s2_time::test_expiry_after_partial_captures_* |
| S2-R6 capture errors / D25 | test_s2_capture::test_exceeds_*, test_invalid_capture_amounts, test_capture_amount_above_the_service_maximum_is_422, test_integral_capture_literals, test_final_must_be_a_boolean, test_only_the_receiver_may_capture, test_unknown_authorization, test_capture_needs_token_and_key, test_capture_body_must_be_an_object, test_capture_precedence, test_capture_of_seeded_closed_hold, test_capture_of_a_seeded_open_hold_past_its_expiry |
| S2-R7 void | test_s2_void::* |
| S2-R8 list | test_s2_list::* |
| S2-R9 expiry | test_s2_time::* ; test_s2_capture::test_capture_of_a_seeded_open_hold_past_its_expiry; test_s2_void::test_void_of_a_clock_expired_hold_is_not_open; test_s2_list::test_status_filter_and_clock_expiry |
| S2-R10 available | test_s2_wallet::test_payment_is_judged_against_available, test_held_funds_cannot_fund_a_new_hold, test_request_pay_is_judged_against_available, test_settlement_net_debit_*, test_settlement_cannot_spend_*, test_receiver_cannot_spend_an_incoming_hold; test_s2_capture::test_capture_may_spend_the_reserved_money |
| S2-R11 concurrency | test_s2_concurrency::* (50-way holds drain available exactly, holds vs payments, partial captures ≤ amount, racing finals close once, capture vs void, payer spend vs capture, concurrent voids, settlements vs holds, expiry during a capture storm, random mixed storm) |
| S2-R12 authorization_id | test_s2_wallet::test_every_payment_carries_authorization_id_null_unless_captured, test_direct_payment_is_immediate_and_leaves_no_hold, test_paid_request_and_its_payment_are_unchanged_by_holds, test_request_creation_and_splits_ignore_holds |
| S2-R13 / D34 import | test_s2_export_import::* ; upgrade/test_upgrade::* |
| §7 seven paths | test_s2_idempotency::* (missing/empty/length, replay 200 identical, reuse 409, claimed key first, key order, concurrent first use, `{}` vs `{"amount":…}`, numerically equal amounts, replay after close/expiry/void, failed keys free, path scoping, per-user keys, other session, concurrent same key different bodies) |
| D35 timestamps | test_s2_time::test_seeded_expires_at_is_reported_in_utc_per_d35, test_every_response_timestamp_follows_d35, test_lexical_order_equals_time_order, test_created_at_is_close_to_the_real_clock; `check_auth` |
| D39 upper bound | test_s2_bounds::test_payment_*, test_insufficient_available_precedes_*, test_request_pay_*, test_settlement_credit_*, test_capture_above_two_pow_53_* (×3 bodies), test_capture_landing_exactly_*, test_holds_do_not_count_* |
| D40 import bounds | test_s2_bounds::test_clock_out_of_range_*, test_out_of_range_numbers_anywhere_in_state_* (×6 values), test_import_with_balance_or_hold_above_two_pow_53_is_rejected |
| Robustness | test_s2_robustness::* |
| Overshoot (stages 3/4) | test_s2_scope::* (no /statement, revisions, corrections, refunds, correction-batches, /history; no closed_at, as_of, known_at, refund_of, effective_at fields; /me ignores as_of) |

### Browser (`tester/ui/`, Playwright + Chromium, testids only)

| Req | Tests |
|---|---|
| S2-U1 routes | test_ui_auth_routes::test_every_route_loads_directly_by_url, test_signed_out_visits_go_to_login, test_navigation_reaches_every_screen; test_ui_static::* (D26 HTML vs JSON, 404 JSON) |
| S2-U2 auth | test_ui_auth_routes::* |
| S2-U3 wallet | test_ui_wallet_pay::test_wallet_without_holds, test_wallet_with_seeded_holds_right_after_reset, test_available_is_the_headline_number, test_formatted_amounts_follow_minor_units |
| S2-U4 / D28 amounts | test_ui_wallet_pay::test_typed_decimals_become_minor_units, test_invalid_amounts_are_refused_without_a_request, test_decimal_rules_follow_the_currency; test_ui_authorizations::test_invalid_or_excessive_capture_amounts_are_refused |
| S2-U5 / D29 pay | test_ui_wallet_pay::test_values_are_kept_after_success, test_visibility_select_*, test_unchanged_resubmission_moves_money_once, test_changing_a_field_*, test_changing_visibility_*, test_insufficient_funds_shows_pay_error_and_keeps_inputs, test_refused_recipients_*, test_pay_error_clears_*, test_paying_held_money_* |
| S2-U5 / D37 uncertain | test_ui_recovery::test_lost_payment_response_is_uncertain_and_retry_moves_money_once, test_uncertain_then_retry_after_more_clicks_still_once, test_uncertain_and_error_look_different, test_errors_and_uncertainty_are_announced |
| S2-U6 request form | test_ui_wallet_pay::test_request_form_* , test_request_for_more_than_the_payer_holds_is_fine |
| S2-U7 feed | test_ui_feed::* |
| S2-U8 requests | test_ui_requests::* |
| S2-U9 split | test_ui_split::* |
| S2-U10 authorizations | test_ui_authorizations::* |
| S2-U11 / D31 refresh | test_ui_recovery::test_a_delayed_earlier_read_never_overwrites_a_later_refresh, test_stale_read_after_own_payment_*, test_refresh_keeps_the_pay_form, test_refresh_observes_new_holds_and_releases, test_balance_spent_elsewhere_*, test_balance_held_elsewhere_*; test_ui_requests::test_request_cancelled_elsewhere_*; test_ui_authorizations::test_*_elsewhere_* |
| S2-U12 upgrade | upgrade/test_upgrade::test_browser_* (below) |
| S2-U13 self-contained | test_ui_static::test_csp_header_on_every_page, test_served_html_css_and_js_reference_no_external_url, test_assets_are_served_with_real_content_types; test_ui_quality::test_every_request_stays_on_the_service_origin |
| S2-U14 quality | test_ui_quality::* (no horizontal scroll at 375/1280, controls reachable at 375, visible labels, visible focus, WCAG AA text + error contrast, document basics, one type system, hold states distinguishable); test_ui_feed::test_items_are_formatted_for_people |

### Upgrade (`tester/upgrade/`, D34/D38, S2-R13, S2-U12)

| Case | Test |
|---|---|
| stage-1 export → stage-2 import: tokens, logins | test_stage1_export_imports_and_tokens_keep_working |
| identities, timestamps, records unchanged | test_records_survive_with_their_identities_and_timestamps |
| receipts replay with the original body | test_receipts_replay_with_the_original_body |
| lost-response pay (API) recovered once | test_lost_response_payment_is_recovered_once |
| failed keys stay free; new ids don't collide | test_failed_key_stays_free_and_new_work_continues |
| replacement + repeatable | test_import_is_repeatable_and_replaces |
| upgraded state round-trips as stage 2 | test_stage2_export_of_upgraded_state_round_trips |
| mutated stage-1 exports refused, unchanged | test_mutated_stage1_exports_are_refused_unchanged |
| **D38 forwarding**: browser signed in against stage 1 (API forwarded), lost pay, import, same tab retries → 200 original, money once, pending request paid from /requests | test_browser_signed_in_before_the_upgrade |
| refresh after upgrade, stage-2 features for the upgraded session | test_browser_refresh_after_the_upgrade_shows_the_imported_state |
| **D38 A → B**: browser on stage-2 A loses a pay response (abort after commit), export A, import into fresh B, tab routed to B: still signed in, retry → 200 original, money once, wallet shows B's balance + held | test_browser_survives_a_stage2_to_stage2_import |

## Risk families (part 7) → suites, each with the oracle

1 conservation with holds → test_s2_concurrency, test_s2_model, test_s2_wallet · 2 idempotency →
test_s2_idempotency, test_ui_wallet_pay, test_ui_recovery, upgrade · 3 precedence →
test_s2_authorize::test_precedence, test_s2_capture::test_capture_precedence,
test_s2_void::test_void_permission_precedes_state, test_s2_bounds (409 before D39 422) · 4
numbers → test_s2_fixture ttl, capture literals, test_s2_bounds · 5 hashing →
test_s2_export_import::test_export_contains_no_plaintext_password · 6 export/import →
test_s2_export_import, test_s2_bounds D40, upgrade · 7 time → test_s2_time · 8 browser recovery →
test_ui_recovery, test_ui_wallet_pay, upgrade browser tests.

Note (D40-1, architect DECISION): integers above 2^53 are not distinguishable after JSON
parsing (2^53+1 parses to 2^53, inside the bound). The D40 import tests probe 2^53+2 and
larger, and assert that exactly 2^53 is accepted.

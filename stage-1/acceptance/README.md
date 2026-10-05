# Stage 1 acceptance (tester seat)

One command against any running build (each test resets the service itself):

```sh
BASE_URL=http://127.0.0.1:8080 /Users/parth/Documents/kickoff/.venv/bin/python -m pytest stage-1/acceptance -q -p no:cacheprovider
```

`--base-url http://...` works instead of `BASE_URL`. Only `pytest` and `httpx` are needed.
The suite lives in [`tester/`](tester/); its README has the requirement → test map (R1–R36).

## Running against a later stage's build (DECISION PF_STAGE)

```sh
PF_STAGE=2 BASE_URL=http://127.0.0.1:8080 /Users/parth/Documents/kickoff/.venv/bin/python -m pytest stage-1/acceptance -q -p no:cacheprovider
```

`PF_STAGE` defaults to 1, so stage-1 runs are unchanged. With `PF_STAGE>=2`, `test_scope.py`
(the stage-1 overshoot guard, R35) is skipped and `test_payments::test_me_shape` checks the
stage-1 `/me` keys as a subset plus `balance == total` (stage 2 supersedes both by S2-R1, R3,
R12 and U1). No other test changes for a later stage. The stage-2 suite carries its own
overshoot guard for stages 3/4.

D35 (timestamps: UTC `+00:00`, `.mmm` only when non-zero) needed no change: every timestamp
check here already accepts an optional fraction and compares instants, not strings.

## Boundary decisions D39 and D40 (`tester/test_bounds.py`)

| Req | Tests |
|---|---|
| D39 balance above 2^53 → 422, after insufficient_funds, no key claimed | test_payment_above_two_pow_53_is_422_and_changes_nothing (amounts 1, 2, 1e9), test_payment_reaching_exactly_two_pow_53_is_allowed, test_insufficient_funds_precedes_the_upper_bound, test_upper_bound_refusal_claims_no_key, test_request_pay_above_two_pow_53_is_422_and_request_stays_pending, test_settlement_credit_above_two_pow_53_is_422 (1, 2, 1e9), test_settlement_bound_is_on_the_net_result, test_settlement_insufficient_funds_precedes_the_upper_bound |
| D40 import bounds → 422, destination unchanged, never 5xx after | test_import_with_clock_out_of_range_is_422_and_destination_unchanged, test_out_of_range_numbers_anywhere_in_state_never_break_the_service (9e15, 2^53+1, −1, 1.5, 253402300800000, 1e30 in every numeric state field), test_import_with_balance_above_two_pow_53_is_rejected |

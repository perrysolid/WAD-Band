# Stage 3 acceptance (tester seat)

Black-box, from `stage-3.md` (statements, corrections), earlier stages and the architect's
S3 DECISIONs only. API suite only (no new UI in stage 3). Each test resets the service itself.

```sh
PY=/Users/parth/Documents/kickoff/.venv/bin/python
BASE_URL=http://127.0.0.1:8080 PREVIOUS_BASE_URL=<stage-2 build> S1_BASE_URL=<stage-1 build> \
  $PY -m pytest stage-3/acceptance -q -p no:cacheprovider
# regressions against the stage-3 build:
PF_STAGE=3 BASE_URL=... $PY -m pytest stage-2/acceptance/tester/api -q -p no:cacheprovider
PF_STAGE=3 BASE_URL=... $PY -m pytest stage-1/acceptance -q -p no:cacheprovider
```

`PREVIOUS_BASE_URL` / `S1_BASE_URL` are optional (those upgrade tests skip without them).
With `PF_STAGE>=3` the stage-2 overshoot guard (`test_s2_scope`) is skipped, like stage 1's.

Oracle (`World.oracle`, run at the end of adversarial tests): Σ totals = seeded total; balance ==
total, available == total − held ≥ 0; every statement chains (`delta = balance_after − previous`),
never goes negative, closes at the current total, is ordered by effective_at then id; openings of
all wallets sum to the seeded total; Σ `/me?as_of` over all users = seeded total at sampled
instants and known_at views; holds consistent; no duplicate/foreign feed items.

| Area | Tests |
|---|---|
| created_at, seeding (S3 timestamps) | test_s3_history::test_seeded_*, test_activity_stays_newest_first_*, test_omitted_created_at_*, test_future_created_at_is_422_*, test_invalid_seeded_created_at_is_422, test_every_endpoint_returning_a_payment_has_created_at |
| /me?as_of | test_s3_history::test_as_of_*, test_bad_as_of_is_422, test_opening_balance_of_a_new_account_is_zero, test_as_of_views_always_sum_* |
| /statement | test_s3_history::test_full_statement_*, test_statement_* (window half-open, ties, pagination invariants, bad params, only own payments) |
| corrections + revisions | test_s3_corrections::* (shape, increase/decrease/zero, replay after newer revisions, key reuse, stale, field validation, 400/401/403/404 order, linked immutable, insufficient vs historical_overdraft, same-instant combine, atomic rejection with reusable key) |
| known_at | test_s3_corrections::test_known_at_*, test_correction_moves_a_payment_into_and_out_of_a_window, test_historical_views_sum_to_the_seeded_total_after_corrections |
| snapshots | test_s3_snapshot::* |
| historical holds, closed_at | test_s3_holds::* |
| concurrency | test_s3_concurrency::* (same expected revision: one wins; same key: once; competing increases; correction vs payment; 50 mixed writers; snapshot stability under writers; reference-model sequences) |
| export/import v1/v2/v3 | test_s3_export_scope::test_v3_*, test_older_exports_*, test_stage2_authorizations_survive_into_stage3 |
| stage-4 absent | test_s3_export_scope::test_stage4_endpoints_do_not_exist, test_no_stage4_fields_anywhere |

Error precedence for corrections follows the architect's S3-D5 (body 400 → 401 → key 400 →
claimed key → field 422 → 404 → 403 → linked 422 → stale 409 → insufficient 409 → historical 409).

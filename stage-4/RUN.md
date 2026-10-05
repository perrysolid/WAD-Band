# Pocketful — stage 4

HTTP service and web UI for payments, requests, splits, the activity feed, atomic net
settlements and payment authorizations (holds and captures). Node.js 22, no third-party dependencies, all state in memory.

## Build and start

```sh
docker build -t pocketful-stage-4 stage-4
docker run --rm -p 8080:8080 -e PORT=8080 pocketful-stage-4
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and needs no outbound
network at run time (`--network none` works). `GET /health` returns
`{"status":"ok"}` within a second of start.

Seed it with `POST /_test/reset` (fixture body), then sign in with
`POST /auth/login`.

## Tests

Unit tests (run on the host, Node.js 22+; they start the server in-process):

```sh
cd stage-4 && node --test test/*.test.js
```

The tester's black-box suite lives in `stage-4/acceptance/` (see its README).

## Web UI

Open `http://localhost:8080/` (it redirects to `/login`). All HTML, JS, CSS and the Inter
font (SIL OFL, `public/fonts/`) are served from the image under `/assets/`; nothing is
fetched from the network.

## State upgrade

`GET /_test/export` writes `state.schema_version` 2. `POST /_test/import` also accepts
a stage-1 export (schema version 1).

## Stage 3 decisions

- DECISION S3-D1 State code stays synchronous (one event-loop turn per write). Every correction is validated and
  checked on a draft (the new revision is appended, judged, and removed before anything else can run), so a rejected
  request changes no balance, revision, idempotency record or counter.
- DECISION S3-D2 Each payment keeps `revisions[]` (revision, amount, effective_at, recorded_at, reason). Balance views replay
  from each wallet's `opening` (seeded balance minus the net of original seeded payments; 0 for new accounts): per payment
  the latest revision recorded at or before `known_at`, applied at its `effective_at`.
- DECISION S3-D3 Holds keep an event list (creation, capture, void, expiry at `expires_at`); `closed_at` is the event time.
  An open hold in a future query expires at its deadline. Events other than expiry are known at their time.
- DECISION S3-D4 Statements are computed in full on first read and frozen as a snapshot (per user, cleared on reset);
  default `to` is unbounded (everything effective up to now, as no revision may be effective in the future).
  Order is selected `effective_at`, then payment id as a plain string (ids are opaque). Only limit/offset may accompany `snapshot`.
- DECISION S3-D5 Correction error order: body 400 -> 401 -> key 400 -> replay/key reuse -> field 422 (expected_revision,
  amount, effective_at, reason) -> 404 -> 403 -> 422 linked_payment_immutable -> 409 stale_revision -> 409
  insufficient_funds (vs available) -> 409 historical_overdraft.
- DECISION S3-D6 Exports are `schema_version` 3 (revisions, openings, hold histories, closed_at); versions 1 and 2 import
  with revision 1 synthesized from `created_at` and openings derived. Recorded times per payment strictly increase (+1 ms bump).

## Stage 4 decisions

- DECISION S4-D1 Refund = new payment (receiver -> sender, `refund_of` = target, note/visibility copied, `request_id` and
  `authorization_id` null). Error order: body 400, 401, key 400, replay/reuse, amount 422, 404, 403 non-receiver, 422
  invalid_refund_target, 422 refund_exceeds_payment (cumulative vs the CURRENT corrected amount), 409 insufficient_funds
  (receiver's available), 201. Refunds never touch requests, authorizations or settlement membership.
- DECISION S4-D2 Single correction: after stale_revision, an amount below the refunded total is 422 refund_exceeds_payment,
  then insufficient_funds (against available), then historical_overdraft. Captures and refunds are linked_payment_immutable.
- DECISION S4-D3 `POST /correction-batches` (settlement operator, key): 1..32 objects with distinct string payment_ids; per
  item in input order: fields 422, 404, linked_payment_immutable, stale_revision, refund_exceeds_payment; then
  incomplete_settlement; then differing member effective instants (compared as instants) 422; then combined net current
  funds vs available (409); then historical total/available at every boundary (409). All revisions share one recorded_at
  (strictly after every member's previous one) and carry `correction_batch_id` (`cb_<n>`). Everything is judged on a draft.
- DECISION S4-D4 Export is schema_version 4 (adds `refund_of`, `counters.cb`, revision `correction_batch_id`); versions 1-4 import.

## History screen (/history) — test ids

Served as HTML only when the client sends `Accept: text/html`; otherwise `/history` is the JSON 404. Signed-out loads go to /login.

- Balance as of: `history-asof-time` (datetime-local, browser time zone; sent as RFC 3339 with the offset in force), `history-asof-submit`,
  `history-asof-loading`, `history-asof-balance`, `history-asof-available`, `history-asof-held` (each with `data-amount` in minor units),
  `history-asof-echo` ("As of <exact string sent>"), `history-asof-error`.
- Statement: `history-from`, `history-to` (datetime-local, both optional; the window is [from, to)), `history-statement-submit`, `history-loading`,
  `history-opening`, `history-closing` (`data-amount`), `history-entries` (list), `history-entry-{payment_id}`, `history-entry-parties-{id}`,
  `history-delta-{id}` (signed text, `data-amount` = delta), `history-balance-after-{id}` (`data-amount`), `history-prev`, `history-next`,
  `history-page-info` ("Entries 21–25"), `history-empty`, `history-error`.
- Paging: 20 entries per page. The first request is `GET /statement?limit=20&offset=0[&from=&to=]`; every later page is
  `GET /statement?snapshot=<token>&limit=20&offset=<n>` with nothing else, so pages stay consistent. A 404 on a snapshot says the statement is no longer available.

## Payment detail (/payment/<id>) — test ids

HTML only with `Accept: text/html` (the API has no /payment/... route, so no collision). Reached by clicking a feed or history item
(the whole item, or the sentence link inside `activity-parties-{id}`). A non-party, or an unknown id, sees `payment-detail-unavailable`.

`payment-detail-sentence`, `payment-detail-parties` ("@from → @to"), `payment-detail-amount` (original amount, `data-amount`),
`payment-detail-current` (only after a correction, `data-amount` = corrected amount), `payment-detail-note`, `payment-detail-privacy`
(`data-visibility`), `payment-detail-time`, `payment-detail-refund-of` (link, refunds only), `payment-detail-revisions` (list),
`payment-detail-revision-{n}`, `payment-detail-revision-amount-{n}` (`data-amount`), `payment-detail-refresh`, `payment-detail-loading`,
`payment-detail-error`, `payment-detail-unavailable`, `payment-detail-no-actions`.

Refund form (receiver of a non-refund payment): `payment-detail-refund-amount`, `-submit`, `-error`, `-uncertain`, `-success`.
Correct form (sender of an ordinary payment; not settlement members, captures or refunds): `payment-detail-correct-amount` (0 allowed),
`-effective_at` (datetime-local, default now), `-reason`, `-submit`, `-error`, `-uncertain`, `-success`. The body sent is
`{expected_revision, amount, effective_at (RFC 3339 + offset), reason}`; the expected revision is the latest one loaded.
Both forms reuse their Idempotency-Key while the body is unchanged and mint a new one on any change.

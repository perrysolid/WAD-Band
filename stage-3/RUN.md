# Pocketful — stage 3

HTTP service and web UI for payments, requests, splits, the activity feed, atomic net
settlements and payment authorizations (holds and captures). Node.js 22, no third-party dependencies, all state in memory.

## Build and start

```sh
docker build -t pocketful-stage-3 stage-3
docker run --rm -p 8080:8080 -e PORT=8080 pocketful-stage-3
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and needs no outbound
network at run time (`--network none` works). `GET /health` returns
`{"status":"ok"}` within a second of start.

Seed it with `POST /_test/reset` (fixture body), then sign in with
`POST /auth/login`.

## Tests

Unit tests (run on the host, Node.js 22+; they start the server in-process):

```sh
cd stage-3 && node --test test/*.test.js
```

The tester's black-box suite lives in `stage-3/acceptance/` (see its README).

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
  Order is selected `effective_at`, then payment id (numeric-aware). Only limit/offset may accompany `snapshot`.
- DECISION S3-D5 Correction error order: body 400 -> 401 -> key 400 -> replay/key reuse -> field 422 (expected_revision,
  amount, effective_at, reason) -> 404 -> 403 -> 422 linked_payment_immutable -> 409 stale_revision -> 409
  insufficient_funds (vs available) -> 409 historical_overdraft.
- DECISION S3-D6 Exports are `schema_version` 3 (revisions, openings, hold histories, closed_at); versions 1 and 2 import
  with revision 1 synthesized from `created_at` and openings derived. Recorded times per payment strictly increase (+1 ms bump).

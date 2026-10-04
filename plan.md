# PLAN — pocketful, stage 2 (wallet screens and payment authorizations)

Source: `/Users/parth/Documents/kickoff/pocketful/spec/stage-2.md` ("S2") plus everything in
`stage-1.md` (§ refs). Folder `stage-2/`. It starts as a byte copy of the ACCEPTED `stage-1/`
(no nested `.git`), committed unchanged first. Every stage-1 requirement R1–R36 and decision
D1–D24 (in this same file, below) still holds unless amended here. Stage 2 must implement nothing
from stage 3 or 4.

## Requirements — API (S2 "Authorizations and captures", "Model", "API")

- **S2-R1** `GET /me` adds `total` and `available` and `held`; `balance == total` always; `held` = Σ remaining of effectively-open holds; `available = total − held` ≥ 0. With no holds, all earlier responses are unchanged apart from the new fields.
- **S2-R2** Fixture: `authorization_ttl_seconds` (default 600; if present it must be a positive integer per D3, else reset 422); `authorizations` array (default []). Each item has id, from_user_id, to_user_id, amount, note, visibility, status ∈ open|captured|voided|expired, and `expires_at` (RFC 3339 with offset). Unknown user ids, bad values or duplicate ids → 422. Σ of seeded UNEXPIRED open holds > that user's balance → 422, state unchanged. `available` is derived, never seeded. A stage-1-shaped fixture (no new keys) works unchanged.
- **S2-R3** `POST /authorizations` (idempotent; caller = payer) → 201 `{authorization_id, from_user_id, from_handle, to_user_id, to_handle, amount, captured_amount:0, remaining_amount:amount, currency, note, visibility, status:"open", expires_at = created_at + ttl, payment_id:null, payment_ids:[], created_at}`. Errors use the payment order: amount 422 → self_payment 422 → note 422 → visibility 422 → unknown handle 404 → available < amount 409 `insufficient_funds`. An open hold is never a feed item.
- **S2-R4** `POST /authorizations/{id}/capture` (idempotent; receiver only). Body `{amount?, final?}`; amount defaults to the remainder; `final` boolean, default true. 201 → a payment in the `POST /payments` shape plus `authorization_id`, with `request_id:null`, `settlement_id:null`, amount = captured, note and visibility copied from the authorization. It appears in `/activity` by the normal rule.
- **S2-R5** A final capture → status `captured` and releases the remainder in the same step. `final:false` with amount < remainder keeps status `open` and the remainder held. Capturing the entire remainder closes it even with `final:false`. `captured_amount` is cumulative, `payment_id` is the latest capture, `payment_ids` lists every capture in order, and `remaining_amount` is 0 once closed. Void or expiry after partial captures releases only the remainder and keeps the capture records.
- **S2-R6** Capture errors (order per D25): amount < 1 or non-integer → 422 `validation_failed`; `final` not a boolean → 400; unknown → 404; caller not receiver (incl. third party) → 403; captured/voided → 409 `authorization_not_open`; expires_at ≤ now (incl. seeded `expired`) → 409 `authorization_expired`; amount > remainder → 422 `capture_exceeds_authorization`. `{}` vs `{"amount":2000}` under one key → 409 reuse. A replay returns 200 with the original payment even after the authorization closed.
- **S2-R7** `POST /authorizations/{id}/void` (payer only, no key) → 200 authorization `voided`, hold released. Voiding again → 200 with the current state. Captured or expired → 409 `authorization_not_open`. Non-payer (incl. third party) 403; unknown 404.
- **S2-R8** `GET /authorizations`: only those where the caller is payer or receiver. Newest first. `direction` outgoing|incoming; `status` open|captured|voided|expired (a clock-expired hold matches `expired`, never `open`). limit/offset/has_more as R24/R25/D20a. Invalid enum values → 422.
- **S2-R9** Expiry: a hold with `expires_at` ≤ now is `expired` and holds nothing, at every read and write (`/me`, `/authorizations`, capture, void, every funds check), with no background job. Expiry at exactly expires_at counts as expired.
- **S2-R10** Every `insufficient_funds` (payments, request pay, settlements, authorizations) is evaluated against `available`. For settlements: every wallet's `available + net` ≥ 0. Captures may spend the money reserved for them.
- **S2-R11** Invariants under 50 concurrent writers: Σ total = seeded total; available ≥ 0 for every user; Σ captures ≤ authorized amount; each idempotent capture moves money once; a closed hold can't be captured again; concurrent capture/void/expiry on one hold serialize.
- **S2-R12** Every payment now carries `authorization_id` (null unless it is a capture). Everything else from stage 1 is unchanged, including `request_id` and `settlement_id`. Seven idempotent write paths.
- **S2-R13** Upgrade: `POST /_test/import` accepts an unchanged stage-1 export (schema_version 1) and a stage-2 export (schema_version 2). Tokens, logins, balances, pending requests (still payable), idempotency replays (incl. a payment whose response was lost before the export) and id sequences all survive. A stage-2 export round-trips authorizations (all fields, statuses, capture lists), ttl, hold state and counters.

## Requirements — browser UI (S2 screens, testids, behaviour)

- **S2-U1 Routes**: `/`, `/requests`, `/split`, `/signup`, `/login`, `/authorizations` load directly by URL (deep link, no 404). `/requests` and `/authorizations` serve HTML only when `Accept` includes `text/html`; otherwise the JSON API (D26). `/login` and `/signup` render their forms even when already signed in. A signed-out visit to `/`, `/requests`, `/split` or `/authorizations` goes to `/login`. Navigation between screens is consistent and visible on every screen.
- **S2-U2 Auth**: signup/login testids. `auth-error` is present only when there is an error. `current-user` (contains the display name) is on every screen when signed in. `current-handle` text is exactly the handle. `logout-button` signs out. A successful login/signup lands on `/`.
- **S2-U3 Wallet** (on `/` and `/authorizations`):
  - `wallet-available` is the headline number, with `data-amount`.
  - `wallet-balance` = formatted total, with `data-amount`.
  - `wallet-held` with `data-amount`, absent when held = 0.
  - Each text is EXACTLY the formatted amount (D27), with no labels inside the element.
  - `wallet-refresh` refreshes the balance + feed without clearing the pay form.
- **S2-U4 Amount input** (pay, request, split, authorize, capture): decimal as typed. `15`, `15.0`, `15.00` → 1500; `15.5` → 1550 (minor_units 2). Nonnumeric, negative, or more than `minor_units` decimals → the form's error element and NO request. Conversion is exact string arithmetic, never floats (D28).
- **S2-U5 Pay form**:
  - Values are kept after success.
  - An unchanged resubmission reuses the same Idempotency-Key and body: money moves once, no `pay-error`.
  - Any field change → a new key (D29).
  - Refused (4xx) → `pay-error`, refresh balance + feed, keep all inputs.
  - Lost response (network error, abort, timeout, 5xx, 408, 429) → `pay-uncertain` (non-empty), not `pay-error`; the form stays retryable with the same key and body.
  - A successful retry removes both elements, refreshes, and moves money exactly once.
  - `pay-visibility` is a select with values `public` and `private`.
- **S2-U6 Request form** (on `/`): request-* testids; `request-error` on refusal. Same key rules as pay.
- **S2-U7 Feed** (on `/`):
  - `activity-list` children are newest first in the DOM, one `activity-item-{payment_id}` per visible payment, with `data-visibility`.
  - `activity-parties-{id}` text contains BOTH handles.
  - `activity-amount-{id}` text is exactly the formatted amount (no sign).
  - `activity-note-{id}` text is exactly the note, and is present even when the note is empty.
  - `empty-activity` replaces `activity-list` when nothing is visible.
- **S2-U8 Requests** (`/requests`):
  - `incoming-list` and `outgoing-list` are always present. `request-item-{id}` has `data-status`; `request-amount-{id}` is exact.
  - Pay and decline buttons appear only on pending incoming items; cancel only on pending outgoing ones.
  - A refused pay/decline/cancel → `request-error` and a refresh of the lists, so a stale button disappears.
  - `empty-requests` appears when both lists are empty.
  - After any success, the lists refresh.
- **S2-U9 Split** (`/split`): `split-amount` follows the U4 rule. `split-handles` is comma-separated, trimmed, with empties dropped and one leading `@` stripped. `split-preview` shows one `split-share-{handle}` per participant before submit; its text is exactly the formatted share per §9 and identical to what the server computes. `split-error` appears on refusal.
- **S2-U10 Authorizations** (`/authorizations`, and the authorize form also on `/` per D30):
  - The authorize-* form follows the pay rules; `authorize-error` appears on refusal, including insufficient available funds.
  - `authorization-list` is newest first; `empty-authorizations` replaces it when empty.
  - Each `authorization-item-{id}` has `data-status`. Its parts are:
    - `authorization-amount-{id}`: exact.
    - `authorization-captured-{id}`: only when status is `captured`.
    - `authorization-expires-{id}`: the RFC 3339 `expires_at` exactly as the API returns it.
    - `authorization-capture-amount-{id}`: prefilled with the remainder (formatted decimal, no currency), and `authorization-capture-{id}`; both only on incoming open items.
    - `authorization-void-{id}`: only on outgoing open items.
  - `authorization-error` appears on a refused capture/void, followed by a refresh.
  - Seeded and new holds show correctly right after a reset.
- **S2-U11 Refresh rules**:
  - After any successful action, the same page shows the new balance, feed and lists without a manual reload, and refreshes only after the write succeeded.
  - Latest refresh wins: every load carries a sequence number per data domain, and a response older than the latest issued is discarded, even when responses arrive out of order (D31).
- **S2-U12 Upgrade**: the token lives in `localStorage`, so a signed-in browser stays signed in across an export/import between requests. In-memory form + key state survives, with no reload needed. A payment whose response was lost before the upgrade is retried with the same key and body, recovers the original payment (200), and refreshes the imported balance.
- **S2-U13 Self-contained**:
  - All HTML/JS/CSS/fonts are served from the image (`/assets/...`). No external URL appears anywhere in the served markup, CSS or JS.
  - Response header `Content-Security-Policy: default-src 'self'` (plus whatever inline policy the build needs; prefer none inline).
- **S2-U14 Product quality** (S2 "Product and visual direction"):
  - A calm, trustworthy consumer-finance look with one consistent system: type scale, spacing, colour, controls, feedback.
  - Primary actions are obvious.
  - Available, held, pending, loading, successful, refused and uncertain states are visually distinct.
  - People, amounts and times are formatted for humans (raw ids only where they help).
  - Works at a 375 px viewport and on desktop with no horizontal page scroll.
  - Visible labels on every input, visible keyboard focus, WCAG AA contrast.
  - Considered empty, loading and error states.
  - `aria-live` for errors and status.

## DECISIONS (stage 2; stage-1 D1–D24 + D20a still apply)

- **D25 Capture/void precedence**: body parse → 401 → key → claimed key → wrong-type 400 (`final`) → 422 `validation_failed` (amount) → 404 → 403 → 409 `authorization_not_open` (captured/voided) → 409 `authorization_expired` (clock-expired or stored expired) → 422 `capture_exceeds_authorization`. Void: 404 → 403 → voided = 200 current state; captured or expired → 409 `authorization_not_open`.
- **D26 HTML vs JSON**: `GET /`, `/split`, `/signup`, `/login` always return the HTML shell. `GET /requests` and `GET /authorizations` return HTML iff the Accept header lists `text/html` with q > 0, otherwise the authenticated JSON API (unchanged errors, 401 without a token). Static files are under `/assets/`. Every other unknown route → 404 JSON (D21).
- **D27 Money format**: `<integer part>.<exactly minor_units digits> <CURRENCY>`, with no decimal point when minor_units = 0. No thousands separators and no sign inside any testid element. Direction is shown in a separate element.
- **D28 Decimal parsing**: trim whitespace, then require `^[0-9]+(\.[0-9]{1,mu})?$` (mu = minor_units; integers only when mu = 0). The value must be ≥ 1 minor unit and ≤ 1000000000 minor units. Anything else is a client-side error and nothing is sent.
- **D29 Form idempotency identity**: each form keeps `{fingerprint, key}`. The fingerprint is the exact JSON body the form would send. On submit, if the fingerprint equals the stored one, the stored key is reused; otherwise a new UUID key is minted and stored. The identity is never cleared on success or refusal; only a change in the fingerprint replaces it. Request-row pay buttons use the identity (request_id + body). Capture buttons use (authorization_id + body).
- **D30 Placement**: the wallet panel (available headline, total, held, refresh) appears on `/` and `/authorizations`. The authorize form appears on `/authorizations` and as a "Hold money" card on `/`. testids are unique within a page.
- **D31 Latest-wins**: separate sequence counters for me, activity, requests and authorizations. Explicit refresh, post-action refresh and the initial load all go through the same loader.
- **D32 Expiry evaluation**: every request computes `now` (monotonic clock) at the start of its synchronous section and first expires every open hold with expires_at ≤ now (min-heap or scan). Expired holds release their remainder. Stage 2 exposes no `closed_at` (that is stage 3).
- **D33 Seeded authorizations**: optional `captured_amount` (default: `amount` if status is captured, else 0) and optional `payment_id` (default null). `created_at` = the reset instant. A seeded open hold whose expires_at ≤ reset time is expired. Seeded statuses voided/captured/expired hold nothing.
- **D34 State format**: export `state.schema_version = 2`. Import accepts 1 (upgraded: no authorizations, ttl 600, payments get `authorization_id: null`) and 2. Higher versions → 422.
- **D35 Timestamps (amends D10)**: output is UTC `+00:00`. Fractional seconds are printed as `.mmm` only when the millisecond part is non-zero (`2026-09-24T13:20:00+00:00` vs `…:00.250+00:00`). Lexical order still equals time order. Instants are stored in ms internally.
- **D36 UI stack**: no framework required. Vanilla ES modules + CSS served from the image, plus one vendored open-licence variable font (e.g. Inter woff2) in `stage-2/public/fonts/`, with a system fallback. No build-time network is needed for the UI.
- **D37 Lost-response classes**: a fetch rejection (network/abort), no response within 8 s, or HTTP 5xx/408/429 → uncertain. Every other 4xx → refused. 2xx → success (201 and replay 200 are treated the same).

## Risk map (stage 2)

1. Conservation and non-negativity with holds → S2-R1, R10, R11 (tester: 50-way authorize/capture/void/pay mixes with an oracle on Σtotal, available ≥ 0, Σcaptures ≤ amount).
2. Idempotency → S2-R3, R4, R6, R13 and U5/U12 (`{}` vs `{"amount":…}`, replay after close).
3. Precedence → D25 (tester pairs).
4. Numbers → capture amount D3; ttl D3 (`600.0` ok, `0`/`-1`/`1.5`/`"600"` → 422).
5. Hashing → unchanged.
6. Export/import → S2-R13 and D34 (stage-1 export into stage-2).
7. Time → S2-R9, D32, D35 (expiry exactly at the deadline, seeded past/future, offsets in fixture `expires_at`).
8. Browser recovery → U5, U11, U12 (Playwright: double submit, changed field, aborted response then retry, out-of-order refresh, signed-in across import).

## Work items (stage 2)

- **W5 developer**: copy the accepted stage-1 folder to stage-2, commit, then implement S2-R1–R13 and S2-U1–U14 per D25–D37, with unit tests. Gate: unit tests, the tester's stage-2 suite, the stage-1 acceptance suite against the stage-2 build, the runner `--stage 2`, and the upgrade gate (export from a stage-1 container, import into stage-2).
- **W6 tester**: `stage-2/acceptance/` covers API + Playwright UI + the upgrade (a stage-1 container export into stage-2) + 375 px no-horizontal-scroll + axe-style checks (labels, focus).
- **W7 reviewer**: clean-clone reproduction, browser walk at 375 px and desktop in every named state, overshoot probe (no closed_at, statement, history, refunds).
- **W8 architect**: gap pass, runner `claimed stage: 2`, extras after ACCEPT.

---

# PLAN — pocketful, stage 1 (payments and settlements)

Source: `/Users/parth/Documents/kickoff/pocketful/spec/stage-1.md` (§ refs below). Result repo
`/Users/parth/Documents/band` (branch `main`), folder `stage-1/`. Later stages (2–4) are built
afterwards in `stage-2/`…`stage-4/`, each a copy of the accepted previous folder.

## Requirements (one observable behaviour each; pass condition in brackets)

- **R1** §3.1–3.2 Listen on 0.0.0.0:$PORT (default 8080); `GET /health` → 200 `{"status":"ok"}` within 60 s of start.
- **R2** §2 `stage-1/Dockerfile` + `stage-1/RUN.md`; builds; runs with `-e PORT` alone, `--network none`/internal network, `--cpus 2 --memory 2g`.
- **R3** §3.3 `POST /_test/reset` (no auth) replaces ALL state (users, tokens, payments, requests, splits, settlements, idempotency records, imported state) → 204; repeatable. Old tokens → 401 after reset.
- **R4** §4 Fixture with a negative `balance`, or any incoherent fixture (D18) → 422 `validation_failed` and the previous state still serves (old tokens still work).
- **R5** §4/§11 Seeded users log in immediately; seeded balances are taken as-is (payments NOT replayed); seeded payments appear in `/activity` per the feed rule; seeded requests readable by both parties and payable/declinable per status; `settlement_operator_ids` default `[]`; `payments`/`requests` default `[]`.
- **R6** §3.4 Responses `application/json; charset=utf-8`; unknown body fields and unknown query params ignored; IDs ≤ 64 chars; timestamps RFC 3339 with explicit offset (D10).
- **R7** §5 Every 4xx/5xx body is `{"error":{"code":…,"message":…}}`; never a 5xx, including 50 concurrent in-flight requests.
- **R8** §5/§7 Error precedence on every endpoint exactly per D1.
- **R9** §4/§6 `POST /auth/signup` → 201 `{user_id, display_name, token}`; handle derived from email local part (lowercase, non-`[a-z0-9_]`→`_`, truncate 20); 409 `email_taken`; 422 password < 8 chars; 422 email not `local@domain`; 409 `handle_taken` and NO account created (email then still free); new user balance 0, can be paid and asked immediately; `/me` shows the derived handle.
- **R10** §6 `POST /auth/login` → 200 `{user_id, display_name, token}`; wrong password or unknown email → 401 `unauthenticated`; every login yields a new token; all tokens stay valid (multiple sessions), no expiry.
- **R11** §6 Bearer token required everywhere except `/health`, `/_test/reset`, `/_test/export`, `/_test/import`, `/auth/signup`, `/auth/login`; missing / malformed / unknown → 401 `unauthenticated`.
- **R12** §6 Passwords stored only as scrypt/bcrypt/argon2 hashes; hashing never blocks other requests; reset with a large seed finishes < 10 s (D14).
- **R13** §8 `GET /me` → exactly `user_id, display_name, handle, balance, currency, minor_units`.
- **R14** §8/§11 `POST /payments` → 201 payment: `payment_id, from_user_id, from_handle, to_user_id, to_handle, amount, currency, note, visibility, request_id(null), settlement_id(null), created_at`; note default `""`, visibility default `"public"`; debit+credit one atomic step.
- **R15** §5/§8 Payment errors: amount < 1, > 1000000000, fractional, string, boolean, null or missing → 422 `validation_failed`; `to_handle` = caller → 422 `self_payment`; note > 200 chars or non-string (incl. null) → 422; visibility not exactly `public`/`private` (incl. non-string) → 422; unknown handle → 404; balance < amount → 409 `insufficient_funds`; any failure leaves both wallets and the feed unchanged.
- **R16** §4/§5 `1000`, `1000.0`, `1e3`, `1.0e3` are the valid amount 1000; `1` and `1000000000` accepted; `0`, `-1`, `1000000001`, `1000.5`, `1e400`, `"1000"`, `true` → 422; paying exactly the whole balance succeeds (balance 0).
- **R17** §8 Notes stored/returned verbatim (no trim, escape, normalisation; emoji/NFD/RTL/`<script>` byte-exact); length counted in code points (D4): 200 ok, 201 → 422; `""` ok.
- **R18** §5/§7 `Idempotency-Key` required on the 5 write paths: absent or empty → 400 `missing_idempotency_key`; length 1..255 ok; 256 → 422 `validation_failed`.
- **R19** §7 First use → 201; replay (same user+method+path+body) → 200 with body identical to the original as a JSON value; same key + different body → 409 `idempotency_key_reuse`; body equality is parsed-JSON equality (key order/whitespace/numeric spelling irrelevant, D3); keys scoped per user AND per method+path (same key on another path or by another user is an independent first use); a key whose first use failed with 4xx is free; N concurrent identical first uses → exactly one 201, rest 200 same body, one effect; replay still returns the original after the resource changed (request later cancelled/paid, balance drained); a claimed key beats field validation and resource checks (409 for a now-invalid body).
- **R20** §8 `POST /requests` → 201 request `request_id, requester_id, requester_handle, payer_id, payer_handle, amount, currency, note, status:"pending", payment_id:null, created_at`; payer balance NOT checked; errors amount 422 → `self_request` 422 → note 422 → unknown handle 404.
- **R21** §1.3/§8 `POST /requests/{id}/pay` (payer only): body `{visibility?}`; 201 payment with `request_id`; request becomes `paid` with `payment_id`; 404 unknown, 403 non-payer (incl. third parties, D5), 409 `request_not_pending`, 409 `insufficient_funds` (request stays pending, payable later after funds arrive); `{}` vs `{"visibility":"public"}` under one key → 409 reuse; replay after success → 200 original even though now paid; a request moves money at most once (concurrent pays with different keys → exactly one 201, others 409 `request_not_pending`).
- **R22** §8 `POST /requests/{id}/decline` (payer only, no key): 200 request `declined`; repeat → 200 same; paid/cancelled → 409 `request_not_pending`; non-payer 403; unknown 404.
- **R23** §8 `POST /requests/{id}/cancel` (requester only, no key): 200 `cancelled`; repeat → 200; paid/declined → 409; non-requester 403; unknown 404.
- **R24** §8 `GET /requests`: only requests where caller is requester or payer; newest first (D11); `direction` incoming|outgoing|absent; `status` pending|paid|declined|cancelled|absent; other values → 422; `limit` 1..200 default 50; `offset` ≥ 0 default 0; `has_more` true iff items exist past the page; response `{requests, has_more}`.
- **R25** §5 Integer query params are plain ASCII digits: `1e9`, `4.0`, `+4`, `-1`, `""`, `abc`, ` 4` → 422; `limit=0`, `limit=201` → 422.
- **R26** §8/§9 `POST /splits` → 201 `{split_id, amount, currency, note, shares[{handle,amount}], requests[…], created_at}`; shares in given order incl. caller, sum = amount; one pending request per non-caller participant (caller is requester, note = split note), share 0 still gets a request; caller-only split → `requests: []`; no balance checked; errors amount 422 → empty/duplicate handles 422 → note 422 → unknown handle 404; failure creates no request.
- **R27** §9 Shares: base = floor(amount/n), first (amount mod n) participants get +1: 1000/3 → 334,333,333; 1/3 → 1,0,0; 10/3 → 4,3,3; 999/3 → 333×3; 5/5 → 1×5; reordered handles move the extra unit; paying all split requests conserves the total.
- **R28** §4/§8 `GET /activity` → `{payments, has_more}`: payments only (never requests/splits); a payment is visible iff public OR caller is sender/receiver; private visible to both parties; newest first; limit/offset as R24/R25.
- **R29** §4 Visibility is one value on the payment, identical for every viewer.
- **R30** §10 `GET /_test/export` (no auth) → 200 `{"track":"pocketful","format_version":1,"state":{…}}`; atomic read-only snapshot (later writes don't change an already-returned export).
- **R31** §10 `POST /_test/import` (no auth) with that object → 204, atomic replacement (not merge); importing twice yields identical state, nothing duplicated; unparseable JSON → 400; missing track/format_version/state, wrong track, wrong version, invalid state → 422 and destination unchanged.
- **R32** §10/§11 After import into a fresh container: same user ids, handles, hashed-password logins, existing bearer tokens, currency, minor_units, balances, payments (ids, timestamps, settlement links), requests (statuses), operators, settlement membership, completed idempotency records (replays → 200 original body; different body → 409); failed keys still free; id sequences continue without collision; previous destination users/tokens gone (401).
- **R33** §11 `POST /settlements`: no token 401; non-operator 403; key required; `transfers` array of 1..32 objects else 422; each entry: amount/note/visibility rules as payments, from=to → 422 `self_payment`, unknown handle 404; entry errors in input order, all before 409 `insufficient_funds`; affordable iff every wallet's net result ≥ 0 (a wallet may pay out money it receives in the same batch); all-or-nothing; failure claims no key and creates nothing; 201 `{settlement_id, committed_at, payments[input order]}`; each member `settlement_id` set, `request_id` null, `created_at == committed_at`; replay 200 original; members in feed per visibility; operator gains no access to others' requests/private items.
- **R34** §1 Conservation (Σ balances = seeded total) and non-negativity at every read under 50 concurrent writers: opposite-direction transfers between the same pair, many payers draining one wallet, many payers paying one request, concurrent splits/settlements/payments.
- **R35** Overshoot guard: stage-1 exposes nothing from stage 2+: `/me` has no `total/available/held`; payments have no `authorization_id`/`refund_of`; `/authorizations`, `/statement`, `/payments/{id}/corrections|revisions|refunds`, `/correction-batches` → 404; `GET /` and `GET /requests` with `Accept: text/html` serve no HTML UI (404 / JSON respectively); `as_of`/`known_at` ignored as unknown params.
- **R36** Unknown route or method → 404 `not_found` with the error body.

## DECISIONS (apply everywhere; tests encode them)

- **D1 Error precedence** (every endpoint, first failing step wins): (1) route match → 404; (2) body unparseable, or not a JSON object, on a body-taking endpoint → 400 `malformed_request`; (3) authentication → 401; (4) endpoint role (settlements: operator) → 403; (5) `Idempotency-Key` absent/empty → 400, then length > 255 → 422; (6) claimed key: same body → 200 replay, different body → 409 `idempotency_key_reuse` (no field validation before this; a wrong-type field under a claimed key is 409); (7) field checks: first ALL wrong-type 400s (D2), then 422 rules in the order the spec table lists them; (8) referenced resource not found → 404 (path resource first, then handles in body order); (9) resource permission → 403; (10) state conflicts → 409 (`request_not_pending`); (11) funds → 409 `insufficient_funds`. Signup: types → password<8 → email format → display_name present → `email_taken` → `handle_taken`.
- **D2 Wrong JSON type → 400** only for: `to_handle`, `payer_handle`, `participant_handles` (non-array or non-string element), signup/login `email`/`password`/`display_name`. Per §5 `amount` (any non-number incl. null), `note` (any non-string incl. null) and `visibility` (anything not exactly the two strings) are 422. Inside settlement `transfers`, every shape/type problem is 422 (batch-shape rule). Missing required field → 422.
- **D3 Numbers.** Amount validity is judged on the exact JSON literal: its decimal value must be an integer in range (`1000.0`, `1e3`, `10000e-1` ok; `1000.5`, `1e-1`, `1000.0000000000001` → 422; `1e400` → 422). `NaN`/`Infinity` are not JSON → 400. Replay body equality compares numbers by value (`1500` ≡ `1500.0` ≡ `1.5e3`), objects key-order-insensitive, arrays order-sensitive.
- **D4 Lengths** (note, Idempotency-Key, password) count Unicode code points.
- **D5 Permission.** An authenticated caller who is not the permitted party on an existing request (incl. a third party) gets 403, checked after 404.
- **D6 Idempotency scope** = (user_id, method, path without query, key). Stored only on success (status + response JSON + canonical body). No in-flight state is needed: claim-check, validation, money movement and record write run in one synchronous critical section (D22).
- **D7 Handles in bodies.** Any string naming no user (incl. `""`, `"Bob"`, `"@bob"`) → 404; self-handle check (422) happens before lookup, by exact string equality with the caller's handle.
- **D8 Email.** Valid iff exactly one `@`, non-empty local and domain parts, no whitespace. Uniqueness and login compare case-insensitively; stored as given. Handle derived from the local part as given.
- **D9 display_name**: required string, content not restricted.
- **D10 Time.** Canonical instant inside = integer epoch milliseconds (UTC) from a monotonic non-decreasing server clock. Output always fixed-width `YYYY-MM-DDTHH:MM:SS.mmm+00:00` (string sort = time sort). Seeded payments/requests get the reset instant (stage 1 ignores any supplied `created_at`).
- **D11 Ordering.** Newest first by `created_at`; ties broken by creation sequence, later first.
- **D12 IDs.** `<prefix>_<counter>` (`u_`, `p_`, `rq_`, `sp_`, `st_`), skipping any id already in use (seeded/imported); counters are exported. Tokens: 32 random bytes hex; store sha256(token).
- **D13 State format.** Export envelope `format_version: 1` forever (spec value). `state.schema = "pocketful-state"`, `state.schema_version = 1` for stage 1; every later stage reads every lower schema_version and upgrades it; a higher/unknown schema_version → 422.
- **D14 Password hashing.** scrypt via async `crypto.scrypt` (libuv threadpool, never on the event loop). API-created credentials: N=2^14,r=8,p=1, 16-byte random salt. Seeded credentials: N=2^12, halved while distinct_plaintexts × N > 3e6 (floor 16; amended after measuring 10 000 distinct at 10.85 s with 2^10), and identical plaintexts within one reset are hashed once (shared salt). Params stored per hash record. Budgets on 2 vCPU: reset of 10 000 users sharing one password < 2 s; 2 000 distinct passwords < 6 s; 50 concurrent logins never 5xx or > 5 s.
- **D15 Body size** limit 64 MiB; larger → 400 `malformed_request`.
- **D16 Bodies.** Content-Type not enforced. On body-taking endpoints an empty body is unparseable → 400 (clients send `{}`). Decline/cancel/GET ignore any body.
- **D17 Authorization** header: scheme `Bearer` (case-insensitive), one space, token; anything else → 401.
- **D18 Fixture validation** (→ 422, state unchanged): object; currency non-empty string; minor_units ∈ {0,2,3}; users array, each with string id (1..64), email, password, display_name, handle matching `^[a-z0-9_]{1,20}$`, integer balance 0..2^53; unique ids/handles/emails (case-insensitive); payments/requests reference existing users, valid amounts/notes/visibility/status, unique ids; operator ids exist. API-only rules (password ≥ 8, email shape) are NOT enforced on fixtures. On `/_test/reset` and `/_test/import`, unparseable → 400, any parsed non-conforming body (incl. non-object) → 422; D1 step 2 applies to API endpoints only.
- **D19 Settlements.** Operator check right after auth (D1 step 4). Entry checks in input order; within an entry: shape → amount → self_payment → note → visibility → from_handle 404 → to_handle 404.
- **D20 Query params.** First occurrence wins; integers `^[0-9]+$` (leading zeros ok); enums exact lowercase.
- **D21 Unknown route/method** → 404 `not_found`.
- **D22 Concurrency.** One Node.js process, one in-memory state. Every write runs validation, checks and mutation synchronously in a single event-loop turn (no `await` inside); slow work (body read, scrypt) happens before. Export = synchronous deep copy; import = parse+validate fully, then swap the state reference in one step. Before commit each write recomputes Σ deltas = 0 and every touched balance ≥ 0, else refuses (defence in depth).
- **D23 Stack.** Node.js 22 LTS, no runtime npm dependencies (node:http, node:crypto); image `node:22-slim` or alpine. Tests outside the image.
- **D24 Splits.** Requests created in participant order, sharing the split's `created_at`; share-0 requests are pending and payable (moving 0).

## Risk map (risk → requirement → test owner)

1 conservation/non-negativity under 50 writers → R34, D22 → tester concurrency suite + oracle. 2 idempotency → R18, R19, R21, R32, D3, D6. 3 precedence → R8, D1, D2, D19. 4 numbers → R16, R25, D3, D20. 5 hashing → R12, D14 (timed reset + concurrent login test). 6 export/import → R30–R32, D12, D13. 7 time → R6, D10, D11 (stage 1 portion). 8 browser recovery → stage 2.

## Work items

- **W1 developer** implement R1–R36 in `stage-1/` (source + unit tests), Dockerfile, RUN.md. Commit only `stage-1/` minus `stage-1/acceptance/`.
- **W2 tester** black-box suite in `stage-1/acceptance/` (one command against `BASE_URL`), covering every R with an invariant oracle. Commit only that folder.
- **W3 reviewer** clean-clone reproduction of the developer's commit + harness isolated run + walk R1–R36.
- **W4 architect** gap pass, runner `claimed stage: 1`, then product extra (request id + structured log), re-review, post revision.

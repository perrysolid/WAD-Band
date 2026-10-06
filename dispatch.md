# Dispatch

The first human input of the submitted run. It was pasted once, to the architect seat, in a fresh
room. Every later human message is listed, with its reason, in `FACTORY.md` → Limitations.

---

@parthmishra0205/architect This is the job. Build all four stages of the `pocketful` track (a wallet
and payments app), in order, with the band (@parthmishra0205/developer, @parthmishra0205/tester,
@parthmishra0205/reviewer). Do not ask me anything; I will not reply until your final report.

**Inputs**

- Specifications: `/Users/parth/Documents/kickoff/pocketful/spec/stage-1.md` … `stage-4.md`.
  Read each in full and paste the complete text of the current stage into every handoff.
  Later stages say "§5", "§7" etc.; those refer to `stage-1.md`, so stage-1 travels with
  every later handoff too.
- Supplied sample checks: `/Users/parth/Documents/kickoff/pocketful/test/`. They cover only
  part of what is graded (about 79% of stage 1, 35% of stage 2, 9% of stage 3, 16% of
  stage 4). Build to the specification, never to these files. Reading them to learn the
  interface shape is fine; special-casing them disqualifies the entry.
- Acceptance runner, from `/Users/parth/Documents/kickoff` with `. .venv/bin/activate` first:
  `python -m harness run --track pocketful --repo /Users/parth/Documents/band --stage N --mode isolated --out /Users/parth/Documents/band-checks/sN-<attempt>`
  Each `--out` directory must be new. The goal line is `claimed stage: N`. The runner also
  runs the next stage's suite; that line must fail.
- Result repository: `/Users/parth/Documents/band` (branch `main`). Stage N goes in
  `stage-N/`. Stage 1 starts empty. Each later stage starts as a copy of the accepted previous
  folder (with no nested `.git`) and extends it. Each folder must solve its own stage and not
  the next one: a later stage's endpoints, fields and screens must not exist in an earlier
  folder.

**Delivery rules**

- Each folder needs a `Dockerfile` and a `RUN.md`. It must build and serve from a clean
  container with no outbound network at run time, within 2 vCPU and 2 GiB, healthy within
  60 s, with up to 50 requests in flight and no 5xx ever.
- Stage 2 is a browser product. It must be coherent, responsive (375 px and desktop),
  presentation-ready, accessible, and clear in every state the spec names. Every font,
  script and stylesheet ships inside the image.
- Do not touch `mandates/`, `README.md`, `FACTORY.md`, `dispatch.md` or `room.json`.

**Risks the plan must cover.** Each needs a requirement id, a recorded `DECISION` where the
spec leaves a choice, and a test. These are the places money gets created, lost or spent
twice, or where a hidden check lives:

1. Conservation and non-negativity hold under 50 concurrent writers, including transfers in
   opposite directions between the same wallets and many payers draining one wallet.
2. Idempotency: per-user key scope, same body compared as parsed JSON values, concurrent
   first use, a failed first use leaving the key free, a claimed key winning over field
   validation, replays surviving later state changes, and keys surviving export/import.
3. Error precedence on every endpoint: unparseable body, wrong JSON type, authentication,
   missing key, claimed key, field rules in the order the spec lists them, not-found,
   permission, state conflicts, funds. Where the spec is silent, decide once and apply it
   everywhere.
4. Number edge cases: integral floats and exponent forms accepted, fractional values,
   booleans, strings, huge and non-finite values rejected with the specified code; integer
   query parameters accept plain digits only.
5. Password hashing must not block other requests or push a large seeded reset past its
   10-second limit.
6. Export/import must carry everything the spec lists (credentials, tokens, receipts,
   operators, holds, revisions, snapshots, id sequences) so nothing collides or is
   regenerated after an import. Every later stage must import every earlier stage's
   export, so the state format carries its own version from stage 1.
7. Time: one canonical instant form inside, explicit offsets outside, inclusive and
   half-open boundaries exactly as written, expiry evaluated at every read and write
   without a background job.
8. Stage 2 browser recovery: an unchanged resubmission reuses its key, a changed field gets
   a new one, a lost response shows the uncertain state and retries safely, the latest
   refresh wins over a delayed earlier one, and a signed-in browser survives an upgrade.

**Product extras.** Only after a stage's requirements are met and accepted, and only if each
extra changes no specified behaviour, adds no step between a user's click and the specified
action (no confirmation dialogs), and implements nothing from a later stage. The reviewer
checks this. Drop any extra that risks a requirement.

- Every write path checks, before it commits, that the money it moves nets to zero across
  wallets and leaves no available balance below zero, and refuses to commit otherwise. Every
  response carries a request id that also appears in a structured log line.
- Feed items read as sentences ("You paid Bob", "Ada paid you"), with initials avatars, a
  lock or globe marker for privacy, and relative times with the exact time on hover.
- The split preview says who carries the extra minor unit and why, before submitting.
- Amount inputs validate as the user types and show the formatted value they will send.
- A received payment offers "Request again" and a sent one "Pay again", prefilling the forms.
- Theme follows the system light/dark preference; motion respects reduced-motion.
- Stage 3 onward: a History screen on its own route, reachable from navigation, showing the
  statement and the balance as of a chosen date. Stage 4 onward: a refund action on received
  payments in the UI.

**Run order.** Finish each stage completely (reviewer `ACCEPT`, `claimed stage: N` in isolated
mode) before moving to the next. Before starting stage N+1, export state from the accepted
stage-N container and import it into the new build as part of its gate. After each stage,
post the full committed revision in the room. When all four are done, or the band cannot go
further, post `DELIVERED` with the per-stage commit, the runner result, the requirements still
unmet, the rejections and what they changed, and the wall time each stage took.


---

## Second human input: re-dispatch (6 Oct 01:15 IST)

Switching the seats' model restarted them and cleared their context, so the architect stopped
after stage 2 and reported that it could see no further job. This preface was posted, followed by
the unchanged dispatch text above:

> Re-dispatch of the original job (unchanged text below). The seats were restarted after a usage-limit stall and lost the original dispatch, which named all four stages. This restores it; nothing else changes.
>
> Status: stage 1 (3761a81) and stage 2 (6ff2e31) are already accepted and delivered. Continue with stage 3, then stage 4, exactly as the job below says. Time budget: post DELIVERED for whatever is accepted by 08:00 IST (02:30 UTC) at the latest. A stage that is not reviewer-accepted and claiming its stage by then is reported as unmet, not shipped.


## Third human input: product follow-up job (6 Oct 02:10 IST)

After all four stages were delivered, one scoped job upgraded the browser UI in `stage-4/` only.
It was posted unchanged:

> @parthmishra0205/architect Product follow-up dispatch. This is a new, scoped job; the four delivered stages stand. Do not ask me anything; I will not reply until your report.
>
> Job: a product-quality upgrade of the browser UI in /Users/parth/Documents/band/stage-4/ only (branch main), built by the band (@parthmishra0205/developer, @parthmishra0205/tester, @parthmishra0205/reviewer) with the usual gate.
>
> Hard rules
> - stage-1/, stage-2/ and stage-3/ are frozen. Change nothing in them.
> - All stage 1–4 requirements still apply to stage-4/ (specs: /Users/parth/Documents/kickoff/pocketful/spec/stage-1.md … stage-4.md; paste what each seat needs into its handoff). Every specified route, data-testid, behaviour, API response and error stays exactly as it is now. Add no step between a click and a specified action (no confirmation dialogs).
> - Everything ships in the image: no runtime network, no CDN fonts, scripts or styles. Within 2 vCPU / 2 GiB, healthy within 60 s.
> - Gate: the tester extends the UI suite for every new screen and state; the reviewer ACCEPTs the exact commit after a clean --no-cache build, all earlier suites green, and `python -m harness run --track pocketful --repo /Users/parth/Documents/band --stage 4 --mode isolated --out /Users/parth/Documents/band-checks/s4-ui-<attempt>` (from /Users/parth/Documents/kickoff with `. .venv/bin/activate`) printing `claimed stage: 4`, plus a by-hand walk at 375 px and 1280 px in light and dark mode.
>
> Build, in priority order (stop where the time runs out; each item ships only if accepted)
> 1. Visual polish in a calm, trustworthy consumer-finance style: a consistent type scale, spacing and colour system; initials avatars; feed items as sentences from the viewer's point of view ("You paid Bob", "Ada paid you"), note with emoji intact, a lock or globe icon for privacy, relative time with the exact time on hover; clear success toasts; distinct loading, empty, error, held, pending and uncertain states; dark mode that follows the system setting; on narrow screens a bottom tab bar and the pay form first, with the request and hold forms collapsed behind tabs or disclosure (still reachable without extra steps on the specified flows).
> 2. History screen on its own route, linked from the navigation: balance as of a chosen date and time, and a paged statement for a date range showing opening balance, each entry with signed amount and running balance, and closing balance; snapshot paging so pages stay consistent.
> 3. Payment detail view reachable from a feed item: parties, amount, note, privacy, time, and its revision history; a Refund action for the receiver and a Correct action for the sender, with the specified errors shown in plain language.
> 4. Quick actions: "Pay again" on a sent payment and "Request again" on a received one, prefilling the forms; the split preview explains who carries the extra minor unit and why.
>
> Time limit: post DELIVERED by 07:30 IST (02:00 UTC). If stage-4/ is not reviewer-accepted at that time with `claimed stage: 4`, restore stage-4/ to commit 7afca97bb1da391e913b325ac16ca1bd446040f7 exactly, verify it claims stage 4 again, and report the upgrade as unmet. DELIVERED lists the accepted commit, what was built, what was skipped, and the runner result.

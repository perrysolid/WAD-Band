# FACTORY

A four-seat software factory in BAND Desktop. One human message dispatches a job; the
band plans it, builds it, tests it independently, reproduces the evidence, and reports.
No seat accepts its own work.

Submitted run: all four stages delivered, each with a reviewer ACCEPT on its exact
commit and `claimed stage: N` in isolated mode. Seats ran `claude-opus-5-5` (high effort)
until midway through stage 2, then `claude-sonnet-5-5` (medium effort) to stay inside one
subscription's usage limit.

## Seats

| Seat | Harness | Model | Owns | Cannot |
|---|---|---|---|---|
| architect | Claude Code | claude-sonnet-5-5 (medium) | requirements, design, routing, delivery decision | write code or tests |
| developer | Claude Code | claude-sonnet-5-5 (medium) | implementation, container build | accept its own work, weaken tests |
| tester | Claude Code | claude-sonnet-5-5 (medium) | black-box acceptance suite written from the requirements | read or edit the implementation |
| reviewer | Claude Code | claude-sonnet-5-5 (medium) | clean-room reproduction, verdict | edit code or tests |

Mandates: [`mandates/`](mandates/). None of them names this problem; the same files run
any job that has a written specification and a deliverable folder.

## How work flows

```
human ──task──▶ architect ──PLAN (R1..Rn, design)──┬──▶ developer ──HANDOFF(commit, gate output)──┐
                                                   └──▶ tester ────HANDOFF(suite, defects)────────┤
                                                                                                  ▼
                 architect ◀──────────────── VERDICT ACCEPT / REJECT(repro) ◀──────────────── reviewer
                     │  REJECT → routed to code, tests or plan owner (3 strikes → re-plan)
                     └─ ACCEPT → DELIVERED (commit, requirement → evidence table) → next stage
```

Every message starts with a tag (`PLAN`, `DECISION`, `HANDOFF`, `VERDICT`, `BLOCKED`,
`DELIVERED`) so the room log reads as a trace.

## Design choices and why

1. **Separate tester that never reads the code.** The shipped checks are a fraction of
   what is graded. A suite written from the requirements by a seat that cannot see the
   implementation catches what the developer did not think of, and cannot drift toward
   "test what the code does".
2. **Requirements extracted into numbered ids before any code.** Hidden checks live in
   sentences mentioned once: error precedence, ordering, repeat behaviour, boundaries.
   The architect lists them; the reviewer must name evidence for each id.
3. **Reviewer reproduces from a clean build with no network.** The reviewer does not
   trust a pasted log. That is the gate that disqualifies entries, so it is the gate the
   band runs on every handoff.
4. **Invariants are structural.** Mandates require atomic check-and-write and
   repeat-safe writes rather than careful sequencing. Concurrency bugs are cheaper to
   design out than to test out.
5. **Self-contained handoffs.** Seats only see messages addressed to them, so every
   handoff carries the full requirements and the exact commit. Nothing points at
   "the message above".
6. **No human after dispatch.** Ambiguity is resolved by the architect as a recorded
   `DECISION`, never by asking.
7. **Three-strike re-plan.** A work item rejected three times goes back to the architect
   to be split or redesigned. This stops fix loops.
8. **Invariant oracle in every adversarial test.** After each concurrency, retry or
   atomicity test, the tester recomputes the core invariant from the public interface.
   A test that only checks status codes cannot see money that leaked.
9. **Upgrade is part of the gate.** Later stages must read earlier stages' saved state.
   The developer versions the state format from the first job, and the tester exports
   from the previous build and imports into the new one before every handoff.
10. **Gap pass before delivery.** Most of the graded checks are not shipped. When the
    sample checks go green, the architect re-reads the spec line by line for behaviour
    with no evidence and sends it back as work. Green samples are not the finish line.
11. **Overshoot guard.** Each stage folder must solve only its own stage. The reviewer
    probes that the next stage's surface is absent, and that extras change no required
    behaviour.
12. **Track detail lives only in the dispatch.** Mandates say how seats work; the
    dispatch carries the spec paths, the risk list and the optional product extras.
    Pointing the same mandates at another problem means writing a new dispatch, nothing
    else.

## Stand it up

You need: BAND Desktop (0.4.10+), the `band` CLI and the Claude Code `band-peer` plugin,
Claude Code signed in, Docker running, Python 3.12+, and the kickoff package with its
harness venv (`python -m pip install -r harness/requirements.txt`,
`python -m playwright install chromium`).

1. **Result repository.** Clone this repository (or start an empty one with the same
   `README.md`, `FACTORY.md`, `mandates/`, `dispatch.md`). Note its absolute path; every
   seat gets that path, never a relative one. Make sure there are no `stage-N/` folders
   left from a practice run.
2. **Create the four seats.** In BAND Desktop choose **New local agent → Claude Code**
   (headless) four times, named exactly `architect`, `developer`, `tester`, `reviewer`.
   For each: model `claude-sonnet-5-5`, effort medium (set it on the seat's runtime
   template, `band --as <owner>/<seat> runtime template set --runtime-model claude-sonnet-5-5
   --runtime-effort medium --apply-and-restart`; the desktop setting alone did not reach
   running seats); working directory = the result repository's absolute
   path; standing instruction / system prompt = the full text of `mandates/<seat>.md`.
   (Alternative: open four Claude Code terminals in the result repository and run
   `/band-peer:jam as architect`, `... as developer`, `... as tester`, `... as reviewer`,
   then paste each seat its mandate as the first message.) Copy each seat's full handle
   (`@<owner>/<seat>`); the dispatch must use those exact handles.
3. **Permissions.** An unattended run cannot stop for approvals. Allow, for every seat,
   file edits inside the result repository, `git`, `docker` (build, run, network create),
   `python -m harness`, and `curl`; allow a headless browser for tester and reviewer.
   Optionally run seats in a Docker Sandbox (Settings → Experiments → Docker Sandboxes,
   **Direct host workspace** mode, working directory = result repository).
4. **Room.** Create one fresh room, add all four seats, and confirm a two-way
   `@handle` exchange (for example architect → reviewer "ping", reviewer → architect
   "pong"). Gate 2 needs a reply in each direction between two seats.
5. **Git identity.** The mandates make every seat commit with
   `-c user.name=<seat>`; check after the first commits with
   `git log --format='%an %s' | head`.
6. **Dispatch.** Edit [`dispatch.md`](dispatch.md) so the handles, kickoff path, result
   path and checks path are yours. Paste the text below its `---` line to the architect
   seat, once. Send nothing else until the architect posts `DELIVERED`.
7. **Record and check.** Download the room (room `⋮` → Open in Band → `⋮` → Download →
   **Download full session**), save it unchanged as `room.json`, then run
   `python -m harness check <result repo> --track pocketful` and
   `python -m harness run --repo <result repo> --all --mode isolated` from a fresh clone.

## Measured cost and time

All times IST (UTC+5:30). Dispatch was posted 4 Oct 22:05. Wall times include the stalls
listed under Limitations, so they overstate the band's working time.

| Stage | Wall time (dispatch/plan → DELIVERED) | Active work (approx.) | Rejections that changed the code | Runner result (isolated) |
|---|---|---|---|---|
| 1 | 4 Oct 22:05 → 5 Oct 20:10 (final accept `3761a81`) | ~2.5 h | 3 reviewer REJECTs (`e7d2b7d`, `bbae7cd`, `91d13b9`) | stage 1 pass 147/147, stage 2 fail, `claimed stage: 1` |
| 2 | plan 5 Oct 03:44 → DELIVERED 6 Oct 01:02 (`6ff2e31`) | ~1.5 h | 0 on the build; 4 test defects found and fixed by the tester | stage 1 147/147, stage 2 35/35, stage 3 fail, `claimed stage: 2` |
| 3 | re-dispatch 6 Oct 01:15 → ACCEPT `ed83c7f` ~01:45 | ~0.5 h | 1 (statement tie order used numeric-aware id comparison; spec says id ascending) | stages 1–3 pass, stage 4 fail, `claimed stage: 3` |
| 4 | 01:22 (started in parallel with stage-3 review) → DELIVERED 01:49 (`7afca97`) | ~0.5 h | 2 (same tie order; batch revisions missing their batch id, and losing it across export/import) | stages 1–4 pass, `claimed stage: 4` |
| 4 · UI upgrade | dispatch 6 Oct 02:10 → DELIVERED 06:37 (`c4cf6a7`), stalled 03:30–06:00 on the usage limit and a network outage | ~2 h | 2 (dropped first click and stray "undefined" on History; stray "null"/"false" and wiped outcome messages on payment detail) | stages 1–4 pass, `claimed stage: 4` |

**Spend.** Band's local usage estimate at list prices (`band usage rooms`, not a bill) is about
$470 for every local session on this machine across the event. That includes the practice
rooms and the Opus sessions in this room, which were recorded before Band attributed them to
a room. The submitted room shows $52.84 attributed. Stage 1 and the first half of stage 2
ran on `claude-opus-5-5` at high effort. From 5 Oct ~20:00 every seat ran `claude-sonnet-5-5`
at medium effort, to stay inside the shared subscription's usage limit.

**Volume (submitted room).** 118 messages: architect 47, developer 29, tester 20, reviewer 14,
human 8 (listed below). 49 handoffs. Commits by seat: developer 26, tester 18, architect 6.
Stage 3 and stage 4 ran on Sonnet in under an hour together: the architect started stage 4
in parallel with stage 3's review and recorded it, and each stage still shipped only after
its own reviewer ACCEPT.

## How it caught bad work

- **Money destroyed near the arithmetic limit (stage 1, reviewer REJECT `e7d2b7d`).** Every suite
  was green: the tester's 663 tests and the shipped checks at 147/147. The reviewer's by-hand
  boundary probe still found a wallet seeded at 2^53 that silently lost one minor unit on a
  1-unit payment. `balance + amount` was a float, and 2^53+1 rounded to 2^53. With amount 2,
  the developer's pre-commit invariant guard refused the write, but it answered 500. The
  architect routed it as a code defect with a new decision (D39: a credit that would exceed
  2^53 is 422 at the funds step). The tester added tests for it to both stages. The developer
  fixed it test-first with exact integer comparisons.
- **Import accepted out-of-range state (same REJECT).** A crafted export could import values
  the service could never produce. Decision D40 bounded import, and the fix came back in
  `ba7ca1f`.
- **Logging extra broke the one-line-per-request contract (REJECTs `bbae7cd`, `91d13b9`).** A client
  aborting mid-body, or pipelined garbage, produced zero or two access-log lines. Two rounds
  of review narrowed it, and `3761a81` was accepted.
- **Defence in depth worked as designed.** The 2^53+2 case shows the second line: the structural
  check was wrong, and the pre-commit conservation guard still refused to corrupt state.
- **Stage 3 and 4: the reviewer read the spec, not the suite.** Statement entries that share a
  timestamp must sort by payment id ascending. The implementation used a numeric-aware
  comparison, so `p_10` sorted after `p_9`. Every suite passed, because the tester's oracle
  made the same assumption. The reviewer rejected it, and the developer fixed both stage
  folders. The tester also fixed its oracle (`08aadc3`), so the defect cannot hide in the
  tests again. In stage 4 the reviewer also rejected batch-created revisions that did not
  expose their batch id, and checked that the id survives export and import.
- **UI upgrade: the reviewer used the product like a person.** With every suite green, the
  reviewer's by-hand walk found that the first click on History was silently dropped while the
  wallet was still loading, and that an error left the word "undefined" on screen. The tester
  then found "null" and "false" text nodes on the payment detail page, and success messages
  wiped by the post-action reload. Two REJECTs; each defect came back as a failing test first
  (`48dcd18`, `cb42744`), then a fix (`eb14351`, `c4cf6a7`).
- **Tests are checked too.** At stage 1 the architect routed 3 of 6 failures back to the tester
  as test defects (empty bearer sent through an HTTP client that normalises it, among others).
  At stage 2 the tester traced all 4 failures against `6ff2e31` to its own tests and fixed them,
  without touching the implementation. The architect also withdrew a reviewer-raised item
  (D40-1) as a test defect, with the reasoning recorded: JSON parsing maps 2^53+1 to 2^53, which
  is a legal value.

## What we tried that failed

- **A first factory pointed at the other track.** We switched tracks before the submitted run.
  The mandates did not change, because they name no track; only the dispatch was rewritten.
- **Four Opus seats on one subscription.** They used up a 5-hour usage window in under an hour
  of work, and stalled twice. Moving every seat to Sonnet at medium effort fixed the stalls
  without changing a mandate's behaviour.
- **Changing a seat's model in the desktop UI** did not reach the running seats. It only took
  effect through the seat's runtime template (`band runtime template set --runtime-model …
  --apply-and-restart`). The restart cleared the seats' conversation context, so the architect
  no longer saw the original four-stage dispatch and stopped after stage 2 (see Limitations).

## Limitations

**Human messages in the submitted room**, all listed here. The first is the dispatch. Five are
operational restarts with no task content. One re-sends the original dispatch, and one is a
separate product follow-up job given after all four stages were delivered.

| Time (IST) | Message | Why |
|---|---|---|
| 4 Oct 22:05 | The dispatch ([`dispatch.md`](dispatch.md)) | the job |
| 5 Oct 03:37 | "Operational notice only: your model session limit has reset. Resume …" to reviewer | the reviewer hit the subscription limit at 22:39 mid-review and did not resume after the reset |
| 5 Oct 04:26 | "Operational notice only: Band connection dropped … Resume …" to developer | a local network outage disconnected three seats |
| 5 Oct 14:39 | "continue" to developer | same outage. The seats stayed disconnected until the Band service was restarted at 14:40 |
| 6 Oct 00:58 | "Operational notice only: the model usage limit interrupted your turns … Resume …" | second usage-limit stall, 20:54 → 00:53 |
| 6 Oct 01:15 | Re-send of the unchanged dispatch, with a status line and a time budget | switching models restarted the seats and cleared their context, and the architect reported it could not see a further job |
| 6 Oct 02:10 | A scoped product follow-up job: upgrade the `stage-4/` browser UI (polish, History, payment detail with refund/correct, quick actions), with `stage-1/`–`stage-3/` frozen, the same gate, and a restore-to-`7afca97` fallback | a new job after all four stages were delivered; it is in [`dispatch.md`](dispatch.md) |
| 6 Oct 06:03 | "Operational notice only: the usage limit interrupted your turns … and a network outage just ended. Resume …" | third usage-limit stall, 03:30 → 05:50, followed by a network outage |

None of these messages carried requirements, hints, approvals or fixes. Every technical
decision in the room was made by the seats.

**Other limitations**
- The shipped checks cover only part of the graded suite (stage 3 ≈ 9%, stage 4 ≈ 16%).
  Our evidence beyond them is the tester's independent suites and the reviewer's probes.
- Only `stage-4/` has the upgraded UI (History, payment detail, refund and correct, quick
  actions, dark mode). `stage-2/` and `stage-3/` keep the stage-2 UI as it was accepted, because
  the follow-up job froze them to protect the chain.
- The stage-1 folder carries the developer's product extras (request id header, structured
  access log, pre-commit invariant guard). The reviewer verified they change no response body.
- State lives in memory. A container restart loses it, which the spec allows.

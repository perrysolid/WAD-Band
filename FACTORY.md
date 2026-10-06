# FACTORY

> Four Claude Code agents in one BAND room built a Venmo-style wallet through
> all four stages. Nothing shipped until a seat that didn't write it had reproduced it from
> scratch, and that rule caught money being silently destroyed while every test was green.

**Result.** Every stage folder claims its stage in isolated mode. Each stage
was accepted by the reviewer on its exact commit. Stage 4 also got a product-grade UI.

---

## Why This approach

Pocketful has one hard rule: money is never created, destroyed or spent twice. A single
agent that writes the code and then writes the tests for it will test what it built. It
won't test what the spec asked for.
So I designed the factory around one rule: **no seat accepts its own work**. Everything
else follows from it:

- The **tester** never sees the code. It writes its suite from the spec alone, so it can't
  drift into "test what the code does".
- The **reviewer** trusts nobody's logs. It clones the exact commit, builds with no
  network, runs every suite itself, and pokes at the edges by hand.
- The **architect** writes no code. It turns the spec into numbered requirements and
  decides where each rejection goes.
- The **developer** builds. It is the only seat that writes product code, and it can't
  approve its own handoff.

So in short all these 4 agents, conversate and interact, then intricate the best possible solution and code possible.

The mandates in [`mandates/`](mandates/) describe how each seat works, not what it builds.
They never mention wallets, endpoints or error codes. 
Everything about pocketful lives in
the dispatch ([`dispatch.md`](dispatch.md)). To point this factory at another problem, you
write a new dispatch and change nothing else.

## The seats

| Seat | Harness | Model | Owns | Is not allowed to |
|---|---|---|---|---|
| architect | Claude Code | `claude-opus-5-5` | requirements, design, routing, the delivery decision | write code or tests |
| developer | Claude Code | `claude-opus-5-5`| implementation, the container build | accept its own work, or weaken a test |
| tester | Claude Code | `claude-opus-5-5`| a black-box suite written from the requirements | read or edit the implementation |
| reviewer | Claude Code | `claude-opus-5-5`| clean-room reproduction and the verdict | edit code or tests |

\* Stage 1 and the first half of stage 2 ran on Opus at high effort. All four seats on Opus
burned through one subscription's 5-hour window in under an hour of work. So from
midway through stage 2 I moved every seat to Sonnet at medium effort, and changed nothing
else. Sonnet then delivered stages 3 and 4 in under an hour together. Both runs are in
the same room log.

## How work flows

```
human ──task──▶ architect ──PLAN (R1..Rn, design)──┬──▶ developer ──HANDOFF(commit, gate output)──┐
                                                   └──▶ tester ────HANDOFF(suite, defects)────────┤
                                                                                                  ▼
                 architect ◀──────────────── VERDICT ACCEPT / REJECT(repro) ◀──────────────── reviewer
                     │  REJECT → routed to code, tests or plan owner (3 strikes → re-plan)
                     └─ ACCEPT → DELIVERED (commit, requirement → evidence table) → next stage
```

Every message starts with a tag: `PLAN`, `DECISION`, `HANDOFF`, `VERDICT`, `BLOCKED` or
`DELIVERED`. That lets you read `room.json` like a trace instead of a chat.

## Design choices, and what each one cost

1. **A tester that never reads the code.** *Cost:* the tester sometimes writes a wrong
   test, and the band has to spend time on it. That happened 8 times across stages 1 and 2,
   and each time the architect routed the failure back to the tester instead of the
   developer. I consider that the system working.
2. **Numbered requirements before any code.** Hidden checks live in sentences the spec
   says only once: error precedence, ordering, retries, boundaries. The architect lists them
   as R1…Rn, and the reviewer has to name evidence for each id. *Cost:* stage 1's plan
   took about 10 minutes before a line of code existed.
3. **The reviewer rebuilds from a clean clone with no network, every time.** A service that
   won't start in isolation scores zero, so the band runs that gate on every handoff, not
   just at the end. *Cost:* the slowest step in every loop, roughly 5–10 minutes per review.
4. **Make the invariant structural, then check it again.** Every write is atomic and safe
   to repeat. Separately, just before any write commits, the developer's code checks that
   the money moved nets to zero and that no balance goes negative. If either fails, it
   refuses the write. This second line is what saved us in stage 1 (see below).
5. **Self-contained handoffs.** Seats only see messages addressed to them, so every
   handoff carries the full requirements and the exact commit hash. *Cost:* long messages,
   with the stage-2 plan split into 8 parts. This turned out to matter more than I
   expected: when seats restarted and lost their memory, the handoffs still carried
   everything they needed.
6. **No human after dispatch.** The architect settles ambiguity with a recorded `DECISION`
   (D1…D40 by stage 4) and never asks me.
7. **Three strikes and the work item is re-planned**, so a fix can't loop forever.
8. **Invariant check in every adversarial test.** After each concurrency, retry or
   atomicity test, the tester recomputes totals from the public API. A test that only checks
   status codes can't see money leaking.
9. **Upgrades are part of the gate.** Every stage must import every earlier stage's
   export, so the saved state carried a format version from stage 1.
10. **A gap pass before delivery.** When the sample checks go green, the architect
    re-reads the spec line by line, looking for behaviour that has no evidence yet.
11. **Overshoot guard.** A stage folder that also solves the next stage claims nothing, so
    the reviewer checks that the next stage's endpoints are absent.

## How it caught bad work

This is the section I'm proudest of, because none of these bugs were caught by green tests.

**1. Money silently destroyed, with every suite green (stage 1).** The tester's 663 tests
passed, and so did the 147/147 shipped checks. The reviewer still probed the arithmetic
boundary by hand. It seeded a wallet at 2^53 and sent 1 unit to it, and one minor unit
vanished. `balance + amount` was a float, and 2^53+1 rounds to 2^53. With an amount of 2,
the pre-commit guard from design choice 4 *did* refuse the write, but it answered 500.
The reviewer rejected `e7d2b7d`. The architect recorded decision D39 (a credit that would
exceed 2^53 is a 422 at the funds step), the tester added tests in both stages, and the
developer fixed it test-first with exact integer comparisons. This is exactly the failure
the track warns about, and only an independent reviewer found it.

**2. Import accepted state the service could never produce (same rejection).** The fix for
decision D40 bounded every imported value (`ba7ca1f`).

**3. The logging extra broke its own contract (two more stage-1 rejections).** A client
aborting mid-request produced zero or two access-log lines instead of exactly one. Two review
rounds narrowed it down, and `3761a81` was accepted.

**4. The tester's own oracle was wrong too (stages 3 and 4).** Statement entries with the
same timestamp must sort by payment id. The code sorted `p_10` after `p_9`, and every suite
passed, because the tester's oracle made the same assumption. The reviewer went back to the
spec text and rejected it. The developer fixed both folders, and the tester fixed its oracle
(`08aadc3`) so the same mistake can't hide in the tests again.

**5. The UI, used like a person would (stage-4 UI upgrade).** With every suite green, the
reviewer's by-hand walk found that the first click on History was silently dropped while the
wallet was loading, and an error left the word "undefined" on screen. The tester found "null"
and "false" showing as text on the payment page, and success messages wiped out by the
page refresh. Each one came back as a failing test first (`48dcd18`, `cb42744`), then a fix
(`eb14351`, `c4cf6a7`).

**6. Not every failure is the code's fault.** At stage 1, three of six failures were test
bugs, and the architect routed them back to the tester. At stage 2, all four failures were
test bugs, and the tester fixed them without touching the implementation. The architect also
withdrew one reviewer item with recorded reasoning: JSON parsing maps 2^53+1 to 2^53, which is
a legal value, so that one wasn't a defect.

## Measured cost and time

All times are IST. The wall times include the stalls under *Limitations*, so the active
work column is closer to the band's real effort.

| Stage | Wall time | Active work (approx.) | Rejections that changed the code | Isolated runner |
|---|---|---|---|---|
| 1 | 4 Oct 22:05 → 5 Oct 20:10 (`3761a81`) | ~2.5 h | 3 (`e7d2b7d`, `bbae7cd`, `91d13b9`) | 147/147, `claimed stage: 1` |
| 2 | 5 Oct 03:44 → 6 Oct 01:02 (`6ff2e31`) | ~1.5 h | 0 on the code; 4 test defects fixed by the tester | stage 2 35/35, stage 1 147/147, `claimed stage: 2` |
| 3 | 6 Oct 01:15 → ~01:45 (`ed83c7f`) | ~0.5 h | 1 (tie order) | stages 1–3 pass, `claimed stage: 3` |
| 4 | 01:22 → 01:49 (`7afca97`), started in parallel with stage 3's review | ~0.5 h | 2 (tie order; batch revisions missing their batch id across export/import) | stages 1–4 pass, `claimed stage: 4` |
| 4 · UI upgrade | 02:10 → 06:37 (`c4cf6a7`), stalled 03:30–06:00 | ~2 h | 2 (History and payment-detail defects) | stages 1–4 pass, `claimed stage: 4` |

**Spend.** Band's usage estimate at list prices (`band usage rooms`, not a bill) is about
$470 across every local session I ran during the event, practice rooms included. Most of it
is the Opus period. The submitted room shows $52.84 attributed to it. The Sonnet half did
stages 3, 4 and the UI upgrade for a fraction of what stage 1 cost.

**Volume.** The room log has 118 text messages: architect 47, developer 29, tester 20,
reviewer 14, and me 8 (all listed below). There were 49 handoffs. Commits by seat:
developer 26, tester 18, architect 6. The work really was spread across the seats; none of
them carried it alone.

## What we tried that failed, and what I learned

- **Four Opus seats on one subscription.** It was the best quality, but they used up the
  5-hour window in under an hour of work and stalled twice. Sonnet at medium effort kept the
  same discipline (the reviewer still rejected real defects) at a pace the budget could sustain.
  Next time I'd give the reviewer a separate credential, because it's the seat you least want
  to stall.
- **Changing the model in the desktop UI didn't reach the running seats.** It only took
  effect through the seat's runtime template, and the restart wiped the seats' conversation
  memory. The architect then honestly reported that it could see no further job, and stopped
  after stage 2. Self-contained handoffs (design choice 5) meant everything restarted cleanly
  once the job was re-sent. Lesson: keep the job's source of truth somewhere a seat can re-read
  it, not only in its memory.


## Limitations, honestly


| Time (IST) | Message | Why |
|---|---|---|
| 4 Oct 22:05 | The dispatch ([`dispatch.md`](dispatch.md)) | the job |
| 5 Oct 03:37 | "Operational notice only: your model session limit has reset. Resume …" | the reviewer hit the usage limit mid-review at 22:39 and didn't wake after the reset |
| 5 Oct 04:26 | "Operational notice only: Band connection dropped … Resume …" | a network outage disconnected three seats |
| 5 Oct 14:39 | "continue" | the same outage; the seats only came back after the Band service restarted at 14:40 |
| 6 Oct 00:58 | "Operational notice only: the model usage limit interrupted your turns … Resume …" | second usage-limit stall, 20:54 → 00:53 |
| 6 Oct 01:15 | The unchanged dispatch, re-sent with a status line and a time budget | the model switch restarted the seats and wiped their memory of the job |
| 6 Oct 02:10 | A scoped UI-upgrade job for `stage-4/` only, with stages 1–3 frozen, the same gate, and a fallback to `7afca97` | a new product job after delivery; the full text is in [`dispatch.md`](dispatch.md) |
| 6 Oct 06:03 | "Operational notice only: … Resume …" | third usage-limit stall, 03:30 → 05:50, then another network outage |

None of these messages carried requirements, hints, approvals or fixes. Every technical
decision in the room was made by the seats.

**Other limitations**
- The shipped checks are only part of the graded suite. Beyond them, our evidence is the
  tester's independent suites and the reviewer's probes, and those may still miss something
  the judges test.
- Only `stage-4/` has the upgraded UI. `stage-2/` and `stage-3/` keep the stage-2 UI exactly
  as it was accepted, because I froze them to protect the chain.
- State lives in memory, so a container restart loses it. The spec allows this.

## Stand it up yourself

You need BAND Desktop (0.4.10+) with the `band` CLI and the Claude Code `band-peer`
plugin, Claude Code signed in, Docker running, Python 3.12+, and the kickoff package with
its harness venv (`python -m pip install -r harness/requirements.txt`,
`python -m playwright install chromium`).

1. **Result repository.** Clone this repo, or start an empty one with the same `README.md`,
   `FACTORY.md`, `mandates/` and `dispatch.md`. Give every seat its **absolute** path.
   Remove any `stage-N/` folders left over from practice.
2. **Create four seats.** In BAND Desktop, choose **New local agent → Claude Code**
   (headless), named exactly `architect`, `developer`, `tester`, `reviewer`. For each seat:
   - **Working directory:** the result repo.
   - **Role:** choose the matching file in `mandates/`.
   - **Model:** set it on the seat's runtime template, because the desktop setting alone
     didn't reach running seats for us:
     `band --as <owner>/<seat> runtime template set --runtime-model <model> --runtime-effort <effort> --apply-and-restart`.
     Pick Opus if your budget allows it and Sonnet if it doesn't.
3. **Permissions.** An unattended run can't stop for approvals. Allow file edits in the repo,
   `git`, `docker`, `curl` and `python -m harness` for every seat, plus a headless browser for
   the tester and reviewer.
4. **Room.** Create one fresh room, add all four seats, and check that two seats can
   `@handle` each other in both directions.
5. **Git identity.** The mandates make each seat commit under its own name. Check with
   `git log --format='%an %s' | head`.
6. **Dispatch.** Edit the handles and paths in [`dispatch.md`](dispatch.md). Paste the text
   below its `---` line to the architect once, then stay out of the room.
7. **If a seat goes quiet**, check `band list`. "Reconnecting" means restart the Band
   service. A usage-limit error means wait for the reset, then send one "operational notice
   only" resume line, and log it the way we did above.
8. **Record and check.** Download the room (room `⋮` → Open in Band → `⋮` → Download →
   **Download full session**, after scrolling to the very first message). Save it unchanged as
   `room.json`, then run `python -m harness check <repo> --track pocketful` and
   `python -m harness run --repo <repo> --all --mode isolated` from a fresh clone.

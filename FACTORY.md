# FACTORY

A four-seat software factory in BAND Desktop. One human message dispatches a job; the
band plans it, builds it, tests it independently, reproduces the evidence, and reports.
No seat accepts its own work.

## Seats

| Seat | Harness | Model | Owns | Cannot |
|---|---|---|---|---|
| architect | Claude Code | claude-opus-5-5 | requirements, design, routing, delivery decision | write code or tests |
| developer | Claude Code | claude-opus-5-5 | implementation, container build | accept its own work, weaken tests |
| tester | Claude Code | claude-opus-5-5 | black-box acceptance suite written from the requirements | read or edit the implementation |
| reviewer | Claude Code | claude-opus-5-5 | clean-room reproduction, verdict | edit code or tests |

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
   For each: model `claude-opus-5-5`; working directory = the result repository's absolute
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

| Stage | Wall time | Model spend | Handoffs | Rejections | Runner result (isolated) |
|---|---|---|---|---|---|
| 1 | TODO | TODO | TODO | TODO | TODO |
| 2 | TODO | TODO | TODO | TODO | TODO |
| 3 | TODO | TODO | TODO | TODO | TODO |
| 4 | TODO | TODO | TODO | TODO | TODO |

## How it caught bad work

TODO, from the submitted room log: each rejection, what it found, and how the fix came back.

## What we tried that failed

TODO, from the practice runs.

## Limitations

TODO

# CentSentry: Green Tests Aren't Proof

*Team BitStorm · Parth Mishra*

Entry for the WeAreDevelopers x BAND *Dark Factory* hackathon, **pocketful** track: a
wallet and payments service (send, request, split, settle, hold and capture, correct,
refund) built by **CentSentry**, a four-seat agent factory in BAND Desktop where no agent accepts its own work. Money is never created,
destroyed or spent twice, under concurrent transfers, retries and rounding.

| Path | What it is |
|---|---|
| [`FACTORY.md`](FACTORY.md) | The factory: seats, flow, design choices, costs, failure handling |
| [`mandates/`](mandates/) | One generic standing instruction per seat |
| [`dispatch.md`](dispatch.md) | Every task-carrying human message of the submitted run (all human messages are listed in `FACTORY.md` → Limitations) |
| `room.json` | The full BAND Desktop room log of that run |
| `stage-1/` … `stage-4/` | One complete, buildable service per stage (`Dockerfile` + `RUN.md` in each) |

**Result:** every folder claims its stage in isolated mode (`harness run --all --mode isolated`).
`stage-4/` is the full product: send, request, split, settle, hold and capture, corrections,
statements and history, refunds and batch corrections, with the upgraded browser UI.

Try it: `docker build -t pocketful stage-4 && docker run -p 8080:8080 pocketful`, open
<http://localhost:8080/signup>, and follow `stage-4/RUN.md`.

Every file under `stage-N/` was written by the band. Its history is in this repository's
commits and in `room.json`.

Team BitStorm: Parth Mishra ([@perrysolid](https://github.com/perrysolid))

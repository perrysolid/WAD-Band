# WAD-Band — Pocketful Dark Factory

Entry for the WeAreDevelopers x BAND *Dark Factory* hackathon, **pocketful** track: a
wallet and payments service (send, request, split, settle, hold and capture, correct,
refund) built by a four-seat agent factory in BAND Desktop. Money is never created,
destroyed or spent twice, under concurrent transfers, retries and rounding.

| Path | What it is |
|---|---|
| [`FACTORY.md`](FACTORY.md) | The factory: seats, flow, design choices, costs, failure handling |
| [`mandates/`](mandates/) | One generic standing instruction per seat |
| [`dispatch.md`](dispatch.md) | The only human input of the submitted run |
| `room.json` | The full BAND Desktop room log of that run |
| `stage-1/` … `stage-4/` | One complete, buildable service per stage (`Dockerfile` + `RUN.md` in each) |

Every file under `stage-N/` was written by the band. Its history is in this repository's
commits and in `room.json`.

Team: Parth Mishra ([@perrysolid](https://github.com/perrysolid))

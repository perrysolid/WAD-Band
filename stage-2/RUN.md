# Pocketful — stage 2

HTTP service and web UI for payments, requests, splits, the activity feed, atomic net
settlements and payment authorizations (holds and captures). Node.js 22, no third-party dependencies, all state in memory.

## Build and start

```sh
docker build -t pocketful-stage-2 stage-2
docker run --rm -p 8080:8080 -e PORT=8080 pocketful-stage-2
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and needs no outbound
network at run time (`--network none` works). `GET /health` returns
`{"status":"ok"}` within a second of start.

Seed it with `POST /_test/reset` (fixture body), then sign in with
`POST /auth/login`.

## Tests

Unit tests (run on the host, Node.js 22+; they start the server in-process):

```sh
cd stage-2 && node --test test/*.test.js
```

The tester's black-box suite lives in `stage-2/acceptance/` (see its README).

## Web UI

Open `http://localhost:8080/` (it redirects to `/login`). All HTML, JS, CSS and the Inter
font (SIL OFL, `public/fonts/`) are served from the image under `/assets/`; nothing is
fetched from the network.

## State upgrade

`GET /_test/export` writes `state.schema_version` 2. `POST /_test/import` also accepts
a stage-1 export (schema version 1).

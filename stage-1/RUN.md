# Pocketful — stage 1

HTTP service for payments, requests, splits, the activity feed and atomic net
settlements. Node.js 22, no third-party dependencies, all state in memory.

## Build and start

```sh
docker build -t pocketful-stage-1 stage-1
docker run --rm -p 8080:8080 -e PORT=8080 pocketful-stage-1
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and needs no outbound
network at run time (`--network none` works). `GET /health` returns
`{"status":"ok"}` within a second of start.

Seed it with `POST /_test/reset` (fixture body), then sign in with
`POST /auth/login`.

## Tests

Unit tests (run on the host, Node.js 22+; they start the server in-process):

```sh
cd stage-1 && node --test test/*.test.js
```

The tester's black-box suite lives in `stage-1/acceptance/` (see its README).

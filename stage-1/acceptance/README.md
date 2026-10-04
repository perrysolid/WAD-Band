# Stage 1 acceptance (tester seat)

One command against any running build (each test resets the service itself):

```sh
BASE_URL=http://127.0.0.1:8080 /Users/parth/Documents/kickoff/.venv/bin/python -m pytest stage-1/acceptance -q -p no:cacheprovider
```

`--base-url http://...` works instead of `BASE_URL`. Only `pytest` and `httpx` are needed.
The suite lives in [`tester/`](tester/); its README has the requirement → test map (R1–R36).

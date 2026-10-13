# Backend tests

Run with locked development dependencies from `backend/`:

```sh
uv run --locked --extra dev pytest -q
uv run --locked --extra dev pytest -q -n 4 --dist loadfile
```

The second command runs four isolated worker processes. `loadfile` keeps cases from each module together. The migrated database template is created per worker, and each test receives its own database copy and system keys. Serial execution remains available for diagnosis and timing comparisons.

Test data and scripted clients belong in `tests/helpers/`; test modules should import them there rather than importing another test module. Keep scenario-specific overrides at the call site.

`fast_recovery_polling` is opt-in for scripted Docker/Podman/Kubernetes/Portainer recovery tests. It skips delays between observations while retaining event-loop yields. Health-check scheduler tests use a virtual clock. Tests of real deadlines, cancellation, native runtime acceptance and subprocess behavior retain their required timing semantics.

For agent runs, create a unique directory under `/data/temp`, set `TMPDIR`, `TMP`, and `TEMP`, and set pytest's `--basetemp` to a child of that directory. Remove task-owned temporary files after all workers finish.

# Isolated runtime acceptance (developer checks)

The normal backend test suite skips `test_real_dind_acceptance.py` unless
`RT_RUN_REAL_DIND_TESTS=1` is explicitly set. Privileged runtime checks must not
be added implicitly to pull-request or regular push jobs.

## Local DinD mTLS check

From `backend/`, on an isolated Linux test host with rootful Podman and a cached
`data.forgejo.org/oci/docker:dind` image:

```sh
RT_RUN_REAL_DIND_TESTS=1 timeout 420 .venv/bin/python -m pytest -q \
  tests/test_real_dind_acceptance.py -o faulthandler_timeout=0
```

The test generates new synthetic CA/server/client material, uses a unique
`rt-owned-mtls-*` DinD container and loopback ephemeral TLS port, and owns all
workloads. It tests CA/DNS rejection, deployment, immutable snapshot recovery,
environment preservation and certificate cleanup. It never mounts a host engine
socket, accesses an existing certificate, or reads a production database.

The 300-second test deadline and 420-second process deadline remain in force.
Disabling pytest's 20-second diagnostic stack dump only for this slow acceptance
run does not disable `pytest-timeout`, SDK request budgets or cleanup. The test
closes adapters and removes its exact container name/volumes in `finally`; do
not replace that with a global prune of an existing engine. Abrupt process or
host termination can prevent `finally`; inspect only the owned `rt-owned-mtls-*`
fixture in that case. GitHub-hosted runner disposal is the final containment.

## Local Portainer grouped Stack check

From `backend/` on an isolated rootful Podman test host:

```sh
RT_RUN_REAL_PORTAINER_TESTS=1 timeout 600 .venv/bin/python -m pytest -q \
  tests/test_real_portainer_acceptance.py -o faulthandler_timeout=0
```

Default suite execution skips this test. `real_portainer_fixture.py` owns one
unique `rt-owned-portainer-*` DinD pinned by digest. It exposes only an ephemeral
loopback Portainer HTTP port, not a Docker TCP port. Portainer 2.45.1 runs nested
inside that engine, with its **own engine's** socket. No host volume/socket,
existing endpoint or stored database is accepted by the fixture. It pulls the
fixed Portainer digest plus the two public Alpine workload tags; external mirrors
are prerequisites. The fresh Portainer gets a random synthetic admin password
and API key that are not printed. HTTP and setup-token bypass are intentional
for this disposable loopback fixture, never production defaults.

The test waits for native service inventory before snapshot capture, checks
current-images display does not mutate stored target configuration, swaps two
services and compares actual native image IDs, then restores the original Compose
and immutable evidence with production validation still strict. The 420-second
test and 600-second process deadlines bound the run; individual pulls are at most
180 seconds. `finally` removes the exact owned engine and its anonymous volumes
and verifies it no longer exists. This removes the entire nested Portainer data,
workloads and keys; it does not perform a global prune. Hard process termination
can still prevent cleanup, so disposable runner containment remains required.

## GitHub Actions

After these changes have been reviewed and committed, manually dispatch
`.github/workflows/runtime-acceptance.yml`. It has no push, pull-request, schedule
or automatic downstream trigger, only `workflow_dispatch`.

- `dind` (default true): fresh `ubuntu-24.04` GitHub-hosted runner, rootful Podman,
  a digest-pinned DinD image tagged to the fixture's existing cache name, locked
  project dependencies and the same opt-in test. No production secrets are
  supplied; checkout does not persist its token. This is privileged on its own
  disposable runner, not a security sandbox for hostile code: run only trusted
  reviewed refs.
- `portainer` (default false): independent fresh runner and the nested fixture
  above, with a separately enabled `RT_RUN_REAL_PORTAINER_TESTS=1` test. No
  existing Portainer endpoint/password can be passed in as workflow inputs.
- `public_registry` (default false): independent anonymous read-only probe of
  `reg.aoodc.com/fawney19/aether`. It does not read stored credentials/config or
  deploy. Tag/Manifest outages fail the job rather than being treated as passed.
  The 10-second request, at most 5-second Blob and 240-second observation budgets
  stay unchanged.

This workflow has local syntax/contract checks but has **not been dispatched or
verified on GitHub** from this session. Pinning and job isolation do not establish
that the remote runner/network is available. The public image mirrors are also
external dependencies. Existing ordinary CI remains unchanged.

Portainer now has a repository-contained acceptance fixture; this is not the
production Portainer browser-to-backend workflow. Kubernetes production writes
and original business Docker endpoint checks remain outside this workflow; no
production endpoint is silently enrolled.

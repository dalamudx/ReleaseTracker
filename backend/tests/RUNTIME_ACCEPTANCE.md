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

## Container configuration fidelity via an owned Unix socket

```sh
RT_RUN_REAL_DIND_TESTS=1 timeout 360 .venv/bin/python -m pytest -q \
  tests/test_real_container_fidelity.py -o faulthandler_timeout=0
```

This opt-in 300-second check starts a separate `rt-owned-fidelity-*` DinD,
exposing **only its new Unix socket** through a private temporary directory.
Its cgroup-v2 delegation is scoped to the fixture's private namespace, not host
business cgroups. It never mounts a host business engine socket or loads stored
runtime credentials/databases. The existing DinD cache image is required.

Two new NGINX cases check: a locally built image introducing an unreviewed ENV is
refused without stopping its old container; and explicitly compatible NGINX
update followed by JSON-roundtripped immutable-image recovery preserves read-only
root, exact dual bind sources/modes, anonymous-volume identity and proof data,
resolved published port, DNS options, groups, capabilities, resource limits,
stdio and stop timeout. Native SDK proxy settings must not inject ENV. The local
negative image is prebuilt, so its uniquely named tag alone uses a test pull
facade; image inspection, container state and rejection are real. Positive
image pulls and all socket writes/readbacks are real. Other tags are untouched.

A target image adding default ENV/labels/exposed ports/anonymous volumes or filling
previously empty startup/health settings is currently rejected before stop; this
is a conservative image-only policy, not an image-defaults merge/review feature.
Unknown preservation options are refused, not silently omitted. Real Docker
readback is compared semantically, with image/managed markers and native IDs
excluded and missing `ReadOnly` equivalent to false. Mounted application data is
not backed up; the test verifies existing volume reuse, not data snapshots.

The fixture removes its exact owned engine and anonymous volumes in `finally`
and checks its absence. Abrupt process termination can prevent cleanup;
never replace it with global pruning. Podman/Portainer live page acceptance is
separate; this test does not claim those runtimes passed a new live update.

## Native Podman fidelity via a private storage root

```sh
RT_RUN_REAL_PODMAN_TESTS=1 timeout 360 .venv/bin/python -m pytest -q \
  tests/test_real_podman_fidelity.py -o faulthandler_timeout=0
```

Opt-in rootful Podman 5.x/Linux acceptance owns new private `--root`, `--runroot`,
VFS storage and Unix `system service` socket. It does not connect the host engine
or read business configuration/credentials. All CLI setup, image pulls, SDK
updates and immutable JSON-snapshot recovery are real, without adapter mocking.
Public mirror image pulls remain a network prerequisite; no existing workload
is a fallback when pulling fails.

Checks include read-only root, dual bind targets/options, original anonymous
volume and proof data, resolved published port with loopback bind address, DNS,
groups, capabilities, memory/swap/CPU/restart, command/entrypoint and stop timeout.
It found two native-only cases now covered in unit tests: unspecified swappiness
`-1` must not be sent to OCI's uint64 field; JSON `[IP, port]` must retain its IP
instead of being interpreted as independent host ports. Explicit swappiness zero
and ordinary multiple host ports remain unchanged.

All `rm -a` commands in this fixture carry its private storage/runroot options,
never the host engine. `finally` removes private containers/volumes, checks no
containers remain and stops that exact service process. Pytest's temporary tree
is independent from the application database. Process-kill containment/cleanup
is still the caller's responsibility; never run a host/global prune. Native
socket acceptance does not establish normal production login or Portainer UI
acceptance.

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

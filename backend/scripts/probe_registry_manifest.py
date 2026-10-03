"""Public, read-only registry probe. Uses no stored DB, runtime config or credentials.

Run from backend: .venv/bin/python scripts/probe_registry_manifest.py \
    --registry reg.aoodc.com --image fawney19/aether --mode auto
The observation deadline is independent of the unchanged request/blob deadlines.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import time

from releasetracker.trackers.docker import DockerTracker


async def probe(*, registry, image, mode, limit=10, timeout=10, observation_timeout=240):
    tracker = DockerTracker(
        "public-registry-probe",
        image,
        registry=registry,
        published_at_mode=mode,
        timeout=timeout,
    )
    original = tracker._request_with_redirect_policy
    events = []

    async def timed(client, method, url, **kwargs):
        stage = (
            "tags"
            if "/tags/" in url
            else "blob" if "/blobs/" in url else "manifest" if "/manifests/" in url else "auth"
        )
        if stage == "blob" and kwargs["timeout"] > min(timeout, 5):
            raise AssertionError("Blob request deadline was relaxed")
        started = time.monotonic()
        event = {"stage": stage, "method": method.upper()}
        try:
            response = await original(client, method, url, **kwargs)
            event["status"] = response.status_code
            return response
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["seconds"] = round(time.monotonic() - started, 3)
            events.append(event)

    tracker._request_with_redirect_policy = timed
    started = time.monotonic()
    result = {"mode": mode, "request_timeout": timeout, "observation_timeout": observation_timeout}
    try:
        async with asyncio.timeout(observation_timeout):
            releases = await tracker.fetch_all(limit=limit)
        if len(releases) != limit or not all(r.commit_sha for r in releases):
            raise AssertionError("Expected requested number of releases with digests")
        blobs = [event for event in events if event["stage"] == "blob"]
        if mode == "first_observed" and blobs:
            raise AssertionError("first_observed must not read config blobs")
        result.update(status="passed", release_count=len(releases), digest_count=len(releases))
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
    result.update(
        seconds=round(time.monotonic() - started, 3),
        request_counts=dict(Counter(f"{e['stage']}:{e['method']}" for e in events)),
        events=events,
    )
    return result


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--mode", choices=("auto", "prefer_real", "first_observed"), default="auto")
    parser.add_argument("--limit", type=positive_int, default=10)
    parser.add_argument("--timeout", type=positive_int, default=10)
    parser.add_argument("--observation-timeout", type=positive_int, default=240)
    args = parser.parse_args()
    result = asyncio.run(
        probe(
            registry=args.registry,
            image=args.image,
            mode=args.mode,
            limit=args.limit,
            timeout=args.timeout,
            observation_timeout=args.observation_timeout,
        )
    )
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Current candidates and channel decisions, independent of retained audit history."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from ..models import Release, ReleaseAliasReference, ReleaseChannel
from ..versioning import (
    SELECTION_RULES_VERSION,
    parsed_version,
    pinned_alias,
    select_version_candidate,
)

# Stable reason codes; the UI owns the wording. Only ``state`` gates deployment.
AWAITING_CHECK = "awaiting_check"
MANIFEST_UNCONFIRMED = "manifest_unconfirmed"
ARTIFACT_CONFLICT = "artifact_conflict"
NO_ELIGIBLE_VERSION = "no_eligible_version"
_LEGACY_REASONS = {
    "No eligible version": NO_ELIGIBLE_VERSION,
    "Equivalent versions refer to different artifacts": ARTIFACT_CONFLICT,
    "Highest candidate manifest is not confirmed": MANIFEST_UNCONFIRMED,
    "Current tag inventory must be refreshed": AWAITING_CHECK,
    "Selection must be refreshed": AWAITING_CHECK,
}


def compatible_display(actual: str, display: str | None) -> bool:
    """A short alias can display the concrete version of the same proven artifact."""
    left = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", actual)
    right = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", display or "")
    if not left or not right:
        return False
    a, b = tuple(int(x) for x in left.groups() if x is not None), tuple(
        int(x) for x in right.groups() if x is not None
    )
    size = min(len(a), len(b))
    return a[:size] == b[:size]


def config_hash(source) -> str:
    value = source.model_dump(mode="json", exclude={"created_at", "updated_at"})
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def selection_revision(row, *, rules_version: int = SELECTION_RULES_VERSION) -> str:
    """Bind a persisted choice to its selection rules and immutable target."""
    payload = json.loads(row["release_payload"]) if row["release_payload"] else {}
    target_identity = [
        payload.get(field)
        for field in (
            "version",
            "tag_name",
            "artifact_digest",
            "commit_sha",
            "chart_version",
            "logical_version",
        )
    ]
    return hashlib.sha256(
        json.dumps(
            [rules_version, row["config_hash"], row["channel_key"], row["state"], target_identity],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def comparable(source, release, channel) -> bool:
    if source.source_type == "container":
        from ..versioning import container_channel_eligible

        return container_channel_eligible(
            release.tag_name, source.source_config.get("version_format", "dotted"), channel
        )
    return (
        bool(pinned_alias(channel))
        or parsed_version(
            release.chart_version if source.source_type == "helm" else release.version
        )
        is not None
    )


async def save_candidates(storage, source, releases, *, listed_tags=None, commit=False):
    db = await storage._get_connection()
    now = datetime.now().isoformat()
    if listed_tags is not None:
        # The complete tag LIST is independent of the bounded manifest probe window.
        await db.execute(
            "UPDATE source_current_candidates SET listed=0 WHERE tracker_source_id=?", (source.id,)
        )
        for tag in listed_tags:
            version = parsed_version(tag)
            provisional = Release(
                tracker_name="",
                tracker_type=source.source_type,
                name=tag,
                tag_name=tag,
                version=tag,
                url="",
                prerelease=bool(version and version.is_prerelease),
            )
            await db.execute(
                """INSERT INTO source_current_candidates(tracker_source_id,tag_name,payload,listed,updated_at)
                VALUES(?,?,?,1,?) ON CONFLICT(tracker_source_id,tag_name) DO UPDATE SET listed=1""",
                (source.id, tag, provisional.model_dump_json(), now),
            )
    await db.execute(
        """INSERT INTO source_inventory_scans(tracker_source_id,origin,listed_at,updated_at)
        VALUES(?,'observed',?,?) ON CONFLICT(tracker_source_id) DO UPDATE SET
        origin=excluded.origin,updated_at=excluded.updated_at,listed_at=COALESCE(excluded.listed_at,source_inventory_scans.listed_at)""",
        (source.id, now if listed_tags is not None else None, now),
    )
    if listed_tags is not None:
        await db.execute(
            "DELETE FROM source_current_candidates WHERE tracker_source_id=? AND listed=0",
            (source.id,),
        )
    if source.source_type == "container":
        releases = [
            release.model_copy(update={"tag_name": tag})
            for release in releases
            for tag in sorted(set([release.tag_name, *release.aliases]))
        ]
    for release in releases:
        version, app_version, chart_version = storage._release_version_metadata(
            release, source_type=source.source_type
        )
        candidate = release.model_copy(
            update={
                "tracker_type": source.source_type,
                "id": None,
                "version": release.tag_name if source.source_type == "container" else version,
                "app_version": app_version,
                "chart_version": chart_version,
                "tag_name": (
                    chart_version
                    if source.source_type == "helm" and chart_version
                    else release.tag_name
                ),
                "artifact_digest": release.artifact_digest
                or storage._release_digest_value(release, source_type=source.source_type),
            }
        )
        # A failed manifest probe must never replace a known immutable artifact.
        if source.source_type == "container" and not (
            candidate.artifact_digest or candidate.commit_sha
        ):
            await db.execute(
                """INSERT INTO source_current_candidates(tracker_source_id,tag_name,payload,listed,updated_at,confirmed)
                VALUES(?,?,?,1,?,0) ON CONFLICT(tracker_source_id,tag_name) DO NOTHING""",
                (source.id, candidate.tag_name, candidate.model_dump_json(), now),
            )
            if candidate.candidate_confirmed is False:
                await db.execute(
                    "UPDATE source_current_candidates SET confirmed=0 WHERE tracker_source_id=? AND tag_name=?",
                    (source.id, candidate.tag_name),
                )
            continue
        await db.execute(
            """INSERT INTO source_current_candidates(tracker_source_id,tag_name,payload,listed,updated_at,confirmed)
            VALUES(?,?,?,1,?,1) ON CONFLICT(tracker_source_id,tag_name)
            DO UPDATE SET payload=excluded.payload,listed=1,updated_at=excluded.updated_at,confirmed=1""",
            (source.id, candidate.tag_name, candidate.model_dump_json(), now),
        )
    if commit:

        await db.commit()


async def bootstrap_candidates(storage, source):
    db = await storage._get_connection()
    inventory = await (
        await db.execute(
            "SELECT origin FROM source_inventory_scans WHERE tracker_source_id=?", (source.id,)
        )
    ).fetchone()
    if inventory is not None:
        # Re-evaluate backfills made before pointer evidence was honoured; a real check replaces them.
        if inventory[0] == "bootstrap" and source.source_type == "container":
            await confirm_backfilled_pointers(storage, source)
        return
    releases = await storage.get_source_release_history_releases_by_source(source.id)
    aliases = await storage.get_source_release_aliases_for_source(source.id)
    latest = await storage.get_source_alias_latest_digests(source.id)
    expanded = []
    for release in releases:
        refs = aliases.get(release.id, [])
        if not refs:
            expanded.append(release)
        for alias in refs:
            if source.source_type in {"container", "helm"} and latest.get(alias["alias"]) != (
                release.artifact_digest or release.commit_sha
            ):
                continue
            expanded.append(
                release.model_copy(
                    update={
                        "tag_name": alias["alias"],
                        "version": alias["alias"],
                        "name": alias["alias"],
                        "channel_name": alias["channel_name"],
                    }
                )
            )
    if not expanded:
        for item in await storage.get_source_release_observations_by_source(source.id):
            expanded.append(
                Release(
                    tracker_name="",
                    tracker_type=source.source_type,
                    name=item.name,
                    tag_name=item.tag_name,
                    version=item.version,
                    app_version=item.app_version,
                    chart_version=item.chart_version,
                    published_at=item.published_at,
                    url=item.url,
                    body=item.body,
                    prerelease=item.prerelease,
                    commit_sha=item.commit_sha,
                )
            )
    await save_candidates(storage, source, expanded)
    await db.execute(
        "UPDATE source_inventory_scans SET origin='bootstrap' WHERE tracker_source_id=?",
        (source.id,),
    )
    if source.source_type == "container":
        await confirm_backfilled_pointers(storage, source)


async def confirm_backfilled_pointers(storage, source):
    """A backfilled tag is confirmed when it matches the tag's most recently observed digest.

    That pointer is the same evidence a live check of the same age would leave, so it is
    trusted equally; tags without such evidence stay unconfirmed until a registry check.
    """
    db = await storage._get_connection()
    latest = await storage.get_source_alias_latest_digests(source.id)
    rows = await (
        await db.execute(
            "SELECT tag_name,payload FROM source_current_candidates WHERE tracker_source_id=?",
            (source.id,),
        )
    ).fetchall()
    for tag, payload in rows:
        data = json.loads(payload)
        digest = data.get("artifact_digest") or data.get("commit_sha")
        await db.execute(
            "UPDATE source_current_candidates SET confirmed=? WHERE tracker_source_id=? AND tag_name=?",
            (int(bool(digest) and latest.get(tag) == digest), source.id, tag),
        )


async def source_candidates(storage, source):
    db = await storage._get_connection()
    rows = await (
        await db.execute(
            "SELECT payload,confirmed FROM source_current_candidates WHERE tracker_source_id=? AND listed=1",
            (source.id,),
        )
    ).fetchall()
    return [
        Release.model_validate_json(row[0]).model_copy(update={"candidate_confirmed": bool(row[1])})
        for row in rows
    ]


async def audit_changes(storage, source, releases):
    """Do not re-create pruned audit on an unchanged current observation."""
    db = await storage._get_connection()
    retention = await storage.get_release_history_retention_count()
    pruned = await (
        await db.execute(
            "SELECT 1 FROM tracker_release_history_tombstones h JOIN aggregate_tracker_sources s ON s.aggregate_tracker_id=h.aggregate_tracker_id WHERE s.id=? AND h.retention_count=? LIMIT 1",
            (source.id, retention),
        )
    ).fetchone()
    result = []
    for release in releases:
        identity = storage.release_identity_key_for_source(release, source_type=source.source_type)
        if await storage.get_source_release_history_id(source.id, identity) is not None:
            result.append(release)
            continue
        row = await (
            await db.execute(
                "SELECT payload,confirmed FROM source_current_candidates WHERE tracker_source_id=? AND tag_name=?",
                (source.id, release.tag_name),
            )
        ).fetchone()
        previous = Release.model_validate_json(row["payload"]) if row else None
        if (
            not pruned
            or previous is None
            or not row["confirmed"]
            or (
                previous.version,
                previous.chart_version,
                previous.artifact_digest or previous.commit_sha,
            )
            != (
                release.version,
                release.chart_version,
                release.artifact_digest or release.commit_sha,
            )
        ):
            result.append(release)
    return result


async def audit_reference(storage, tracker_id, source, release):
    """Optional display correlation; missing audit never removes a current decision."""
    identity = storage.release_identity_key_for_source(release, source_type=source.source_type)
    source_id = await storage.get_source_release_history_id(source.id, identity)
    if source_id is None:
        return None, None
    db = await storage._get_connection()
    row = await (
        await db.execute(
            """SELECT h.id,h.identity_key,h.version FROM tracker_release_history h
        LEFT JOIN tracker_release_history_sources x ON x.tracker_release_history_id=h.id
        WHERE h.aggregate_tracker_id=? AND (h.primary_source_release_history_id=? OR x.source_release_history_id=?)
          AND h.merged_into_tracker_release_history_id IS NULL
        ORDER BY (EXISTS(SELECT 1 FROM source_release_history p WHERE p.id=h.primary_source_release_history_id AND p.source_type IN ('github','gitlab','gitea'))) DESC,(h.version=?) DESC,h.id DESC LIMIT 1""",
            (tracker_id, source_id, source_id, release.version),
        )
    ).fetchone()
    return source_id, row


async def refresh_selections(storage, tracker, *, commit=True):
    db = await storage._get_connection()
    previous = {
        (row["tracker_source_id"], row["channel_key"]): dict(row)
        for row in await (
            await db.execute(
                "SELECT * FROM tracker_channel_selections WHERE aggregate_tracker_id=?",
                (tracker.id,),
            )
        ).fetchall()
    }
    await db.execute(
        "DELETE FROM tracker_channel_selections WHERE aggregate_tracker_id=?", (tracker.id,)
    )
    releases = []
    now = datetime.now().isoformat()
    for source in tracker.sources:
        if not source.enabled:
            continue
        await bootstrap_candidates(storage, source)
        candidates = await source_candidates(storage, source)
        inventory = await (
            await db.execute(
                "SELECT * FROM source_inventory_scans WHERE tracker_source_id=?", (source.id,)
            )
        ).fetchone()
        channels = source.release_channels or [
            ReleaseChannel(
                release_channel_key="default",
                name="stable",
                type=None if source.source_type == "container" else "release",
            )
        ]
        for rank, channel in enumerate(channels):
            if not channel.enabled:
                continue
            key = channel.release_channel_key or storage._channel_selection_key(channel, rank)
            eligible = [
                r
                for r in candidates
                if storage._release_matches_channel(
                    r, channel, channel_source_type=source.source_type
                )
                and comparable(source, r, channel)
            ]
            ranked = (
                [r.model_copy(update={"version": r.tag_name}) for r in eligible]
                if source.source_type == "container" and not pinned_alias(channel)
                else eligible
            )
            selected = select_version_candidate(ranked, exact_alias=pinned_alias(channel))
            # Ranking uses the tag version. Keep that same Release in the snapshot;
            # a digest-shared floating alias may carry a different stored version.
            winner = selected
            state, reason = "empty", NO_ELIGIBLE_VERSION
            if eligible:
                state, reason = "conflict", ARTIFACT_CONFLICT
            if winner is not None:
                state, reason = "ready", None
                if source.source_type == "container" and (
                    winner.candidate_confirmed is False
                    or not (winner.artifact_digest or winner.commit_sha)
                ):
                    state, reason = "pending", MANIFEST_UNCONFIRMED
                same_artifact = [
                    r.tag_name
                    for r in candidates
                    if (r.artifact_digest or r.commit_sha)
                    == (winner.artifact_digest or winner.commit_sha)
                ]
                _, audit = await audit_reference(storage, tracker.id, source, winner)
                old_payload = json.loads(
                    previous.get((source.id, key), {}).get("release_payload") or "{}"
                )
                logical_version = winner.logical_version
                if (
                    source.source_type == "container"
                    and audit
                    and compatible_display(winner.version, audit["version"])
                ):
                    logical_version = audit["version"]
                elif (
                    old_payload.get("tag_name") == winner.tag_name
                    and old_payload.get("artifact_digest") == winner.artifact_digest
                    and compatible_display(winner.version, old_payload.get("logical_version"))
                ):
                    logical_version = old_payload["logical_version"]
                winner = winner.model_copy(
                    update={
                        "tracker_name": tracker.name,
                        "channel_name": channel.name,
                        "selection_source_id": source.id,
                        "logical_version": logical_version,
                        "aliases": (
                            sorted(same_artifact)
                            if winner.artifact_digest or winner.commit_sha
                            else [winner.tag_name]
                        ),
                    }
                )
            # Backfilled tags without pointer evidence wait for a registry check; that is
            # not a failed manifest probe.
            if (
                source.source_type == "container"
                and inventory["origin"] == "bootstrap"
                and state == "pending"
            ):
                reason = AWAITING_CHECK
            payload = winner.model_dump_json() if winner is not None else None
            old = previous.get((source.id, key))
            if state == "pending" and old and old["release_payload"]:
                payload = old[
                    "release_payload"
                ]  # Display the previous result, never deploy it as a confirmed target.
            fingerprint = config_hash(source)
            revision = selection_revision(
                {
                    "release_payload": payload,
                    "config_hash": fingerprint,
                    "channel_key": key,
                    "state": state,
                }
            )
            await db.execute(
                """INSERT INTO tracker_channel_selections(aggregate_tracker_id,tracker_source_id,
                channel_key,channel_name,state,reason,release_payload,config_hash,revision,updated_at,inventory_revision)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tracker.id,
                    source.id,
                    key,
                    channel.name,
                    state,
                    reason,
                    payload,
                    fingerprint,
                    revision,
                    now,
                    inventory["updated_at"],
                ),
            )
            if payload:
                releases.append(Release.model_validate_json(payload))
    if commit:
        await db.commit()
    return releases


async def reconcile_selection_rules(storage) -> tuple[int, int]:
    """Rebuild obsolete local choices on startup without fetching or queuing work."""
    if not await storage._table_exists("tracker_channel_selections"):
        return 0, 0

    async with storage.tasks.transaction() as db:
        rows = await (
            await db.execute(
                "SELECT aggregate_tracker_id,channel_key,config_hash,state,release_payload,revision "
                "FROM tracker_channel_selections"
            )
        ).fetchall()
        stale_ids = {
            row["aggregate_tracker_id"]
            for row in rows
            if row["revision"] != selection_revision(row)
        }
        # Old scans may have saved a digest-sharing alias as the tag version.
        # This changes only the version in the candidate payload, not its
        # observation time, confirmation, tag pointer or digest.
        normalized = await db.execute(
            "UPDATE source_current_candidates "
            "SET payload=json_set(payload, '$.version', tag_name) "
            "WHERE tracker_source_id IN "
            "(SELECT id FROM aggregate_tracker_sources WHERE source_type='container') "
            "AND json_extract(payload, '$.version') IS NOT tag_name"
        )
        if stale_ids:
            for tracker in await storage.get_all_aggregate_trackers():
                if tracker.id not in stale_ids:
                    continue
                await refresh_selections(storage, tracker, commit=False)
                current = await current_rows(storage, tracker.id) or []
                await storage.refresh_tracker_current_releases(
                    tracker.id, [row["release"] for row in current], commit=False
                )
        return len(stale_ids), normalized.rowcount


async def snapshots(storage, tracker_id):
    db = await storage._get_connection()
    rows = [
        dict(row)
        for row in await (
            await db.execute(
                "SELECT c.*,i.updated_at AS current_inventory_revision,i.origin AS inventory_origin FROM tracker_channel_selections c LEFT JOIN source_inventory_scans i ON i.tracker_source_id=c.tracker_source_id WHERE c.aggregate_tracker_id=? ORDER BY c.tracker_source_id,c.channel_key",
                (tracker_id,),
            )
        ).fetchall()
    ]
    sources = {}
    for row in rows:
        source_id = row["tracker_source_id"]
        if source_id not in sources:
            sources[source_id] = await storage.get_tracker_source(source_id)
        source = sources[source_id]
        row["reason"] = _LEGACY_REASONS.get(row["reason"], row["reason"])
        if row["state"] == "pending" and row["inventory_origin"] == "bootstrap":
            row["reason"] = AWAITING_CHECK
        if (
            row["revision"] != selection_revision(row)
            or source is None
            or row["inventory_revision"] != row["current_inventory_revision"]
            or row["config_hash"] != config_hash(source)
        ):
            row["state"], row["reason"] = "pending", AWAITING_CHECK
    return rows


async def bound_selection(storage, source, channel_name):
    rows = [
        r
        for r in await snapshots(storage, source.aggregate_tracker_id)
        if r["tracker_source_id"] == source.id
    ]
    db = await storage._get_connection()
    inventory = await (
        await db.execute(
            "SELECT updated_at FROM source_inventory_scans WHERE tracker_source_id=?", (source.id,)
        )
    ).fetchone()
    if rows and (
        inventory is None or any(row["inventory_revision"] != inventory[0] for row in rows)
    ):
        return None, True
    if not rows:
        return None, inventory is not None
    matching = [r for r in rows if not channel_name or r["channel_name"] == channel_name]
    if not matching or any(
        r["config_hash"] != config_hash(source) or r["state"] != "ready" for r in matching
    ):
        return None, True
    candidates = [
        Release.model_validate_json(r["release_payload"]).model_copy(
            update={"selection_revision": r["revision"]}
        )
        for r in matching
        if r["release_payload"]
    ]
    return (candidates[0] if len(candidates) == 1 else select_version_candidate(candidates)), True


async def current_rows(storage, tracker_id):
    values = await snapshots(storage, tracker_id)
    if not values:
        initialized = await (
            await (await storage._get_connection()).execute(
                "SELECT 1 FROM source_inventory_scans i JOIN aggregate_tracker_sources s ON s.id=i.tracker_source_id WHERE s.aggregate_tracker_id=? LIMIT 1",
                (tracker_id,),
            )
        ).fetchone()
        return [] if initialized else None  # Only uninitialized legacy projections can fall back.
    rows = []
    for value in values:
        if not value["release_payload"]:
            continue
        release = Release.model_validate_json(value["release_payload"])
        identity = storage.release_identity_key_for_source(
            release, source_type=release.tracker_type
        )
        source = await storage.get_tracker_source(value["tracker_source_id"])
        if source is None or not source.enabled:
            continue
        source_history_id, history = await audit_reference(storage, tracker_id, source, release)
        if history:
            identity = history["identity_key"]
        release = release.model_copy(update={"version": release.logical_version or release.version})
        release = release.model_copy(
            update={
                "alias_references": [
                    ReleaseAliasReference(
                        tracker_source_id=source.id,
                        source_type=source.source_type,
                        alias=release.tag_name,
                        prerelease=release.prerelease,
                        digest=release.artifact_digest or release.commit_sha,
                    )
                ]
            }
        )
        contribution = {
            **release.model_dump(mode="json"),
            "source_release_history_id": source_history_id,
            "source_key": source.source_key,
            "source_type": source.source_type,
            "contribution_kind": "primary",
            "digest": release.artifact_digest or release.commit_sha,
            "observed_at": release.last_observed_at or value["updated_at"],
            "aliases": release.aliases,
        }
        rows.append(
            {
                "tracker_release_history_id": history[0] if history else None,
                "identity_key": identity,
                "version": release.version,
                "digest": release.artifact_digest or release.commit_sha,
                "published_at": release.published_at,
                "name": release.name,
                "tag_name": release.tag_name,
                "prerelease": release.prerelease,
                "url": release.url,
                "changelog_url": release.changelog_url,
                "body": release.body,
                "projected_at": datetime.fromisoformat(value["updated_at"]),
                "primary_source": {
                    "source_key": source.source_key,
                    "source_type": source.source_type,
                    "source_release_history_id": source_history_id,
                },
                "release": release,
                "source_contributions": [contribution],
                "selection_state": value["state"],
                "selection_reason": value["reason"],
                "selection_revision": value["revision"],
                "channel_keys": [value["channel_key"]],
            }
        )
    # Coalesce only the display surface. Bound executors keep per-source decisions.
    grouped = {}
    for row in rows:
        from ..versioning import parsed_version

        version = parsed_version(row["version"])
        key = str(version.key) if version is not None else row["version"]
        if key not in grouped:
            grouped[key] = row
            continue
        current = grouped[key]
        current["channel_keys"] = sorted(set(current["channel_keys"] + row["channel_keys"]))
        if (
            current["release"].artifact_digest is None
            and row["release"].artifact_digest is not None
        ):
            current["digest"] = row["digest"]
            current["release"] = current["release"].model_copy(
                update={"artifact_digest": row["release"].artifact_digest}
            )
        existing = {
            (item["source_key"], item["tag_name"], item["digest"])
            for item in current["source_contributions"]
        }
        current["source_contributions"].extend(
            item
            for item in row["source_contributions"]
            if (item["source_key"], item["tag_name"], item["digest"]) not in existing
        )
        refs = current["release"].alias_references + row["release"].alias_references
        current["release"] = current["release"].model_copy(update={"alias_references": refs})
    return list(grouped.values())

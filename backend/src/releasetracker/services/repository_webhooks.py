"""Repository webhook contracts. Payloads are signals, never release truth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Literal
from urllib.parse import urlsplit, unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import TrackerSource


class RepositoryWebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tracker_source_id: int = Field(gt=0)
    provider: Literal["github", "gitlab", "gitea", "forgejo"]
    enabled: bool = False
    auth_mode: Literal["hmac", "gitlab_signing", "gitlab_token"] = "hmac"
    secret: str | None = Field(default=None, min_length=16, max_length=512, repr=False)
    release_published: bool = True
    workflow_success: bool = True
    linked_source_ids: list[int] = Field(default_factory=list, max_length=20)
    branches: list[str] = Field(default_factory=list, max_length=20)
    workflows: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_options(self):
        if (self.provider == "gitlab") != (self.auth_mode != "hmac"):
            raise ValueError("Choose the authentication mode supported by this provider")
        if not self.release_published and not self.workflow_success:
            raise ValueError("Enable at least one webhook event")
        for field in ("branches", "workflows"):
            patterns = [p.strip() for p in getattr(self, field)]
            if any(not p or len(p) > 200 for p in patterns):
                raise ValueError("Filters must be non-empty and at most 200 characters")
            setattr(self, field, list(dict.fromkeys(patterns)))
        if len(set(self.linked_source_ids)) != len(self.linked_source_ids):
            raise ValueError("Linked sources must be unique")
        if self.tracker_source_id in self.linked_source_ids:
            raise ValueError("The repository source cannot link to itself")
        return self


@dataclass
class RepositoryEvent:
    kind: str
    action: str
    repository: dict
    branch: str = ""
    workflow: str = ""
    ref: str = ""
    run_id: str = ""
    reason: str = ""

    def summary(self) -> dict:
        return {
            key: str(getattr(self, key))[:200]
            for key in ("kind", "action", "branch", "workflow", "ref", "run_id")
        }


def obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def verify_signature(
    provider: str,
    mode: str,
    secret: str,
    headers,
    body: bytes,
    now: float | None = None,
) -> None:
    """Fail closed; never downgrade to token authentication when a signature is absent."""
    if not secret:
        raise ValueError("Invalid signature")
    if mode == "gitlab_token":
        valid = hmac.compare_digest(headers.get("x-gitlab-token", "").encode(), secret.encode())
    elif mode == "gitlab_signing":
        try:
            timestamp = headers["webhook-timestamp"]
            if abs((time.time() if now is None else now) - int(timestamp)) > 300:
                raise ValueError("Expired signature")
            message = headers["webhook-id"].encode() + b"." + timestamp.encode() + b"." + body
            key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
            expected = "v1," + base64.b64encode(hmac.digest(key, message, "sha256")).decode()
            valid = any(
                hmac.compare_digest(expected, part)
                for part in headers.get("webhook-signature", "").split()
            )
        except (ValueError, KeyError):
            raise ValueError("Invalid signature") from None
    else:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if provider == "github":
            expected, received = "sha256=" + digest, headers.get("x-hub-signature-256", "")
        else:
            expected = digest
            received = headers.get(f"x-{provider}-signature", "") or headers.get(
                "x-gitea-signature", ""
            )
        valid = hmac.compare_digest(expected.encode(), received.encode())
    if not valid:
        raise ValueError("Invalid signature")


def _action_run_reference(run: dict) -> tuple[str, str]:
    """Resolve the Git ref, not Forgejo's display-only PrettyRef.

    Forgejo ActionRun embeds the original trigger as JSON in event_payload.
    Pushes can target tags as well as branches, so neither prettyref nor the
    event name alone proves that a short name is a branch.
    """

    def text(value) -> str:
        return value if isinstance(value, str) else ""

    direct = text(run.get("ref"))
    fallback = direct or text(run.get("prettyref"))
    trigger = run.get("event_payload")
    if isinstance(trigger, str):
        try:
            trigger = json.loads(trigger)
        except (ValueError, RecursionError):
            return fallback, ""
    if trigger is not None and not isinstance(trigger, dict):
        return fallback, ""
    trigger = obj(trigger)
    original = text(trigger.get("ref"))
    ref_type = text(trigger.get("ref_type"))
    # Prefer an explicitly typed original ref to an abbreviated API run.ref.
    if original and not original.startswith("refs/") and ref_type in {"branch", "tag"}:
        original = ("refs/heads/" if ref_type == "branch" else "refs/tags/") + original
    ref = direct if direct.startswith("refs/") else original or fallback
    if (
        run.get("is_fork_pull_request") is True
        or any(text(run.get(key)).startswith("pull_request") for key in ("event", "trigger_event"))
        or "pull_request" in trigger
        or ref_type == "tag"
    ):
        return ref, ""
    candidates = [value for value in (direct, original) if value]
    qualified = [value for value in candidates if value.startswith("refs/")]
    if not qualified or any(not value.startswith("refs/heads/") for value in qualified):
        return ref, ""
    branch = qualified[0].removeprefix("refs/heads/")
    if not branch or any(value.removeprefix("refs/heads/") != branch for value in candidates):
        return ref, ""
    return "refs/heads/" + branch, branch


def normalize_event(provider: str, headers, payload: dict) -> RepositoryEvent:
    action = str(payload.get("action", ""))
    repo = obj(payload.get("repository"))
    if provider == "gitlab":
        name = headers.get("x-gitlab-event", "")
        repo = obj(payload.get("project"))
        if name == "Release Hook" and payload.get("action") == "create":
            return RepositoryEvent("release", "published", repo, ref=str(payload.get("tag", "")))
        attrs = obj(payload.get("object_attributes"))
        if name == "Pipeline Hook" and attrs.get("status") == "success":
            branch = "" if attrs.get("tag") else str(attrs.get("ref", ""))
            return RepositoryEvent(
                "workflow",
                "success",
                repo,
                branch=branch,
                workflow=str(attrs.get("name") or ""),
                ref=str(attrs.get("ref", "")),
                run_id=str(attrs.get("id", "")),
            )
    else:
        name = headers.get(f"x-{provider}-event", "")
        if provider == "forgejo":
            name = name or headers.get("x-gitea-event", "")
        if name == "ping":
            return RepositoryEvent("ping", action, repo)
        release = obj(payload.get("release"))
        release_actions = {"published"}
        if provider in {"forgejo", "gitea"}:
            # Forgejo/Gitea can report a newly created non-draft release as
            # `updated`, especially after release assets are attached.
            release_actions.update({"created", "updated"})
        if name == "release" and action in release_actions and release.get("draft") is not True:
            return RepositoryEvent("release", action, repo, ref=str(release.get("tag_name", "")))
        if provider in {"forgejo", "gitea"} and name in {
            "action_run",
            "action_run_success",
            "action_run_recover",
        }:
            run = obj(payload.get("run"))
            if action in {"success", "recover", "completed"} and run.get("status") in {
                None,
                "success",
            }:
                ref, branch = _action_run_reference(run)
                return RepositoryEvent(
                    "workflow",
                    "success",
                    obj(run.get("repository")),
                    branch=branch,
                    workflow=str(run.get("workflow_id") or ""),
                    ref=ref,
                    run_id=str(run.get("id", "")),
                )
        run = obj(payload.get("workflow_run"))
        if (
            name == "workflow_run"
            and action == "completed"
            and run.get("conclusion") == "success"
            and run.get("status") == "completed"
        ):
            workflow = obj(payload.get("workflow"))
            return RepositoryEvent(
                "workflow",
                "success",
                repo,
                branch=str(run.get("head_branch") or ""),
                workflow=str(run.get("path") or workflow.get("path") or ""),
                ref=str(run.get("head_branch") or ""),
                run_id=str(run.get("id", "")),
            )
    return RepositoryEvent("ignored", action, repo, reason="unsupported_event")


def repository_matches(source: TrackerSource, provider: str, repository: dict) -> bool:
    config = source.source_config
    expected = str(config.get("project") if provider == "gitlab" else config.get("repo", ""))
    url = repository.get("web_url") if provider == "gitlab" else repository.get("html_url")
    if not isinstance(url, str) or not url:
        return False
    default_instances = {
        "github": "https://github.com",
        "gitlab": "https://gitlab.com",
        "gitea": "https://gitea.com",
        "forgejo": "https://gitea.com",
    }
    base = str(config.get("instance") or default_instances[provider])
    actual, configured = urlsplit(url), urlsplit(base)
    if actual.scheme not in {"https", "http"} or actual.netloc.lower() != configured.netloc.lower():
        return False
    if provider == "gitlab" and expected.isdigit():
        return str(repository.get("id")) == expected
    path = unquote(actual.path).rstrip("/").removesuffix(".git")
    return path.lower() == (configured.path.rstrip("/") + "/" + expected.strip("/")).lower()


def event_reason(event: RepositoryEvent, config: dict) -> str:
    if event.kind in {"ignored", "ping"}:
        return event.reason or "ping"
    if event.kind == "release":
        return "" if config["release_published"] else "event_disabled"
    if not config["workflow_success"]:
        return "event_disabled"
    for field, value in (("branches", event.branch), ("workflows", event.workflow)):
        patterns = config[field]
        if patterns and not value:
            return field + "_unavailable"
        if patterns and not any(fnmatchcase(value, p) for p in patterns):
            return field + "_mismatch"
    return ""


def delivery_key(provider: str, headers, body: bytes) -> str:
    names = {
        "github": ["x-github-delivery"],
        "gitlab": ["webhook-id", "x-gitlab-event-uuid", "x-gitlab-webhook-uuid"],
        "gitea": ["x-gitea-delivery"],
        "forgejo": ["x-forgejo-delivery", "x-gitea-delivery"],
    }
    for name in names[provider]:
        value = headers.get(name)
        if value:
            return "id:" + hashlib.sha256(value.encode()).hexdigest()
    # Lack of a delivery id does not disable deduplication. Persist only a bounded hash.
    return "body:" + hashlib.sha256(body).hexdigest()

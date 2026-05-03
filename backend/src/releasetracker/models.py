"""Core data models"""

from datetime import datetime
from typing import Literal, Any

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ConfigDict,
    field_serializer,
    field_validator,
    model_validator,
)


class Release(BaseModel):
    """Release model"""

    id: int | None = None
    tracker_name: str
    tracker_type: str = "github"
    name: str
    tag_name: str
    version: str
    app_version: str | None = None
    chart_version: str | None = None
    published_at: datetime
    url: str
    changelog_url: str | None = None
    prerelease: bool = False
    body: str | None = None  # Release notes content
    channel_name: str | None = None  # Channel name (stable/prerelease/beta/canary)
    commit_sha: str | None = None  # Git commit SHA
    republish_count: int = 0  # Republish count
    created_at: datetime = Field(default_factory=datetime.now)


class TrackerStatus(BaseModel):
    """Tracker status"""

    name: str
    type: Literal["github", "gitlab", "gitea", "helm", "container"]
    enabled: bool = True
    last_check: datetime | None = None
    last_version: str | None = None
    error: str | None = None
    channel_count: int = 0  # Channel count


TrackerSourceType = Literal["github", "gitlab", "gitea", "helm", "container"]
TrackerChangelogPolicy = Literal["primary_source"]
CanonicalReleaseContributionKind = Literal["primary", "supporting"]
SourceFetchRunTriggerMode = Literal["scheduled", "manual", "bootstrap"]
SourceFetchRunStatus = Literal["running", "success", "partial", "failed"]
TrackerReleaseHistoryContributionKind = Literal["primary", "supporting"]


def _normalize_tracker_changelog_policy(value: str) -> str:
    return value


class ChangelogPolicyReference(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    policy: TrackerChangelogPolicy = "primary_source"
    primary_source_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("primary_source_key"),
    )

    @field_validator("policy", mode="before")
    @classmethod
    def normalize_policy(cls, value: str) -> str:
        return _normalize_tracker_changelog_policy(value)

    @field_serializer("policy")
    def serialize_policy(self, value: str) -> str:
        return _normalize_tracker_changelog_policy(value)

    @field_validator("primary_source_key")
    @classmethod
    def validate_primary_source_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("primary_source_key must be a non-empty string when provided")
        return normalized_value

    @property
    def primary_channel_key(self) -> str | None:
        return self.primary_source_key


class ReleaseChannel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    release_channel_key: str = Field(validation_alias=AliasChoices("release_channel_key"))
    name: Literal["stable", "prerelease", "beta", "canary"]
    type: Literal["release", "prerelease"] | None = None
    include_pattern: str | None = None
    exclude_pattern: str | None = None
    enabled: bool = True

    @field_validator("release_channel_key")
    @classmethod
    def validate_release_channel_key(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("release_channel_key must be a non-empty string")
        return normalized_value

    @property
    def channel_key(self) -> str:
        return self.release_channel_key

    @property
    def key(self) -> str:
        return self.release_channel_key


class TrackerSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: int | None = None
    aggregate_tracker_id: int | None = None
    source_key: str
    source_type: TrackerSourceType
    enabled: bool = True
    credential_name: str | None = None
    source_config: dict[str, Any] = Field(default_factory=dict)
    release_channels: list[ReleaseChannel] = Field(default_factory=list)
    source_rank: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="before")
    @classmethod
    def extract_release_channels_from_source_config(cls, data: Any):
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        config_field_name = "source_config"
        config_value = payload.get(config_field_name)
        if isinstance(config_value, dict) and "release_channels" in config_value:
            payload[config_field_name] = dict(config_value)
            embedded_release_channels = payload[config_field_name].pop("release_channels")
            payload.setdefault("release_channels", embedded_release_channels)

        return payload

    @field_validator("source_key")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("source_key must be a non-empty string")
        return normalized_value

    @field_validator("credential_name")
    @classmethod
    def validate_credential_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("credential_name must be a non-empty string when provided")
        return normalized_value

    @model_validator(mode="after")
    def validate_source_config(self):
        provider_requirements: dict[str, tuple[set[str], set[str]]] = {
            "github": ({"repo", "fetch_mode"}, {"repo"}),
            "gitlab": ({"project", "instance"}, {"project"}),
            "gitea": ({"repo", "instance"}, {"repo"}),
            "helm": ({"repo", "chart"}, {"repo", "chart"}),
            "container": ({"image", "registry"}, {"image", "registry"}),
        }

        allowed_keys, required_keys = provider_requirements[self.source_type]
        unknown_keys = sorted(set(self.source_config.keys()) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                f"Unknown source_config keys for {self.source_type}: {', '.join(unknown_keys)}"
            )

        missing_keys = [
            key
            for key in sorted(required_keys)
            if not isinstance(self.source_config.get(key), str)
            or not self.source_config[key].strip()
        ]
        if missing_keys:
            raise ValueError(
                f"Missing required source_config keys for {self.source_type}: {', '.join(missing_keys)}"
            )

        for key, value in self.source_config.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"source_config.{key} must be a non-empty string")

        if self.source_type == "github" and self.source_config.get("fetch_mode") not in {
            None,
            "graphql_first",
            "rest_first",
        }:
            raise ValueError("source_config.fetch_mode must be one of: graphql_first, rest_first")

        return self


class SourceReleaseObservation(BaseModel):
    id: int | None = None
    tracker_source_id: int
    source_release_key: str
    name: str
    tag_name: str
    version: str
    app_version: str | None = None
    chart_version: str | None = None
    published_at: datetime
    url: str
    changelog_url: str | None = None
    prerelease: bool = False
    body: str | None = None
    commit_sha: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=datetime.now)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class CanonicalReleaseObservation(BaseModel):
    source_release_observation_id: int
    contribution_kind: CanonicalReleaseContributionKind = "supporting"
    created_at: datetime = Field(default_factory=datetime.now)


class CanonicalRelease(BaseModel):
    id: int | None = None
    aggregate_tracker_id: int
    canonical_key: str
    version: str
    name: str
    tag_name: str
    published_at: datetime
    url: str
    changelog_url: str | None = None
    prerelease: bool = False
    body: str | None = None
    primary_observation_id: int | None = None
    observations: list[CanonicalReleaseObservation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class SourceFetchRun(BaseModel):
    id: int | None = None
    tracker_source_id: int
    trigger_mode: SourceFetchRunTriggerMode = "scheduled"
    started_at: datetime
    finished_at: datetime | None = None
    status: SourceFetchRunStatus = "running"
    error_message: str | None = None
    fetched_count: int = 0
    filtered_in_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)


class SourceReleaseHistory(BaseModel):
    id: int | None = None
    tracker_source_id: int
    first_source_fetch_run_id: int
    source_type: TrackerSourceType
    source_release_key: str
    version: str
    digest: str | None = None
    digest_algorithm: str | None = None
    digest_media_type: str | None = None
    digest_platform: str | None = None
    identity_key: str
    name: str
    tag_name: str
    published_at: datetime
    url: str
    changelog_url: str | None = None
    prerelease: bool = False
    body: str | None = None
    commit_sha: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    first_observed_at: datetime
    created_at: datetime = Field(default_factory=datetime.now)


class SourceReleaseRunObservation(BaseModel):
    id: int | None = None
    source_fetch_run_id: int
    source_release_history_id: int
    observed_at: datetime = Field(default_factory=datetime.now)
    created_at: datetime = Field(default_factory=datetime.now)


class TrackerReleaseHistory(BaseModel):
    id: int | None = None
    aggregate_tracker_id: int
    identity_key: str
    version: str
    digest: str | None = None
    digest_algorithm: str | None = None
    digest_media_type: str | None = None
    digest_platform: str | None = None
    primary_source_release_history_id: int
    created_at: datetime = Field(default_factory=datetime.now)


class TrackerReleaseHistorySource(BaseModel):
    tracker_release_history_id: int
    source_release_history_id: int
    contribution_kind: TrackerReleaseHistoryContributionKind = "supporting"
    created_at: datetime = Field(default_factory=datetime.now)


class TrackerCurrentRelease(BaseModel):
    id: int | None = None
    aggregate_tracker_id: int
    identity_key: str
    version: str
    digest: str | None = None
    tracker_release_history_id: int
    name: str
    tag_name: str
    published_at: datetime
    url: str
    changelog_url: str | None = None
    prerelease: bool = False
    body: str | None = None
    projected_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class AggregateTracker(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int | None = None
    name: str
    enabled: bool = True
    changelog_policy: TrackerChangelogPolicy = "primary_source"
    primary_changelog_source_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("primary_changelog_source_key"),
    )
    description: str | None = None
    sources: list[TrackerSource] = Field(
        default_factory=list,
        validation_alias=AliasChoices("sources"),
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("name must be a non-empty string")
        return normalized_value

    @field_validator("changelog_policy", mode="before")
    @classmethod
    def normalize_changelog_policy(cls, value: str) -> str:
        return _normalize_tracker_changelog_policy(value)

    @field_serializer("changelog_policy")
    def serialize_changelog_policy(self, value: str) -> str:
        return _normalize_tracker_changelog_policy(value)

    @field_validator("primary_changelog_source_key")
    @classmethod
    def validate_primary_changelog_source_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(
                "primary_changelog_source_key must be a non-empty string when provided"
            )
        return normalized_value

    @model_validator(mode="after")
    def validate_sources(self):
        if not self.sources:
            raise ValueError("aggregate trackers must define at least one tracker source")

        seen_source_keys: set[str] = set()
        for source in self.sources:
            if source.source_key in seen_source_keys:
                raise ValueError(f"duplicate source_key: {source.source_key}")
            seen_source_keys.add(source.source_key)

        if (
            self.primary_changelog_source_key
            and self.primary_changelog_source_key not in seen_source_keys
        ):
            raise ValueError(
                "primary_changelog_source_key must reference one of the tracker sources"
            )

        return self


class ReleaseStats(BaseModel):
    """Release statistics"""

    total_trackers: int
    total_releases: int
    recent_releases: int  # Last 24 hours
    latest_update: datetime | None = None
    daily_stats: list[dict[str, Any]] = (
        []
    )  # Daily release statistics [{"date": "...", "channels": {...}}]
    channel_stats: dict[str, int] = (
        {}
    )  # Total release count by channel {"stable": 10, "beta": 5, ...}
    release_type_stats: dict[str, int] = (
        {}
    )  # Release count by release type {"stable": 10, "prerelease": 5}


CredentialType = Literal[
    "github",
    "gitlab",
    "gitea",
    "helm",
    "docker",
    "docker_runtime",
    "podman_runtime",
    "kubernetes_runtime",
    "portainer_runtime",
]


class Credential(BaseModel):
    """API credential model"""

    id: int | None = None
    name: str  # Credential name, for example "Company GitHub Token"
    type: CredentialType
    token: str = ""  # API Token for tracker credentials, retained for compatibility
    secrets: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None  # Optional description
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def normalize_token_secret(self):
        if self.token and "token" not in self.secrets:
            self.secrets["token"] = self.token
        if not self.token and isinstance(self.secrets.get("token"), str):
            self.token = self.secrets["token"]
        return self


class Notifier(BaseModel):
    """Notifier configuration model"""

    id: int | None = None
    name: str
    type: str = "webhook"  # Currently only webhook is supported
    url: str
    events: list[str] = Field(default_factory=lambda: ["new_release"])
    enabled: bool = True
    language: Literal["en", "zh"] = "en"
    description: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ReleaseHistory(BaseModel):
    """Release history model"""

    id: int | None = None
    release_id: int
    commit_sha: str
    published_at: datetime
    body: str | None = None
    channel_name: str | None = None
    recorded_at: datetime = Field(default_factory=datetime.now)


class ExecutorStatus(BaseModel):
    id: int | None = None
    executor_id: int
    last_run_at: datetime | None = None
    last_result: str | None = None
    last_error: str | None = None
    last_version: str | None = None
    updated_at: datetime = Field(default_factory=datetime.now)


class ExecutorRunHistory(BaseModel):
    id: int | None = None
    executor_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["queued", "running", "success", "failed", "skipped"]
    from_version: str | None = None
    to_version: str | None = None
    message: str | None = None
    diagnostics: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class ExecutorSnapshot(BaseModel):
    id: int | None = None
    executor_id: int
    snapshot_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ExecutorDesiredState(BaseModel):
    id: int | None = None
    executor_id: int
    desired_state_revision: str
    desired_target: dict[str, Any] = Field(default_factory=dict)
    desired_target_fingerprint: str
    pending: bool = True
    next_eligible_at: datetime | None = None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    claim_until: datetime | None = None
    last_completed_revision: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ==================== Auth Models ====================


class User(BaseModel):
    """User model"""

    id: int | None = None
    username: str
    email: str
    password_hash: str | None = None  # OIDC users have no password
    oauth_provider: str | None = None  # OIDC provider slug
    oauth_sub: str | None = None  # OIDC Subject（unique identifier）
    avatar_url: str | None = None
    status: str = "active"  # active, inactive
    created_at: datetime = Field(default_factory=datetime.now)
    last_login_at: datetime | None = None


class Session(BaseModel):
    """Session model"""

    id: int | None = None
    user_id: int
    token_hash: str
    refresh_token_hash: str | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.now)


class TokenPair(BaseModel):
    """Token pair"""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class LoginRequest(BaseModel):
    """Login request"""

    username: str
    password: str


class RegisterRequest(BaseModel):
    """Register request"""

    username: str
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    """Change password request"""

    old_password: str
    new_password: str

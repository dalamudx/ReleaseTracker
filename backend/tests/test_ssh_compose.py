import pytest

from releasetracker.services.ssh_compose import (
    SSHComposeProject,
    image_provenance,
    load_yaml,
    dotenv_entries,
)


def analyze(expression, *, env="VERSION=1.2.3", process=None, extra=""):
    content = f"services:\n  web:\n    image: {expression}\n{extra}"
    return image_provenance(
        {"/app/compose.yml": content},
        {"services": {"web": {"image": "app:1.2.3"}}},
        {"/app/.env": env},
        process or {},
    )[0]


def test_literal_and_dotenv_provenance():
    literal = analyze("app:1.2.3")
    assert literal["source"] == "compose" and literal["safe_to_edit"]
    env = analyze("app:${VERSION}")
    assert env["source"] == "env_file" and env["safe_to_edit"]
    assert env["write_file"] == "/app/.env" and env["variable"] == "VERSION"


def test_container_environment_is_not_interpolation_environment():
    row = analyze(
        "app:${VERSION}",
        env="",
        extra="    environment:\n      VERSION: 1.2.3\n    env_file: container.env\n",
    )
    assert row["source"] == "default_or_unresolved"
    assert not row["safe_to_edit"]


def test_process_environment_precedence_and_secret_redaction():
    row = analyze(
        "app:${VERSION}",
        env="VERSION=0.1",
        process={"VERSION": "1.2.3", "PASSWORD": "secret-never-return"},
    )
    assert row["source"] == "process_environment"
    assert not row["safe_to_edit"]
    assert "secret-never-return" not in str(row)


@pytest.mark.parametrize(
    "expression,env",
    [
        ("app:${VERSION:-1.2.3}", ""),
        ("app:${VERSION}", "VERSION=1.2.3\nVERSION=1.2.3"),
        ("app:${MAJOR}.${MINOR}", ""),
        ("app:${VERSION}", "VERSION=${BASE}"),
        ("app:${VERSION}", "VERSION=9.0"),
    ],
)
def test_ambiguous_versions_not_marked_safe(expression, env):
    assert not analyze(expression, env=env)["safe_to_edit"]


def test_shared_variable_cannot_mutate_unrelated_settings():
    row = analyze("app:${VERSION}", extra="    volumes:\n      - /data/${VERSION}:/data\n")
    assert not row["safe_to_edit"]
    assert "shared_variable" in row["warnings"]


def test_explicit_environment_file_order():
    row = image_provenance(
        {"compose.yml": "services:\n  web:\n    image: app:${VERSION}"},
        {"services": {"web": {"image": "app:2"}}},
        {"base.env": "VERSION=1", "prod.env": "VERSION=2"},
        {},
    )[0]
    assert row["safe_to_edit"] and row["write_file"] == "prod.env"


def test_compose_override_file_order():
    row = image_provenance(
        {
            "base.yml": "services:\n  web:\n    image: app:1",
            "prod.yml": "services:\n  web:\n    image: app:2",
        },
        {"services": {"web": {"image": "app:2"}}},
        {},
        {},
    )[0]
    assert row["safe_to_edit"] and row["write_file"] == "prod.yml"


@pytest.mark.parametrize("text", ["x: &a [*a]", "services: [", "- not-a-document"])
def test_unsafe_yaml_rejected(text):
    with pytest.raises(ValueError):
        load_yaml(text)


def test_shell_paths_are_arguments_and_project_is_bounded():
    project = SSHComposeProject(
        working_dir="/app space",
        project="app",
        config_files=["compose;name.yml"],
        env_files=["prod.env"],
        tool="docker_compose",
    )
    assert project.argv("docker_compose") == [
        "docker",
        "compose",
        "--project-name",
        "app",
        "-f",
        "/app space/compose;name.yml",
        "--env-file",
        "/app space/prod.env",
    ]
    with pytest.raises(ValueError):
        SSHComposeProject(working_dir="relative", project="app", config_files=["compose.yml"])


@pytest.mark.asyncio
@pytest.mark.parametrize("multiple_tools", [False, True])
async def test_remote_analysis_has_no_mutating_commands_or_environment_leaks(
    monkeypatch, multiple_tools
):
    from contextlib import asynccontextmanager
    from releasetracker.services import ssh_compose
    from releasetracker.services.ssh_transport import SSHCommandResult

    calls = []

    class Session:
        async def run(self, argv, **kwargs):
            calls.append(argv)
            if argv[-1] == "version":
                available = multiple_tools or argv[:2] == ["docker", "compose"]
                return SSHCommandResult(0 if available else 127, "version", "")
            if "info" in argv:
                return SSHCommandResult(0, "engine-id", "")
            if argv[-1] == "config":
                return SSHCommandResult(
                    0,
                    "services:\n  web:\n    image: app:1.2.3\n    environment:\n      PASSWORD: hidden-env-value",
                    "",
                )
            assert argv == ["env", "-0"]
            return SSHCommandResult(0, "PASSWORD=hidden-env-value\x00", "")

        @asynccontextmanager
        async def sftp(self):
            yield object()

    @asynccontextmanager
    async def open_session(*_):
        yield Session()

    async def read_file(session, sftp, path, optional=False):
        assert path in {"/app/compose.yml", "/app/.env"}
        return (
            "VERSION=1.2.3\nSECRET=hidden-file-value"
            if path.endswith(".env")
            else "services:\n  web:\n    image: app:${VERSION}\n"
        )

    monkeypatch.setattr(ssh_compose, "open_ssh_session", open_session)
    monkeypatch.setattr(ssh_compose, "read_project_file", read_file)
    result = await ssh_compose.analyze_compose(
        None,
        None,
        SSHComposeProject(working_dir="/app", project="app", config_files=["compose.yml"]),
    )
    assert result["read_only"]
    assert result["requires_tool_selection"] is multiple_tools
    if not multiple_tools:
        assert result["services"][0]["safe_to_edit"]
        assert result["selected_tool"] == "docker_compose"
    else:
        assert not result["services"]
    assert "hidden-env-value" not in str(result) and "hidden-file-value" not in str(result)
    assert not any("pull" in argv or "up" in argv or "down" in argv for argv in calls)


@pytest.mark.parametrize(
    "text",
    [
        "services: {}\nservices: {}",
        "services: [bad]",
        "services:\n  web:\n    image: app:1\n    extends: other",
    ],
)
def test_duplicate_or_inherited_services_fail_closed(text):
    with pytest.raises(ValueError):
        image_provenance({"compose.yml": text}, {"services": {}}, {}, {})


def test_dotenv_quoted_and_empty_values():
    assert dotenv_entries("export VERSION='1.2.3'")["VERSION"] == {"value": "1.2.3", "simple": True}
    assert dotenv_entries("VERSION=\n")["VERSION"]["value"] == ""

import copy
import pytest

from releasetracker.services.ssh_compose_plan import SSHComposeTarget, build_plan


def target(**kwargs):
    return SSHComposeTarget(
        working_dir="/app",
        project="app",
        config_files=["compose.yml"],
        tool="docker_compose",
        **kwargs,
    )


def test_requires_fixed_tool():
    with pytest.raises(ValueError):
        SSHComposeTarget(working_dir="/app", project="app", config_files=["compose.yml"])


def test_literal_patch_preserves_comments_secrets_and_other_fields():
    text = '# comment\nservices:\n  web:\n    image: "app:1" # image\n    environment:\n      SECRET: hidden-secret\n'
    rendered = {"services": {"web": {"image": "app:1", "environment": {"SECRET": "hidden-secret"}}}}
    plan = build_plan(target(), {"/app/compose.yml": text}, rendered, {}, {}, {"web": "app:2"})
    assert plan.changes[0].after == text.replace('"app:1"', '"app:2"')
    assert "hidden-secret" not in str(plan) + str(plan.public_summary())
    expected = copy.deepcopy(rendered)
    expected["services"]["web"]["image"] = "app:2"
    plan.validate_rendered(expected)
    expected["services"]["web"]["environment"]["SECRET"] = "changed"
    with pytest.raises(ValueError, match="non_image"):
        plan.validate_rendered(expected)


def test_managed_markers_are_part_of_the_single_compose_file_plan():
    source = "services:\n  web:\n    image: app:1\n"
    markers = {
        "releasetracker.io/managed-by": "installation-test",
        "releasetracker.io/target-id": "target-test",
        "releasetracker.io/schema": "1",
    }
    plan = build_plan(
        target(),
        {"/app/compose.yml": source},
        {"services": {"web": {"image": "app:1"}}},
        {},
        {},
        {"web": "app:2"},
        managed_markers=markers,
    )
    assert len(plan.changes) == 1
    assert "releasetracker.io/target-id: target-test" in plan.changes[0].after
    expected = {"services": {"web": {"image": "app:2", "labels": markers}}}
    plan.validate_rendered(expected)


def test_managed_markers_do_not_turn_dotenv_change_into_yaml():
    markers = {
        "releasetracker.io/managed-by": "installation-test",
        "releasetracker.io/target-id": "target-test",
        "releasetracker.io/schema": "1",
    }
    with pytest.raises(ValueError, match="managed_markers_require_override"):
        build_plan(
            target(),
            {"/app/compose.yml": "services:\n  web:\n    image: app:${VERSION}\n"},
            {"services": {"web": {"image": "app:1"}}},
            {"/app/.env": "VERSION=1\n"},
            {},
            {"web": "app:2"},
            managed_markers=markers,
        )


def test_env_patch_preserves_other_values_and_crlf():
    plan = build_plan(
        target(),
        {"/app/compose.yml": "services:\n  web:\n    image: app:${VERSION}\n"},
        {"services": {"web": {"image": "app:1"}}},
        {"/app/.env": "# release\r\nVERSION=1\r\nSECRET=hidden\r\n"},
        {},
        {"web": "app:2"},
    )
    assert plan.changes[0].path == "/app/.env"
    assert plan.changes[0].after == "# release\r\nVERSION=2\r\nSECRET=hidden\r\n"


@pytest.mark.parametrize(
    "expression,environment,extra",
    [
        ("app:${VERSION}", {"VERSION": "1"}, ""),
        ("app:${VERSION}", {}, "    environment:\n      VERSION: ${VERSION}\n"),
        ("app:${VERSION:-1}", {}, ""),
    ],
)
def test_ambiguous_sources_require_explicit_override(expression, environment, extra):
    files = {"/app/compose.yml": f"services:\n  web:\n    image: {expression}\n" + extra}
    env = {} if ":-" in expression else {"/app/.env": "VERSION=1\n"}
    with pytest.raises(ValueError):
        build_plan(
            target(),
            files,
            {"services": {"web": {"image": "app:1"}}},
            env,
            environment,
            {"web": "app:2"},
        )
    plan = build_plan(
        target(write_strategy="override"),
        files,
        {"services": {"web": {"image": "app:1"}}},
        env,
        environment,
        {"web": "app@sha256:" + "a" * 64},
    )
    assert len(plan.changes) == 1 and plan.changes[0].path.endswith(".override.yml")


def test_digest_not_inserted_into_tag_variable():
    with pytest.raises(ValueError):
        build_plan(
            target(),
            {"/app/compose.yml": "services:\n  web:\n    image: app:${VERSION}\n"},
            {"services": {"web": {"image": "app:1"}}},
            {"/app/.env": "VERSION=1"},
            {},
            {"web": "app@sha256:" + "a" * 64},
        )


def test_full_image_variable_accepts_digest():
    plan = build_plan(
        target(),
        {"/app/compose.yml": "services:\n  web:\n    image: ${IMAGE}\n"},
        {"services": {"web": {"image": "app:1"}}},
        {"/app/.env": "IMAGE=app:1"},
        {},
        {"web": "app@sha256:" + "a" * 64},
    )
    assert plan.changes[0].after == "IMAGE=app@sha256:" + "a" * 64


def test_multiple_write_files_rejected_and_all_input_hashes_in_identity():
    files = {
        "/app/compose.yml": "services:\n  web:\n    image: app:1\n  api:\n    image: api:${VERSION}\n"
    }
    rendered = {"services": {"web": {"image": "app:1"}, "api": {"image": "api:1"}}}
    with pytest.raises(ValueError, match="multiple_write_files"):
        build_plan(
            target(),
            files,
            rendered,
            {"/app/.env": "VERSION=1"},
            {},
            {"web": "app:2", "api": "api:2"},
        )
    a = build_plan(target(), files, rendered, {"/app/.env": "VERSION=1"}, {}, {"web": "app:2"})
    b = build_plan(
        target(), files, rendered, {"/app/.env": "VERSION=1\n# edited"}, {}, {"web": "app:2"}
    )
    assert a.public_summary()["plan_id"] != b.public_summary()["plan_id"]


@pytest.mark.parametrize("image", ["-x", "app:2;touch /tmp/bad", "app:$(id)", "app:2\n"])
def test_image_arguments_fail_closed(image):
    with pytest.raises(ValueError):
        build_plan(
            target(),
            {"/app/compose.yml": "services:\n  web:\n    image: app:1\n"},
            {"services": {"web": {"image": "app:1"}}},
            {},
            {},
            {"web": image},
        )

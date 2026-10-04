"""Socket inspect -> persistent JSON -> native creation must preserve semantics."""

from copy import deepcopy
from types import SimpleNamespace

from docker.models.containers import _create_container_args
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.executors.compose_runtime_update import build_grouped_runtime_recreate_spec
from releasetracker.executors.docker import DockerRuntimeAdapter
from releasetracker.executors.podman import PodmanRuntimeAdapter


def inspected(config=None, host=None, mounts=None):
    return SimpleNamespace(
        id="a" * 64,
        name="isolated-fidelity",
        attrs={
            "Image": "sha256:" + "b" * 64,
            "Config": config or {},
            "HostConfig": host or {},
            "Mounts": mounts or [],
            "NetworkSettings": {"Networks": {}},
        },
    )


def recreate(container, runtime="docker"):
    return build_grouped_runtime_recreate_spec(
        container, runtime_type=runtime, target_image="nginx:new", current_image="nginx:old"
    )


def adapter(runtime="docker", client=None):
    cls = DockerRuntimeAdapter if runtime == "docker" else PodmanRuntimeAdapter
    return cls(
        RuntimeConnectionConfig(
            name="isolated", type=runtime, config={"socket": "unix:///never-connect"}, secrets={}
        ),
        client=client or SimpleNamespace(),
    )


@pytest.mark.parametrize(
    "field,value,parameter",
    [
        ("Privileged", True, "privileged"),
        ("ReadonlyRootfs", True, "read_only"),
        ("DnsOptions", ["ndots:1"], "dns_opt"),
        ("CpusetCpus", "0-1", "cpuset_cpus"),
        ("CpusetMems", "0", "cpuset_mems"),
        ("MemorySwap", -1, "memswap_limit"),
        ("MemorySwappiness", 0, "mem_swappiness"),
        ("OomKillDisable", True, "oom_kill_disable"),
        ("PidMode", "host", "pid_mode"),
        ("IpcMode", "host", "ipc_mode"),
        ("UsernsMode", "host", "userns_mode"),
        ("CgroupParent", "custom", "cgroup_parent"),
        ("Runtime", "nvidia", "runtime"),
        ("VolumesFrom", ["other:ro"], "volumes_from"),
        ("GroupAdd", ["44"], "group_add"),
    ],
)
def test_nondefault_host_fields_survive_mapping_and_actual_docker_sdk(field, value, parameter):
    config = recreate(inspected(host={field: value})).create_config
    assert config[parameter] == value
    _create_container_args({**deepcopy(config), "version": "1.45"})


@pytest.mark.parametrize(
    "binds",
    [
        ["/synthetic/data:/data:ro,Z,rshared"],
        ["/synthetic/shared:/first:ro", "/synthetic/shared:/second:rw"],
    ],
)
def test_binds_survive_sdk_without_option_loss_or_source_collision(binds):
    config = recreate(inspected(host={"Binds": binds})).create_config
    raw = _create_container_args({**deepcopy(config), "version": "1.45"})
    assert raw["host_config"]["Binds"] == binds


def test_existing_anonymous_volume_name_and_full_structured_mount_options_are_saved():
    actual = {
        "Type": "volume",
        "Name": "existing-anonymous",
        "Source": "/engine/volumes/existing-anonymous/_data",
        "Destination": "/data",
        "RW": True,
    }
    source = inspected(
        config={"Volumes": {"/data": {}}},
        host={
            "Mounts": [
                {
                    "Type": "volume",
                    "Target": "/data",
                    "VolumeOptions": {"NoCopy": True, "Subpath": "nested"},
                }
            ]
        },
        mounts=[actual],
    )
    config = recreate(source).create_config
    assert config["mounts"] == [
        {
            "Type": "volume",
            "Target": "/data",
            "Source": "existing-anonymous",
            "VolumeOptions": {"NoCopy": True, "Subpath": "nested"},
        }
    ]
    assert source.attrs["HostConfig"]["Mounts"][0].get("Source") is None


@pytest.mark.parametrize("binds", [[], ["/synthetic/config:/config:ro"]])
def test_podman_does_not_replace_structured_volumes_with_binds(binds):
    host = {
        "Binds": binds,
        "Mounts": [
            {"Type": "volume", "Source": "named-storage", "Target": "/data", "ReadOnly": False}
        ],
    }
    spec = recreate(inspected(host=host), "podman")
    value = adapter("podman")
    config = value._apply_podman_host_config_preservation(deepcopy(spec.create_config), host)
    payload = value._render_podman_create_payload(value._podman_container_create_config(config))
    value._sanitize_podman_rendered_create_payload(
        payload, source_named_volume_names=value._podman_source_named_volume_names(config)
    )
    assert any(
        item.get("Name") == "named-storage" and item.get("Dest") == "/data"
        for item in payload.get("volumes", [])
    )


def test_empty_values_are_distinct_from_missing_and_source_objects_are_not_aliased():
    source = inspected(config={"Env": [], "Entrypoint": [], "Cmd": [], "Labels": {}})
    spec = recreate(source)
    assert spec.create_config["environment"] == []
    assert spec.create_config["entrypoint"] == []
    assert spec.create_config["command"] == []
    spec.create_config["labels"]["unexpected"] = "mutated"
    assert source.attrs["Config"]["Labels"] == {}
    assert spec.snapshot_payload["create_config"]["labels"] == {}


def test_randomly_published_port_preserves_actual_binding_not_new_random_port():
    source = inspected(host={"PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]}})
    source.attrs["NetworkSettings"]["Ports"] = {
        "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32888"}]
    }
    assert recreate(source).create_config["ports"] == {"80/tcp": ("127.0.0.1", 32888)}
    assert source.attrs["HostConfig"]["PortBindings"]["80/tcp"][0]["HostPort"] == ""


@pytest.mark.parametrize(
    "defaults",
    [
        {"Env": ["UNREVIEWED=yes"]},
        {"Labels": {"added": "value"}},
        {"Volumes": {"/new": {}}},
        {"ExposedPorts": {"443/tcp": {}}},
        {"Healthcheck": {"Test": ["CMD", "true"]}},
        {"User": "1000"},
    ],
)
def test_target_image_defaults_cannot_silently_add_configuration(defaults):
    from releasetracker.executors.container_configuration import verify_image_defaults

    client = SimpleNamespace(
        images=SimpleNamespace(get=lambda _: SimpleNamespace(attrs={"Config": defaults}))
    )
    with pytest.raises(ValueError, match="unreviewed"):
        verify_image_defaults(client, {"image": "new", "environment": [], "labels": {}})


def test_invalid_native_podman_options_refused_before_stopping_container():

    # Native payload preflight is pure; unsupported options cannot reach stop.
    value = adapter("podman")
    with pytest.raises(ValueError, match="cannot preserve"):
        value._podman_create_arguments(
            {
                "image": "new",
                "mounts": [
                    {
                        "Type": "volume",
                        "Source": "existing",
                        "Target": "/data",
                        "VolumeOptions": {"Subpath": "nested"},
                    }
                ],
            }
        )


def test_native_devices_and_security_path_lists_survive_actual_sdk_render():
    client = SimpleNamespace(
        api=SimpleNamespace(_version="1.45", create_container=lambda **_: None)
    )
    source = inspected(
        host={
            "Devices": [
                {
                    "PathOnHost": "/dev/fuse",
                    "PathInContainer": "/dev/fuse",
                    "CgroupPermissions": "rwm",
                }
            ],
            "MaskedPaths": [],
            "ReadonlyPaths": ["/proc/sys"],
        }
    )
    spec = recreate(source)
    raw = adapter(client=client)._docker_create_arguments(client, spec.create_config)
    assert raw["host_config"]["Devices"] == source.attrs["HostConfig"]["Devices"]
    assert raw["host_config"]["MaskedPaths"] == []
    assert raw["host_config"]["ReadonlyPaths"] == ["/proc/sys"]


def test_empty_port_binding_retains_expose_only_not_publish():
    spec = recreate(
        inspected(
            config={"ExposedPorts": {"9000/tcp": {}}}, host={"PortBindings": {"9000/tcp": None}}
        )
    )
    assert spec.create_config["_releasetracker_exposed_ports"] == ["9000/tcp"]
    assert "ports" not in spec.create_config


def test_post_create_native_readback_rejects_missing_or_unrequested_fields():
    from releasetracker.executors.container_configuration import verify_created_configuration

    class NativeClient:
        __module__ = "docker.client"

    current = inspected(config={"Image": "new", "Env": ["APP=keep"]}, host={"ReadonlyRootfs": True})
    client = NativeClient()
    client.containers = SimpleNamespace(get=lambda _: current)
    value = adapter(client=client)
    expected = value._extract_create_kwargs(current, "new")
    verify_created_configuration(value, current, expected)
    current.attrs["HostConfig"]["ReadonlyRootfs"] = False
    with pytest.raises(RuntimeError, match="configuration does not match"):
        verify_created_configuration(value, current, expected)
    current.attrs["HostConfig"]["ReadonlyRootfs"] = True
    current.attrs["Config"]["Env"].append("UNREQUESTED=value")
    with pytest.raises(RuntimeError, match="configuration does not match"):
        verify_created_configuration(value, current, expected)


def test_podman_tmpfs_keeps_permissions_and_options_not_only_size():
    value = adapter("podman")
    kwargs, payload = value._podman_create_arguments(
        {"image": "new", "tmpfs": {"/tmp": "rw,nosuid,noexec,size=8388608,mode=1777"}}
    )
    assert payload["mounts"][0]["options"] == [
        "rw",
        "nosuid",
        "noexec",
        "size=8388608",
        "mode=1777",
    ]


def test_missing_existing_volume_cannot_be_silently_recreated():
    from releasetracker.executors.container_configuration import verify_existing_volumes

    def absent(_):
        raise RuntimeError("synthetic missing volume")

    client = SimpleNamespace(volumes=SimpleNamespace(get=absent))
    with pytest.raises(ValueError, match="refusing to create replacement data"):
        verify_existing_volumes(
            client,
            {"mounts": [{"Type": "volume", "Source": "must-already-exist", "Target": "/data"}]},
        )


def test_partial_health_config_cannot_inherit_new_target_image_timing():
    from releasetracker.executors.container_configuration import verify_image_defaults

    client = SimpleNamespace(
        images=SimpleNamespace(
            get=lambda _: SimpleNamespace(
                attrs={"Config": {"Healthcheck": {"Test": ["CMD", "true"], "Interval": 1000000000}}}
            )
        )
    )
    with pytest.raises(ValueError, match="unreviewed healthcheck"):
        verify_image_defaults(
            client, {"image": "new", "healthcheck": {"Test": ["CMD", "true"], "Interval": 0}}
        )


def test_repeated_environment_keys_are_not_normalized_away():
    from releasetracker.executors.container_configuration import configuration_projection

    expected = {"image": "new", "environment": ["A=1", "A=2"]}
    actual = {"image": "new", "environment": ["A=2"]}
    assert configuration_projection(adapter(), expected) != configuration_projection(
        adapter(), actual
    )


def test_stdio_flags_are_preserved_in_native_request_without_sdk_recomputation():
    calls = []
    from docker import APIClient

    native = APIClient(base_url="unix:///never-connect", version="1.45")
    api = SimpleNamespace(
        _version="1.45",
        create_container=lambda **_: None,
        create_container_config=native.create_container_config,
        create_container_from_config=lambda body, **kwargs: (calls.append(body) or {"Id": "new"}),
    )
    client = SimpleNamespace(
        api=api, containers=SimpleNamespace(get=lambda _: SimpleNamespace(id="new"))
    )
    config = {
        "AttachStdin": False,
        "AttachStdout": False,
        "AttachStderr": False,
        "StdinOnce": False,
        "OpenStdin": True,
    }
    expected = recreate(inspected(config=config)).create_config
    try:
        adapter(client=client)._create_docker_container(client, expected)
        assert {field: calls[0][field] for field in config} == config
    finally:
        native.close()


def test_duplicate_source_bind_list_native_config_declares_real_targets_not_mode_suffixes():
    client = SimpleNamespace(
        api=SimpleNamespace(_version="1.45", create_container=lambda **_: None)
    )
    config = recreate(
        inspected(host={"Binds": ["/source:/first:ro,rprivate", "/source:/second:rw,Z"]})
    ).create_config
    raw = adapter(client=client)._docker_create_arguments(client, config)
    assert raw["volumes"] == ["/first", "/second"]
    assert raw["host_config"]["Binds"] == ["/source:/first:ro,rprivate", "/source:/second:rw,Z"]


@pytest.mark.parametrize(
    "field,value", [("ContainerIDFile", "/synthetic/pid-id"), ("LxcConf", {"unsupported": "value"})]
)
def test_known_unsupported_nonempty_settings_refuse_instead_of_vanishing(field, value):
    with pytest.raises(ValueError, match="cannot be safely preserved"):
        recreate(inspected(host={field: value}))


def test_podman_seccomp_is_not_accidentally_sent_as_a_selinux_option():
    with pytest.raises(ValueError, match="security option cannot be preserved"):
        adapter("podman")._podman_create_arguments(
            {"image": "new", "security_opt": ["seccomp=unconfined"]}
        )


@pytest.mark.parametrize("value", [-1, 0, 60, 100])
def test_podman_swappiness_unspecified_sentinel_is_not_sent_to_uint64_field(value):
    _, payload = adapter("podman")._podman_create_arguments(
        {"image": "new", "mem_limit": 128000000, "mem_swappiness": value}
    )
    memory = payload["resource_limits"]["memory"]
    if value == -1:
        assert "swappiness" not in memory
    else:
        assert memory["swappiness"] == value


@pytest.mark.parametrize(
    "binding,expected",
    [
        (
            ["127.0.0.1", 18080],
            [{"container_port": 80, "protocol": "tcp", "host_ip": "127.0.0.1", "host_port": 18080}],
        ),
        (
            ["::1", "18080"],
            [{"container_port": 80, "protocol": "tcp", "host_ip": "::1", "host_port": 18080}],
        ),
        (["", 18080], [{"container_port": 80, "protocol": "tcp", "host_port": 18080}]),
        (
            [18080, 18081],
            [
                {"container_port": 80, "protocol": "tcp", "host_port": 18080},
                {"container_port": 80, "protocol": "tcp", "host_port": 18081},
            ],
        ),
        (
            [["127.0.0.1", 18080], ["::1", 18081]],
            [
                {
                    "container_port": 80,
                    "protocol": "tcp",
                    "host_ip": "127.0.0.1",
                    "host_port": 18080,
                },
                {"container_port": 80, "protocol": "tcp", "host_ip": "::1", "host_port": 18081},
            ],
        ),
    ],
)
def test_podman_json_snapshot_port_pairs_keep_interface_not_extra_or_all_interface_binding(
    binding, expected
):
    assert (
        adapter("podman")._pod_portmappings_from_create_config({"ports": {"80/tcp": binding}})
        == expected
    )


def test_native_readback_queue_markers_use_exact_planned_values_including_deployment_id():
    from releasetracker.executors.container_configuration import verify_created_configuration
    from releasetracker.services.deployment_plan import MANAGED_MARKERS, managed_markers

    class NativeClient:
        __module__ = "docker.client"

    markers = managed_markers("owned-install", "owned-target", 7)
    current = inspected(config={"Image": "new", "Labels": {"audit": "original", **markers}})
    client = NativeClient()
    client.containers = SimpleNamespace(get=lambda _: current)
    value = adapter(client=client)
    expected = {"image": "new", "name": current.name, "labels": {"audit": "original"}}
    token = MANAGED_MARKERS.set(markers)
    try:
        verify_created_configuration(value, current, expected)
        assert expected["labels"] == {"audit": "original"}
        current.attrs["Config"]["Labels"]["releasetracker.io/deployment-id"] = "8"
        with pytest.raises(RuntimeError, match="management markers do not match"):
            verify_created_configuration(value, current, expected)
        current.attrs["Config"]["Labels"]["releasetracker.io/deployment-id"] = "7"
        current.attrs["Config"]["Labels"]["unrequested"] = "must-not-appear"
        with pytest.raises(RuntimeError, match="configuration does not match"):
            verify_created_configuration(value, current, expected)
    finally:
        MANAGED_MARKERS.reset(token)


@pytest.mark.parametrize("value", [-2, 101, True])
def test_invalid_podman_swappiness_fails_during_payload_preflight(value):
    with pytest.raises(ValueError, match="swappiness must be between"):
        adapter("podman")._podman_create_arguments({"image": "new", "mem_swappiness": value})


def test_native_stop_timeout_and_no_sdk_proxy_injection():
    spec = recreate(inspected(config={"StopTimeout": 45}))
    assert spec.create_config["stop_timeout"] == 45
    assert adapter()._sanitize_docker_create_kwargs(spec.create_config)["use_config_proxy"] is False


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_unknown_meaningful_mount_type_is_refused_before_create(runtime):
    with pytest.raises(ValueError, match="mount"):
        recreate(
            inspected(
                host={"Mounts": [{"Type": "cluster", "Target": "/data", "Source": "source"}]}
            ),
            runtime,
        )

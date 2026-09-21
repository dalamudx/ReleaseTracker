from contextlib import asynccontextmanager

import asyncssh
import pytest

from releasetracker.config import RuntimeConnectionConfig
from releasetracker.models import Credential
from releasetracker.services.ssh_transport import (
    SSHOperationError,
    discover_host_key,
    open_ssh_session,
    test_ssh_connection as probe_ssh,
)


async def credential(storage):
    return await storage.create_credential(
        Credential(
            name="ssh-secret",
            type="ssh",
            secrets={"auth_method": "password", "password": " loopback-test-secret "},
        )
    )


def config(credential_id, **kwargs):
    return RuntimeConnectionConfig(
        name=kwargs.pop("name", "target"),
        type="ssh",
        credential_id=credential_id,
        config={"host": "127.0.0.1", "username": "tester", **kwargs},
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/runtime-connections/ssh/host-key",
        "/api/runtime-connections/ssh/test",
        "/api/executors/ssh/compose/discover",
        "/api/executors/ssh/compose/analyze",
    ],
)
def test_ssh_endpoints_require_authentication(client, endpoint):
    assert client.post(endpoint, json={}).status_code == 401


@pytest.mark.asyncio
async def test_single_hop_relationships_and_referenced_proxy_mutations(storage):
    cid = await credential(storage)
    proxy = config(cid, name="proxy", allow_proxy=True)
    pid = await storage.create_runtime_connection(proxy)
    target = config(cid, proxy_connection_id=pid)
    tid = await storage.create_runtime_connection(target)
    for change in ({"enabled": False}, {"config": {**proxy.config, "allow_proxy": False}}):
        with pytest.raises(ValueError, match="referenced"):
            await storage.update_runtime_connection(pid, proxy.model_copy(update=change))
    with pytest.raises(ValueError, match="referenced"):
        await storage.delete_runtime_connection(pid)
    with pytest.raises(ValueError, match="itself"):
        await storage.update_runtime_connection(tid, config(cid, proxy_connection_id=tid))
    with pytest.raises(ValueError, match="allowing proxy"):
        await storage.create_runtime_connection(config(cid, name="third", proxy_connection_id=tid))
    with pytest.raises(ValueError, match="one hop"):
        config(cid, proxy_connection_id=pid, allow_proxy=True)
    await storage.delete_runtime_connection(tid)
    assert await storage.delete_runtime_connection(pid)


@pytest.mark.asyncio
async def test_ssh_credential_encrypted_masked_and_blank_edit_preserved(authed_client, storage):
    cid = await credential(storage)
    response = authed_client.get(f"/api/credentials/{cid}")
    assert response.status_code == 200
    assert response.json()["secrets"]["password"] == "****"
    assert "loopback" not in response.text
    response = authed_client.put(f"/api/credentials/{cid}", json={"secrets": {"password": ""}})
    assert response.status_code == 200
    assert (await storage.get_credential(cid)).secrets["password"] == " loopback-test-secret "
    db = await storage._get_connection()
    row = await (
        await db.execute("SELECT secrets FROM credentials WHERE id = ?", (cid,))
    ).fetchone()
    assert "loopback-test-secret" not in row[0]


@pytest.mark.asyncio
async def test_ssh_credentials_participate_in_encryption_key_rotation(storage, system_key_manager):
    from releasetracker.services.system_keys import rotate_encryption_key

    cid = await credential(storage)
    before = (
        await (
            await (await storage._get_connection()).execute(
                "SELECT secrets FROM credentials WHERE id = ?", (cid,)
            )
        ).fetchone()
    )[0]
    await rotate_encryption_key(storage, system_key_manager, generate=True)
    assert (await storage.get_credential(cid)).secrets["password"] == " loopback-test-secret "
    after = (
        await (
            await (await storage._get_connection()).execute(
                "SELECT secrets FROM credentials WHERE id = ?", (cid,)
            )
        ).fetchone()
    )[0]
    assert before != after and "loopback-test-secret" not in after


@pytest.mark.asyncio
async def test_ssh_api_rejects_deleting_referenced_proxy(authed_client, storage):
    cid = await credential(storage)
    pid = await storage.create_runtime_connection(config(cid, name="proxy", allow_proxy=True))
    await storage.create_runtime_connection(config(cid, proxy_connection_id=pid))
    assert authed_client.delete(f"/api/runtime-connections/{pid}").status_code == 409


@pytest.mark.asyncio
async def test_missing_host_key_blocks_before_network(storage, monkeypatch):
    cid = await credential(storage)

    def unexpected(*args, **kwargs):
        pytest.fail("unverified host must not receive authentication")

    monkeypatch.setattr(asyncssh, "connect", unexpected)
    with pytest.raises(SSHOperationError, match="confirmation_required"):
        async with open_ssh_session(storage, config(cid)):
            pass


class LoopbackServer(asyncssh.SSHServer):
    def __init__(self, public_key=None, allow_forward=True):
        self.public_key = public_key
        self.allow_forward = allow_forward

    def public_key_auth_supported(self):
        return self.public_key is not None

    def validate_public_key(self, username, key):
        return username == "tester" and key == self.public_key

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "tester" and password == " loopback-test-secret "

    def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
        return self.allow_forward and dest_host == "127.0.0.1"


@asynccontextmanager
async def server(tmp_path, *, client_key=None, allow_forward=True):
    key = asyncssh.generate_private_key("ssh-ed25519")

    async def process(proc):
        proc.stdout.write("ok\n")
        proc.exit(0)

    async with asyncssh.listen(
        "127.0.0.1",
        0,
        server_factory=lambda: LoopbackServer(client_key, allow_forward),
        server_host_keys=[key],
        process_factory=process,
        gss_host=None,
        sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(tmp_path)),
    ) as listener:
        yield listener.get_port(), key.export_public_key().decode().strip()


@pytest.mark.asyncio
@pytest.mark.parametrize("via_proxy", [False, True])
async def test_real_loopback_password_host_key_sftp_and_single_hop(storage, tmp_path, via_proxy):
    cid = await credential(storage)
    async with server(tmp_path) as (port, key):
        target = config(cid, port=port, host_key=key)
        if via_proxy:
            proxy = config(cid, name="jump", port=port, host_key=key, allow_proxy=True)
            target.config["proxy_connection_id"] = await storage.create_runtime_connection(proxy)
        discovered = await discover_host_key(storage, target)
        assert discovered["host_key"] == key
        assert discovered["verified"] is False
        assert (await probe_ssh(storage, target))["success"]
        wrong_key = (
            asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
        )
        target.config["host_key"] = wrong_key
        with pytest.raises(SSHOperationError, match="host_key_mismatch"):
            await probe_ssh(storage, target)


@pytest.mark.asyncio
async def test_referenced_ssh_credential_cannot_change_type(storage):
    cid = await credential(storage)
    await storage.create_runtime_connection(config(cid))
    with pytest.raises(ValueError, match="cannot change type"):
        await storage.update_credential(
            cid, Credential(name="ssh-secret", type="github", token="x")
        )


@pytest.mark.asyncio
async def test_real_encrypted_private_key_and_sftp_file_bounds(storage, tmp_path):
    from releasetracker.services.ssh_compose import read_project_file

    key = asyncssh.generate_private_key("ssh-ed25519")
    cid = await storage.create_credential(
        Credential(
            name="private",
            type="ssh",
            secrets={
                "auth_method": "private_key",
                "private_key": key.export_private_key(passphrase="unlock").decode(),
                "passphrase": "unlock",
            },
        )
    )
    (tmp_path / "compose.yml").write_text("services: {}")
    (tmp_path / "symlink.yml").symlink_to("compose.yml")
    (tmp_path / "large.yml").write_bytes(b"a" * (1024 * 1024 + 1))
    async with server(tmp_path, client_key=key.convert_to_public()) as (port, host_key):
        async with open_ssh_session(storage, config(cid, port=port, host_key=host_key)) as session:
            async with session.sftp() as sftp:
                assert await read_project_file(session, sftp, "/compose.yml") == "services: {}"
                assert await read_project_file(session, sftp, "/missing", optional=True) is None
                for name in ("symlink.yml", "large.yml"):
                    with pytest.raises(SSHOperationError):
                        await read_project_file(session, sftp, "/" + name)


@pytest.mark.asyncio
async def test_proxy_forwarding_denial_is_reported_without_secret(storage, tmp_path):
    cid = await credential(storage)
    async with server(tmp_path, allow_forward=False) as (port, host_key):
        pid = await storage.create_runtime_connection(
            config(cid, name="proxy", port=port, host_key=host_key, allow_proxy=True)
        )
        with pytest.raises(SSHOperationError, match="forwarding_or_target_unavailable") as error:
            await probe_ssh(
                storage, config(cid, port=port, host_key=host_key, proxy_connection_id=pid)
            )
        assert "loopback-test-secret" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"port": 0},
        {"port": True},
        {"host": "x\ny"},
        {"host": "ssh://host"},
        {"host_key": "invalid"},
    ],
)
def test_invalid_ssh_settings(changes):
    with pytest.raises(ValueError):
        config(1, **changes)

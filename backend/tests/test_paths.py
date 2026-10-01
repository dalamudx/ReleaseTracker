from releasetracker import paths


def test_default_paths_live_under_backend_data(monkeypatch):
    monkeypatch.delenv(paths.DB_PATH_ENV, raising=False)
    assert paths.database_path() == paths.backend_dir() / "data" / "releases.db"
    assert paths.system_secrets_path() == paths.backend_dir() / "data" / "system-secrets.json"


def test_db_path_env_matches_entrypoint_and_keeps_keys_beside_database(monkeypatch, tmp_path):
    db_path = tmp_path / "state" / "rt.db"
    monkeypatch.setenv(paths.DB_PATH_ENV, f"  {db_path}  ")
    assert paths.database_path() == db_path
    assert paths.system_secrets_path() == db_path.parent / "system-secrets.json"


def test_entrypoint_uses_same_environment_variable():
    script = (paths.backend_dir() / "scripts" / "docker-entrypoint.sh").read_text()
    assert f"${{{paths.DB_PATH_ENV}:-/app/backend/data/releases.db}}" in script

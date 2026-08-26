from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def test_bootstrap_and_project_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("PAS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'config' / 'test.db'}")

    import app.core.config as config
    import app.db.session as session
    import app.db.init_db as init_db
    import app.main as main

    importlib.reload(config)
    importlib.reload(session)
    importlib.reload(init_db)
    importlib.reload(main)

    client = TestClient(main.create_app())
    assert client.get("/api/bootstrap").json() == {"initialized": False}

    response = client.post("/api/bootstrap", json={"email": "admin@example.com", "password": "long-password"})
    assert response.status_code == 200
    assert response.json()["user"]["is_admin"] is True

    project_response = client.post("/api/projects", json={"title": "Episode clip"})
    assert project_response.status_code == 200
    project = project_response.json()["project"]
    assert project["title"] == "Episode clip"

    render_response = client.post(f"/api/projects/{project['id']}/render")
    assert render_response.status_code == 200
    assert render_response.json()["job"]["kind"] == "render"


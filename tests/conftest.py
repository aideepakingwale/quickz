import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quickz.config import Settings  # noqa: E402
from quickz.server import create_app  # noqa: E402

SETUP_TOKEN = "setup-token-123"
ADMIN = {"username": "boss", "display_name": "Boss", "password": "correct horse battery"}
CSRF = {"X-Quickz": "1"}


def make_app(tmp_path, guard=None):
    return create_app(Settings(data_dir=tmp_path, public_url="http://192.168.1.50:8000", setup_token=SETUP_TOKEN), guard)


def setup_admin(client, **over):
    """Complete first-run setup (idempotent: signs in if setup was already done)."""
    body = {"token": SETUP_TOKEN, **ADMIN, **over}
    r = client.post("/api/auth/setup", json={"token": body["token"], "username": body["username"],
                                              "display_name": body["display_name"], "password": body["password"]})
    if r.status_code == 409:
        r = client.post("/api/auth/login", json={"username": body["username"], "password": body["password"]})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def create_host(admin_client, username, display_name=None, password="a-long-host-password"):
    """Admin creates a host; returns (id, final password) after the host picks their own password."""
    r = admin_client.post("/api/users", json={"username": username, "display_name": display_name or username.title()})
    assert r.status_code == 200, r.text
    return r.json()["id"], r.json()["temp_password"]


def sign_in(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def host_session(app, admin_client, username, password="a-long-host-password"):
    """A TestClient signed in as a freshly created host who already chose their own password."""
    uid, temp = create_host(admin_client, username)
    c = TestClient(app, headers=CSRF)
    sign_in(c, username, temp)
    assert c.post("/api/auth/password", json={"current": temp, "new": password}).status_code == 200
    return uid, c


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def client(app):
    """A signed-in Super Admin (the original single-host behaviour, now with real accounts)."""
    with TestClient(app, headers=CSRF) as c:
        setup_admin(c)
        yield c

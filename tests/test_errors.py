"""Uploads survive a briefly locked file; real storage problems and unexpected crashes say what happened."""
import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import CSRF, SETUP_TOKEN, make_app, setup_admin

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def c(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app, headers=CSRF, raise_server_exceptions=False) as client:
        setup_admin(client)
        client.tmp = tmp_path
        yield client


def test_a_briefly_locked_file_is_retried(c, monkeypatch):
    real, calls = Path.replace, {"n": 0}

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] <= 2:                      # antivirus / indexer still holding the new file
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    r = c.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 200 and calls["n"] == 3
    assert (c.tmp / "uploads" / r.json()["url"].rsplit("/", 1)[1]).is_file()
    assert not list((c.tmp / "uploads").glob("*.part"))


def test_a_storage_problem_is_explained_and_leaves_nothing_behind(c, monkeypatch):
    def broken(self, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", broken)
    r = c.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "storage" and "No space left on device" in body["error"] and "free space" in body["error"]
    assert not list((c.tmp / "uploads").glob("*.part"))                       # the half-written file is cleaned up


def test_unexpected_crashes_get_a_reference_and_a_log_entry(c):
    @c.app.get("/api/boom")
    async def boom():
        raise RuntimeError("kaboom-12345")

    r = c.get("/api/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "server_error" and len(body["ref"]) == 6 and body["ref"] in body["error"] and "kaboom" not in body["error"]   # no internals leak to the browser
    log = (c.tmp / "errors.log").read_text(encoding="utf-8")
    assert body["ref"] in log and "kaboom-12345" in log and "Traceback" in log and "/api/boom" in log


def test_normal_errors_are_unchanged(c):
    assert c.post("/api/uploads", content=b"not an image at all, sorry", headers={"Content-Type": "image/png"}).status_code == 400
    assert c.get("/api/quizzes/999").status_code == 404

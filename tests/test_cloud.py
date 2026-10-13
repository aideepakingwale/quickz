"""Cloud persistence: SigV4 correctness, snapshot/restore of the database, lazy media recovery, failure safety."""
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN, CSRF, SETUP_TOKEN, setup_admin
from quickz.cloud import CloudError, CloudSync, S3Client, S3Config, sign_v4
from quickz.config import Settings
from quickz.server import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
QUIZ = "# Persisted\nQ: One\n- A\n- B*\n"


# ------------------------------------------------------------------ a tiny in-memory S3
class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        self.fail = 0            # next N requests answer HTTP 500
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _auth_ok(self):
                a = self.headers.get("Authorization", "")
                return a.startswith("AWS4-HMAC-SHA256 Credential=KEYID/") and "Signature=" in a and self.headers.get("x-amz-content-sha256")

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if outer.fail:
                    outer.fail -= 1
                    return self._reply(500, b"boom")
                if not self._auth_ok() or hashlib.sha256(body).hexdigest() != self.headers["x-amz-content-sha256"]:
                    return self._reply(403, b"bad signature")
                outer.objects[self.path] = body
                outer.puts += 1
                self._reply(200, b"")

            def do_GET(self):
                if outer.fail:
                    outer.fail -= 1
                    return self._reply(500, b"boom")
                if not self._auth_ok():
                    return self._reply(403, b"bad signature")
                blob = outer.objects.get(self.path)
                self._reply(200, blob) if blob is not None else self._reply(404, b"<Error><Code>NoSuchKey</Code></Error>")

            def _reply(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def cloud(self, **kw):
        return CloudSync(S3Client(S3Config(self.endpoint, "bucket", "KEYID", "SECRET", "auto", "qz")), **kw)

    def close(self):
        self.server.shutdown()


@pytest.fixture
def s3():
    f = FakeS3()
    yield f
    f.close()


def build(tmp_path, cloud):
    return create_app(Settings(data_dir=tmp_path, public_url="http://x", setup_token=SETUP_TOKEN), cloud=cloud)


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------------ signing
def test_sigv4_matches_the_aws_documentation_example():
    """GET Object example from 'Signature Calculations for the Authorization Header' in the S3 API docs."""
    h = sign_v4("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {"Range": "bytes=0-9"},
                hashlib.sha256(b"").hexdigest(), "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "us-east-1", "20130524T000000Z")
    assert h["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in h["authorization"]


def test_s3_client_roundtrip_and_missing_object(s3):
    c = s3.cloud().s3
    c.put("a/b.txt", b"hello")
    assert c.get("a/b.txt") == b"hello"
    assert c.get("a/missing") is None
    s3.fail = 1
    with pytest.raises(CloudError):
        c.get("a/b.txt")


def test_not_configured_means_no_cloud(monkeypatch):
    for k in ("QUICKZ_S3_ENDPOINT", "QUICKZ_S3_BUCKET", "QUICKZ_S3_KEY_ID", "QUICKZ_S3_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert CloudSync.from_env() is None
    monkeypatch.setenv("QUICKZ_S3_ENDPOINT", "https://x")
    assert CloudSync.from_env() is None            # needs all four


# ------------------------------------------------------------------ the whole point: survive a wiped disk
def test_a_wiped_disk_comes_back_with_accounts_quizzes_and_uploads(tmp_path, s3):
    disk1, disk2 = tmp_path / "one", tmp_path / "two"
    cloud1 = s3.cloud()
    with TestClient(build(disk1, cloud1), headers=CSRF) as c:
        setup_admin(c)
        qid = c.post("/api/quizzes/import", json={"text": QUIZ}).json()["id"]
        url = c.post("/api/uploads", content=PNG, headers={"content-type": "image/png"}).json()["url"]
        assert wait_for(lambda: any("/uploads/" in k for k in s3.objects)), "upload was not copied to the bucket"
        assert cloud1.snapshot_now(force=True)
    assert "/bucket/qz/db/quickz.db.gz" in s3.objects     # stop() also flushed a final snapshot

    # new instance, empty disk (what Render does on every restart / redeploy)
    cloud2 = s3.cloud()
    app2 = build(disk2, cloud2)
    assert cloud2.status["restored"] is True
    with TestClient(app2, headers=CSRF) as c2:
        r = c2.post("/api/auth/login", json={"username": ADMIN["username"], "password": ADMIN["password"]})
        assert r.status_code == 200                        # same account, same password hash
        assert [q["id"] for q in c2.get("/api/quizzes").json()] == [qid]
        assert not (disk2 / "uploads" / url.rsplit("/", 1)[1]).exists()   # not on disk yet...
        assert c2.get(url).content == PNG                  # ...but pulled back from the bucket on demand
        assert (disk2 / "uploads" / url.rsplit("/", 1)[1]).exists()       # and cached locally afterwards
        assert c2.get(url).headers["cache-control"].startswith("public")


def test_sessions_survive_a_restore(tmp_path, s3):
    with TestClient(build(tmp_path / "a", s3.cloud()), headers=CSRF) as c:
        setup_admin(c)
        cookies = dict(c.cookies)
    with TestClient(build(tmp_path / "b", s3.cloud()), headers=CSRF, cookies=cookies) as c2:
        assert c2.get("/api/auth/state").json()["user"]["username"] == ADMIN["username"]


def test_first_ever_boot_with_empty_bucket_starts_fresh(tmp_path, s3):
    cloud = s3.cloud()
    app = build(tmp_path, cloud)
    assert cloud.status["restored"] is False
    with TestClient(app, headers=CSRF) as c:
        assert c.get("/api/auth/state").json()["setup_needed"] is True


def test_refuses_to_start_empty_when_the_bucket_cannot_be_read(tmp_path, s3):
    """Otherwise the next snapshot would overwrite good remote data with an empty database."""
    s3.fail = 5
    with pytest.raises(CloudError, match="Refusing to start"):
        build(tmp_path, s3.cloud())
    assert not (tmp_path / "quickz.db").exists()      # nothing was created that could later overwrite the bucket


def test_existing_local_database_is_kept_and_never_overwritten_by_restore(tmp_path, s3):
    with TestClient(build(tmp_path, s3.cloud()), headers=CSRF) as c:
        setup_admin(c)
    s3.objects.clear()                                      # bucket lost its data, disk still has it
    cloud = s3.cloud()
    app = build(tmp_path, cloud)
    assert cloud.status["restored"] is False
    with TestClient(app, headers=CSRF) as c:
        assert c.post("/api/auth/login", json={"username": ADMIN["username"], "password": ADMIN["password"]}).status_code == 200
    assert "/bucket/qz/db/quickz.db.gz" in s3.objects       # ...and it repopulated the bucket


# ------------------------------------------------------------------ snapshots
def test_snapshots_only_when_something_changed_and_survive_outages(tmp_path, s3):
    cloud = s3.cloud()
    with TestClient(build(tmp_path, cloud), headers=CSRF) as c:
        setup_admin(c)
        assert cloud.snapshot_now() is True                 # something changed since boot
        puts = s3.puts
        assert cloud.snapshot_now() is False                # nothing changed -> no upload
        assert s3.puts == puts
        c.post("/api/quizzes/import", json={"text": QUIZ})
        s3.fail = 1                                         # bucket hiccup: must not raise or disturb the app
        assert cloud.snapshot_now() is False and cloud.status["last_error"]
        assert c.get("/api/quizzes").status_code == 200
        assert cloud.snapshot_now() is True                 # next attempt succeeds and clears the error
        assert cloud.status["last_error"] is None and cloud.status["last_ok"]


def test_backup_status_is_admin_only(tmp_path, s3):
    from conftest import host_session
    app = build(tmp_path, s3.cloud())
    with TestClient(app, headers=CSRF) as admin:
        setup_admin(admin)
        st = admin.get("/api/backup/status").json()
        assert st["enabled"] and st["bucket"] == "bucket"
        assert "SECRET" not in str(st) and "KEYID" not in str(st)         # never leak credentials
        _, host = host_session(app, admin, "hana")
        assert host.get("/api/backup/status").status_code == 403


def test_status_when_cloud_is_off(client):
    assert client.get("/api/backup/status").json() == {"enabled": False}


# ------------------------------------------------------------------ upload route safety
def test_upload_route_rejects_odd_names(client):
    for bad in ("..%2f..%2fetc%2fpasswd", "x.png", "0123456789abcdef.exe!", "0123456789abcdef", "%2e%2e", "0123456789ABCDEF.png"):
        assert client.get(f"/uploads/{bad}").status_code == 404, bad
    assert client.get("/uploads/0123456789abcdef.png").status_code == 404     # well-formed but absent

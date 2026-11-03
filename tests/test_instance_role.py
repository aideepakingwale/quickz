"""On an EC2 server the bucket is reached with the instance's IAM role: no access keys stored anywhere."""
import datetime as dt
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from conftest import CSRF, SETUP_TOKEN, setup_admin
from quickz.cloud import CloudError, CloudSync, RoleCredentials, S3Client, S3Config
from quickz.config import Settings
from quickz.server import create_app
from test_cloud import PNG, FakeS3, wait_for  # noqa: F401


def iso(seconds_from_now: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds_from_now)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeIMDS:
    """The instance metadata service (IMDSv2: a session token first, then the role name, then its credentials)."""

    def __init__(self, lifetime=3600, require_token=True):
        self.fetches = 0
        self.lifetime = lifetime
        self.token_headers = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_PUT(self):
                if self.path == "/latest/api/token":
                    return self._send(200, "imds-session-token")
                self._send(404, "")

            def do_GET(self):
                outer.token_headers.append(self.headers.get("X-aws-ec2-metadata-token"))
                if require_token and self.headers.get("X-aws-ec2-metadata-token") != "imds-session-token":
                    return self._send(401, "")                      # IMDSv2 only: no session token, no answer
                if self.path == "/latest/meta-data/iam/security-credentials/":
                    return self._send(200, "quickz-role\n")
                if self.path == "/latest/meta-data/iam/security-credentials/quickz-role":
                    outer.fetches += 1
                    return self._send(200, json.dumps({"Code": "Success", "AccessKeyId": "KEYID", "SecretAccessKey": "SECRET",
                                                       "Token": f"SESSION-{outer.fetches}", "Expiration": iso(outer.lifetime)}))
                self._send(404, "")

            def _send(self, code, body):
                b = body.encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def s3():
    f = FakeS3()
    yield f
    f.close()


@pytest.fixture
def imds(monkeypatch):
    f = FakeIMDS()
    monkeypatch.setenv("QUICKZ_IMDS_URL", f.url)
    yield f
    f.close()


def role_client(s3):
    return S3Client(S3Config(s3.endpoint, "bucket", "", "", "auto", "qz", use_role=True))


# ------------------------------------------------------------------ credentials
def test_requests_are_signed_with_the_roles_temporary_credentials(s3, imds):
    c = role_client(s3)
    c.put("a/b.txt", b"hello")
    assert c.get("a/b.txt") == b"hello"
    assert set(s3.tokens) == {"SESSION-1"}                       # every request carried the session token, and it was signed
    assert imds.token_headers and all(h == "imds-session-token" for h in imds.token_headers)


def test_credentials_are_cached_until_shortly_before_they_expire(s3, imds):
    c = role_client(s3)
    for i in range(5):
        c.put(f"k/{i}", b"x")
    assert imds.fetches == 1
    imds.lifetime = 120                                            # next batch is already inside the 5-minute renewal window
    c.roles._cred = None
    c.put("k/x", b"x")
    c.put("k/y", b"y")
    assert imds.fetches == 3


def test_a_rolled_over_session_is_renewed_and_the_request_retried(s3, imds):
    c = role_client(s3)
    c.put("a", b"1")
    s3.expired = 1
    assert c.get("a") == b"1"
    assert imds.fetches == 2 and s3.tokens[-1] == "SESSION-2"


def test_an_unreachable_metadata_service_gives_a_helpful_error(s3, monkeypatch):
    monkeypatch.setenv("QUICKZ_IMDS_URL", "http://127.0.0.1:9")
    with pytest.raises(CloudError, match="IAM role.*hop limit"):
        role_client(s3).put("a", b"1")


def test_the_metadata_service_must_answer_with_an_imdsv2_token(imds):
    rc = RoleCredentials(imds.url)
    assert rc.get()[0] == "KEYID"


# ------------------------------------------------------------------ configuration
def test_role_mode_needs_no_keys(monkeypatch):
    for k, v in {"QUICKZ_S3_ENDPOINT": "https://s3.eu-west-2.amazonaws.com", "QUICKZ_S3_BUCKET": "my-bucket", "QUICKZ_S3_USE_ROLE": "1"}.items():
        monkeypatch.setenv(k, v)
    for k in ("QUICKZ_S3_KEY_ID", "QUICKZ_S3_SECRET", "QUICKZ_S3_REGION"):
        monkeypatch.delenv(k, raising=False)
    cloud = CloudSync.from_env()
    assert cloud is not None and cloud.s3.cfg.use_role and cloud.s3.cfg.region == "eu-west-2"
    assert cloud.status["auth"] == "instance role"
    monkeypatch.delenv("QUICKZ_S3_USE_ROLE")
    assert CloudSync.from_env() is None                            # without the role flag, keys are still required


# ------------------------------------------------------------------ the whole thing: upload -> bucket -> CDN redirect
def test_uploads_go_to_the_bucket_with_the_role_and_phones_are_sent_to_the_cdn(tmp_path, s3, imds):
    app = create_app(Settings(data_dir=tmp_path, public_url="http://x", setup_token=SETUP_TOKEN, public_bucket_url="https://d111.cloudfront.net"), cloud=s3.cloud(role=True))
    with TestClient(app, headers=CSRF) as c:
        setup_admin(c)
        url = c.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).json()["url"]
        name = url.rsplit("/", 1)[1]
        assert wait_for(lambda: f"/bucket/qz/uploads/{name}" in s3.objects)
        assert "SESSION-1" in s3.tokens
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == f"https://d111.cloudfront.net/qz/uploads/{name}"
        assert c.get("/api/backup/status").json()["auth"] == "instance role"

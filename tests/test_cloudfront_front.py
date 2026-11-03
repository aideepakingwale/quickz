"""Behind CloudFront: only CloudFront may reach the server, and the visitor's https is remembered."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import CSRF, SETUP_TOKEN
from quickz.config import Settings
from quickz.server import create_app

SECRET = "stack-1234-uuid"
VIA = {"X-Origin-Verify": SECRET, "CloudFront-Forwarded-Proto": "https", "Host": "d111abcdef.cloudfront.net"}


def app_for(tmp_path, **kw):
    return create_app(Settings(data_dir=tmp_path, public_url="https://d111abcdef.cloudfront.net", setup_token=SETUP_TOKEN, trust_proxy=True, origin_secret=SECRET, **kw), cloud=False)


def test_requests_that_skip_cloudfront_are_refused(tmp_path):
    with TestClient(app_for(tmp_path), headers=CSRF) as c:
        for path in ("/", "/host", "/api/auth/state", "/static/css/base.css"):
            assert c.get(path).status_code == 403, path
        assert c.get("/api/auth/state", headers={"X-Origin-Verify": "guess"}).status_code == 403
        assert c.get("/api/auth/state", headers={"X-Origin-Verify": SECRET}).status_code == 200
        assert c.get("/healthz").status_code == 200                       # Docker's own check needs no secret


def test_websockets_need_the_secret_too(tmp_path):
    with TestClient(app_for(tmp_path), headers=CSRF) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws/screen/12345"):
                pass
        # with the secret the request is let through (this game does not exist, so the app itself then says 4404)
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/screen/12345", headers={"X-Origin-Verify": SECRET}):
                pass
        assert e.value.code == 4404


def test_the_visitors_https_survives_the_plain_http_hop(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app, headers=CSRF) as c:
        r = c.post("/api/auth/setup", headers=VIA, json={"token": SETUP_TOKEN, "username": "boss", "display_name": "B", "password": "correct horse battery"})
        assert r.status_code == 200
        assert "secure" in r.headers["set-cookie"].lower()                # the session cookie is marked Secure
        # (the test client speaks plain http, so it rightly will not send a Secure cookie back: pass it by hand)
        token = next(iter(c.cookies.jar)).value
        auth = {**VIA, "Cookie": f"quickz_session={token}"}
        qid = c.post("/api/quizzes/import", headers=auth, json={"text": "# T\nQ: One\n- A\n- B*\n"}).json()["id"]
        code = c.post("/api/games", headers=auth, json={"quiz_id": qid}).json()["code"]
        info = c.get(f"/api/game/{code}", headers=VIA).json()
        assert info["join_url"] == "https://d111abcdef.cloudfront.net"     # the address players scan, not the server's own IP
        assert c.get(f"/qr/{code}.svg", headers=VIA).status_code == 200


def test_nothing_changes_without_cloudfront_settings(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, public_url="http://10.0.0.5", setup_token=SETUP_TOKEN), cloud=False)
    with TestClient(app, headers=CSRF) as c:
        assert c.get("/api/auth/state").status_code == 200
        assert c.get("/api/auth/state", headers={"CloudFront-Forwarded-Proto": "https"}).status_code == 200


def test_the_host_console_websocket_works_even_though_cloudfront_rewrites_the_host_header(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app, headers=CSRF) as c:
        c.post("/api/auth/setup", headers=VIA, json={"token": SETUP_TOKEN, "username": "boss", "display_name": "B", "password": "correct horse battery"})
        token = next(iter(c.cookies.jar)).value
        auth = {**VIA, "Cookie": f"quickz_session={token}"}
        qid = c.post("/api/quizzes/import", headers=auth, json={"text": "# T\nQ: One\n- A\n- B*\n"}).json()["id"]
        code = c.post("/api/games", headers=auth, json={"quiz_id": qid}).json()["code"]
        # what the server really receives: Host = its own AWS name, Origin = the CloudFront site the browser is on
        hdrs = {"X-Origin-Verify": SECRET, "Cookie": f"quickz_session={token}", "Origin": "https://d111abcdef.cloudfront.net"}
        with c.websocket_connect(f"/ws/host/{code}", headers=hdrs) as ws:
            assert ws.receive_json()["v"]["code"] == code
        # a page on some OTHER site may not drive the host console
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect(f"/ws/host/{code}", headers={**hdrs, "Origin": "https://evil.example"}):
                pass
        assert e.value.code == 4401

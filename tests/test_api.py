import json

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import setup_admin

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

QUIZ_TEXT = """# API quiz
Q: One plus one?
- 1
- 2*
- 3
time: 20

Q: Buzz!
type: buzzer
"""


def host_login(client):
    """Make sure this client is signed in as the Super Admin."""
    setup_admin(client)


def make_game(client, settings=None):
    host_login(client)
    qid = client.post("/api/quizzes/import", json={"text": QUIZ_TEXT}).json()["id"]
    g = client.post("/api/games", json={"quiz_id": qid, "settings": settings or {}}).json()
    return qid, g


def drain(ws, until):
    for _ in range(50):
        m = ws.receive_json()
        if m.get("t") == "state" and until(m["v"]):
            return m["v"]
    raise AssertionError("expected state never arrived")


def test_host_endpoints_require_login(app):
    from fastapi.testclient import TestClient
    with TestClient(app, headers={"X-Quickz": "1"}) as anon:
        for method, path in [("get", "/api/quizzes"), ("get", "/api/games"), ("post", "/api/games"), ("post", "/api/uploads"), ("get", "/api/users")]:
            assert getattr(anon, method)(path).status_code == 401
        bad = anon.post("/api/auth/login", json={"username": "boss", "password": "nope"})
        assert bad.status_code == 401


def test_login_sets_http_only_cookie(app):
    from fastapi.testclient import TestClient
    with TestClient(app, headers={"X-Quickz": "1"}) as c:
        r = c.post("/api/auth/setup", json={"token": "setup-token-123", "username": "boss", "display_name": "Boss", "password": "correct horse battery"})
        cookie = r.headers["set-cookie"].lower()
        assert "quickz_session" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        assert c.get("/api/quizzes").status_code == 200


def test_quiz_crud_and_export(client):
    host_login(client)
    bad = client.post("/api/quizzes", json={"title": "", "questions": []})
    assert bad.status_code == 400 and "title" in bad.json()["error"].lower()
    qid = client.post("/api/quizzes/import", json={"text": QUIZ_TEXT}).json()["id"]
    quiz = client.get(f"/api/quizzes/{qid}").json()
    assert quiz["title"] == "API quiz" and len(quiz["questions"]) == 2
    quiz["title"] = "Renamed"
    assert client.put(f"/api/quizzes/{qid}", json=quiz).status_code == 200
    assert client.get("/api/quizzes").json()[0]["title"] == "Renamed"
    exported = client.get(f"/api/quizzes/{qid}/export")
    assert exported.status_code == 200 and json.loads(exported.text)["title"] == "Renamed"
    again = client.post("/api/quizzes/import", json={"quiz": json.loads(exported.text)})
    assert again.status_code == 200
    assert client.delete(f"/api/quizzes/{qid}").status_code == 200
    assert client.get(f"/api/quizzes/{qid}").status_code == 404


def test_upload_validates_magic_bytes(client):
    host_login(client)
    assert client.post("/api/uploads", content=b"<script>alert(1)</script>", headers={"content-type": "image/png"}).status_code == 400
    r = client.post("/api/uploads", content=PNG, headers={"content-type": "image/png"})
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("/uploads/") and url.endswith(".png")
    assert client.get(url).content == PNG


def test_join_validation_and_public_info(client):
    qid, g = make_game(client)
    code = g["code"]
    info = client.get(f"/api/game/{code}").json()
    assert info["title"] == "API quiz"
    assert info["join_url"] == "http://testserver"  # the address the browser actually used
    local = client.get(f"/api/game/{code}", headers={"host": "localhost:8000"}).json()
    assert local["join_url"] == "http://192.168.1.50:8000"  # localhost is useless to phones -> LAN address
    assert client.get("/api/game/00000").status_code == 404
    assert client.post("/api/join", json={"code": "00000", "nickname": "x"}).status_code == 404
    assert client.post("/api/join", json={"code": code, "nickname": ""}).status_code == 400
    ok = client.post("/api/join", json={"code": code, "nickname": "Zed"})
    assert ok.status_code == 200 and ok.json()["token"]
    assert client.post("/api/join", json={"code": code, "nickname": "zed"}).status_code == 400
    # token re-join returns the same player
    again = client.post("/api/join", json={"code": code, "nickname": "other", "token": ok.json()["token"]})
    assert again.json()["nickname"] == "Zed"


def test_qr_is_svg_for_join_url(client):
    _, g = make_game(client)
    r = client.get(f"/qr/{g['code']}.svg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and b"<svg" in r.content
    assert client.get("/qr/99999.svg").status_code == 404


def test_websocket_auth(client):
    _, g = make_game(client)
    code = g["code"]
    client.cookies.clear()
    for path in (f"/ws/host/{code}", f"/ws/player/{code}?token=bogus", "/ws/host/00000", "/ws/nobody/" + code):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path) as ws:
                ws.receive_json()
    # cross-site origin is refused even with a valid cookie
    host_login(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/host/{code}", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_json()


def test_full_game_over_websockets(client):
    qid, g = make_game(client, {"speed_bonus": False})
    code, gid = g["code"], g["id"]
    tok = client.post("/api/join", json={"code": code, "nickname": "Ann"}).json()["token"]

    with client.websocket_connect(f"/ws/host/{code}") as host, \
         client.websocket_connect(f"/ws/screen/{code}") as screen, \
         client.websocket_connect(f"/ws/player/{code}?token={tok}") as player:
        hv = host.receive_json()["v"]
        assert hv["phase"] == "lobby" and hv["players"] == 1 and len(hv["outline"]) == 2
        assert screen.receive_json()["v"]["phase"] == "lobby"
        assert player.receive_json()["v"]["me"]["nickname"] == "Ann"

        host.send_json({"t": "next"})
        pv = drain(player, lambda v: v["phase"] == "question")
        assert pv["q"]["type"] == "mcq" and "correct" not in json.dumps(pv)
        sv = drain(screen, lambda v: v["phase"] == "question")
        assert "q_correct" not in sv and "correct" not in sv["q"]
        hv = drain(host, lambda v: v["phase"] == "question")
        right = hv["q_correct"][0]
        assert hv["q"]["options"][1]["id"] == right

        player.send_json({"t": "answer", "choice": [right]})
        pv = drain(player, lambda v: v["phase"] == "reveal")
        assert pv["result"]["correct"] and pv["result"]["points"] == 1000 and pv["me"]["score"] == 1000

        host.send_json({"t": "next"})  # buzzer question
        drain(player, lambda v: v["phase"] == "question")
        player.send_json({"t": "buzz"})
        hv = drain(host, lambda v: v.get("buzz") and v["buzz"]["holder"])
        assert hv["buzz"]["holder_name"] == "Ann"
        host.send_json({"t": "resolve_buzz", "correct": True})
        drain(host, lambda v: v["phase"] == "reveal")
        host.send_json({"t": "next"})  # past last question -> finished
        final = drain(player, lambda v: v["phase"] == "finished")
        assert final["leaderboard"][0]["nickname"] == "Ann" and final["leaderboard"][0]["score"] == 2000

        player.send_json({"t": "ping", "c": 5})
        pong = player.receive_json()
        assert pong["t"] == "pong" and pong["c"] == 5

    results = client.get(f"/api/games/{gid}/results").json()
    assert results["leaderboard"][0]["score"] == 2000
    assert results["analytics"][0]["pct_correct"] == 100
    csv_text = client.get(f"/api/games/{gid}/results.csv").text
    assert "Ann" in csv_text and csv_text.splitlines()[0].startswith("Rank,Nickname")
    games = client.get("/api/games").json()
    assert games[0]["status"] == "finished" and games[0]["players"] == 1


def test_csv_neutralises_formula_nicknames(client):
    _, g = make_game(client)
    client.post("/api/join", json={"code": g["code"], "nickname": "=HYPERLINK(1)"})
    csv_text = client.get(f"/api/games/{g['id']}/results.csv").text
    assert "'=HYPERLINK(1)" in csv_text


def test_kicked_player_socket_closed_and_cannot_return(client):
    _, g = make_game(client)
    code = g["code"]
    tok = client.post("/api/join", json={"code": code, "nickname": "Ann"}).json()["token"]
    with client.websocket_connect(f"/ws/host/{code}") as host, client.websocket_connect(f"/ws/player/{code}?token={tok}") as player:
        host.receive_json()
        player.receive_json()
        hv = drain(host, lambda v: v["players_list"])
        host.send_json({"t": "kick", "player_id": hv["players_list"][0]["id"]})
        with pytest.raises(WebSocketDisconnect) as exc:
            for _ in range(5):
                player.receive_json()
        assert exc.value.code == 4403
    assert client.post("/api/join", json={"code": code, "nickname": "x", "token": tok}).status_code == 403


def test_static_pages_served(client):
    for path in ("/", "/host", "/j/12345", "/play", "/screen/12345", "/static/js/common.js", "/static/css/app.css", "/healthz"):
        assert client.get(path).status_code == 200, path

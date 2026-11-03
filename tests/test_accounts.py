"""Accounts, roles, host isolation and the security guards around them."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import ADMIN, CSRF, SETUP_TOKEN, create_host, host_session, make_app, setup_admin, sign_in
from quickz import auth
from quickz.db import Database

QUIZ = """# Mine
Q: One
- A
- B*
"""


def anon(app):
    return TestClient(app, headers=CSRF)


def add_quiz(c, text=QUIZ):
    r = c.post("/api/quizzes/import", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ primitives
def test_password_hashing_roundtrip_and_salting():
    h1, h2 = auth.hash_password("correct horse battery"), auth.hash_password("correct horse battery")
    assert h1 != h2 and h1.startswith("scrypt$")                      # unique salt per hash
    assert auth.verify_password("correct horse battery", h1)
    assert not auth.verify_password("wrong", h1) and not auth.verify_password("x", "garbage")
    assert "correct horse" not in h1


def test_password_and_username_policy():
    for bad in ("short", "a" * 200, "password123", "aaaaaaaaaaaa"):
        with pytest.raises(auth.AuthError):
            auth.validate_password(bad)
    with pytest.raises(auth.AuthError):
        auth.validate_password("deepak2026", "deepak")                 # contains the username
    assert auth.validate_password("correct horse battery")
    assert auth.normalize_username("  Deepak.I  ") == "deepak.i"
    for bad in ("ab", "has space", "x" * 40, "-lead", "emoji😀"):
        with pytest.raises(auth.AuthError):
            auth.normalize_username(bad)
    tp = auth.temp_password()
    assert len(tp) == 14 and tp.count("-") == 2 and not set(tp) & set("0O1lI")


def test_login_guard_locks_per_username_and_per_ip_then_recovers():
    t = [0.0]
    g = auth.LoginGuard(user_limit=3, ip_limit=100, window=60, lock=30, clock=lambda: t[0])
    for _ in range(3):
        assert g.retry_after("amy", "1.1.1.1") == 0
        g.failure("amy", "1.1.1.1")
    assert g.retry_after("amy", "9.9.9.9") > 0                         # username locked from any IP
    assert g.retry_after("bob", "9.9.9.9") == 0                        # others unaffected
    t[0] = 31
    assert g.retry_after("amy", "1.1.1.1") == 0                        # lock expires
    g.success("amy")
    g2 = auth.LoginGuard(user_limit=99, ip_limit=2, window=60, lock=30, clock=lambda: t[0])
    g2.failure("a", "7.7.7.7"); g2.failure("b", "7.7.7.7")
    assert g2.retry_after("c", "7.7.7.7") > 0                          # IP locked across usernames


# ------------------------------------------------------------------ first-run setup
def test_state_reports_setup_needed_until_first_admin(tmp_path):
    app = make_app(tmp_path)
    with anon(app) as c:
        s = c.get("/api/auth/state").json()
        assert s["setup_needed"] and s["user"] is None
        assert c.get("/api/quizzes").status_code == 401                # nothing is open before setup
        setup_admin(c)
        s = c.get("/api/auth/state").json()
        assert not s["setup_needed"] and s["user"]["role"] == "admin"


def test_setup_requires_the_terminal_token_and_works_once(tmp_path):
    app = make_app(tmp_path)
    with anon(app) as c:
        body = {"username": "boss", "display_name": "Boss", "password": "correct horse battery"}
        r = c.post("/api/auth/setup", json={**body, "token": "guess"})
        assert r.status_code == 403 and r.json()["code"] == "bad_token"
        weak = c.post("/api/auth/setup", json={**body, "token": SETUP_TOKEN, "password": "short"})
        assert weak.status_code == 400
        assert c.post("/api/auth/setup", json={**body, "token": SETUP_TOKEN}).status_code == 200
        again = c.post("/api/auth/setup", json={**body, "token": SETUP_TOKEN, "username": "second"})
        assert again.status_code == 409                                 # can't mint a second admin via setup


def test_setup_token_attempts_are_rate_limited(tmp_path):
    guard = auth.LoginGuard(user_limit=3, ip_limit=3)
    app = make_app(tmp_path, guard)
    with anon(app) as c:
        codes = [c.post("/api/auth/setup", json={"token": f"bad{i}", "username": "boss", "password": "correct horse battery"}).status_code for i in range(5)]
        assert codes[:3] == [403, 403, 403] and 429 in codes[3:]


def test_pre_account_quizzes_are_claimed_by_the_first_admin(tmp_path):
    db = Database(tmp_path / "quickz.db")
    from quickz.questions import parse_text_quiz
    qid = db.create_quiz(parse_text_quiz(QUIZ))                         # legacy row: no owner
    del db
    app = make_app(tmp_path)
    with anon(app) as c:
        setup_admin(c)
        assert [q["id"] for q in c.get("/api/quizzes").json()] == [qid]


# ------------------------------------------------------------------ login / sessions
def test_login_errors_are_generic_and_throttled(client, app):
    with anon(app) as c:
        wrong_pw = c.post("/api/auth/login", json={"username": "boss", "password": "nope-nope-nope"})
        no_user = c.post("/api/auth/login", json={"username": "ghost", "password": "nope-nope-nope"})
        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json()["error"] == no_user.json()["error"]       # doesn't reveal which usernames exist
        for _ in range(6):
            r = c.post("/api/auth/login", json={"username": "boss", "password": "nope-nope-nope"})
        assert r.status_code == 429 and "Retry-After" in r.headers
        # even the right password is refused while locked
        assert c.post("/api/auth/login", json=ADMIN | {"password": ADMIN["password"]}).status_code == 429


def test_usernames_are_case_insensitive(client, app):
    with anon(app) as c:
        assert c.post("/api/auth/login", json={"username": "BOSS", "password": ADMIN["password"]}).status_code == 200


def test_csrf_header_is_required_for_state_changes(client, app):
    with TestClient(app) as no_header:                                   # no X-Quickz header
        no_header.cookies.update(client.cookies)
        assert no_header.post("/api/quizzes", json={"title": "x", "questions": []}).status_code == 403
        assert no_header.get("/api/quizzes").status_code == 200          # reads don't need it
        assert no_header.post("/api/auth/login", json=ADMIN).status_code == 403


def test_sessions_survive_a_server_restart_and_logout_ends_them(tmp_path):
    app = make_app(tmp_path)
    with anon(app) as c:
        setup_admin(c)
        cookies = dict(c.cookies)
    app2 = make_app(tmp_path)                                            # "restart": same database
    with TestClient(app2, headers=CSRF, cookies=cookies) as c2:
        assert c2.get("/api/auth/state").json()["user"]["username"] == "boss"
        assert c2.post("/api/auth/logout").status_code == 200
        assert c2.get("/api/quizzes").status_code == 401


def test_only_a_hash_of_the_session_token_is_stored(client, app):
    token = client.cookies.get("quickz_session")
    rows = app.state.db._all("SELECT token_hash FROM sessions")
    assert rows and all(token != r["token_hash"] for r in rows)


def test_session_expiry_is_enforced(client, app):
    app.state.db._run("UPDATE sessions SET expires_at=1")
    assert client.get("/api/quizzes").status_code == 401


def test_logout_all_ends_every_device(client, app):
    other = TestClient(app, headers=CSRF)
    sign_in(other, "boss", ADMIN["password"])
    assert other.get("/api/quizzes").status_code == 200
    assert client.post("/api/auth/logout-all").status_code == 200
    assert other.get("/api/quizzes").status_code == 401 and client.get("/api/quizzes").status_code == 401


def test_password_change_needs_current_password_and_signs_out_other_devices(client, app):
    other = TestClient(app, headers=CSRF)
    sign_in(other, "boss", ADMIN["password"])
    assert client.post("/api/auth/password", json={"current": "wrong-password-1", "new": "another long passphrase"}).status_code == 403
    assert client.post("/api/auth/password", json={"current": ADMIN["password"], "new": ADMIN["password"]}).status_code == 400
    assert client.post("/api/auth/password", json={"current": ADMIN["password"], "new": "weak"}).status_code == 400
    assert client.post("/api/auth/password", json={"current": ADMIN["password"], "new": "another long passphrase"}).status_code == 200
    assert client.get("/api/quizzes").status_code == 200                 # this device stays signed in
    assert other.get("/api/quizzes").status_code == 401                  # the other one is out
    fresh = anon(app)
    assert fresh.post("/api/auth/login", json={"username": "boss", "password": ADMIN["password"]}).status_code == 401
    sign_in(fresh, "boss", "another long passphrase")


# ------------------------------------------------------------------ user management
def test_only_super_admin_can_manage_users(client, app):
    uid, h = host_session(app, client, "hana")
    assert h.get("/api/users").status_code == 403
    assert h.post("/api/users", json={"username": "evil"}).status_code == 403
    assert h.patch(f"/api/users/{uid}", json={"role": "admin"}).status_code == 403          # no self-promotion
    assert h.post(f"/api/users/{uid}/reset-password").status_code == 403
    users = client.get("/api/users").json()
    assert {u["username"] for u in users} == {"boss", "hana"}
    assert all("password_hash" not in u for u in users)


def test_new_host_must_change_the_temporary_password_first(client, app):
    uid, temp = create_host(client, "hana", "Hana")
    c = anon(app)
    user = sign_in(c, "hana", temp)
    assert user["must_change"] is True and user["role"] == "host"
    blocked = c.get("/api/quizzes")
    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
    assert c.post("/api/quizzes", json={"title": "x", "questions": []}).status_code == 403
    assert c.get("/api/auth/state").json()["user"]["must_change"] is True      # the UI can still ask
    assert c.post("/api/auth/password", json={"current": temp, "new": "my own long passphrase"}).status_code == 200
    assert c.get("/api/quizzes").status_code == 200


def test_create_user_validation(client):
    assert client.post("/api/users", json={"username": "x"}).status_code == 400
    assert client.post("/api/users", json={"username": "hana"}).status_code == 200
    dup = client.post("/api/users", json={"username": "HANA"})
    assert dup.status_code == 409 and dup.json()["code"] == "username_taken"


def test_reset_password_gives_one_time_temp_and_ends_sessions(client, app):
    uid, h = host_session(app, client, "hana")
    r = client.post(f"/api/users/{uid}/reset-password")
    temp = r.json()["temp_password"]
    assert h.get("/api/quizzes").status_code == 401                              # signed out immediately
    c = anon(app)
    assert sign_in(c, "hana", temp)["must_change"] is True
    assert anon(app).post("/api/auth/login", json={"username": "hana", "password": "a-long-host-password"}).status_code == 401


def test_disabling_a_host_ends_sessions_blocks_login_and_keeps_their_data(client, app):
    uid, h = host_session(app, client, "hana")
    qid = add_quiz(h)
    assert client.patch(f"/api/users/{uid}", json={"disabled": True}).status_code == 200
    assert h.get("/api/quizzes").status_code == 401
    r = anon(app).post("/api/auth/login", json={"username": "hana", "password": "a-long-host-password"})
    assert r.status_code == 403 and r.json()["code"] == "disabled"
    assert any(q["id"] == qid for q in client.get("/api/quizzes").json())        # data kept, admin can still see it
    assert client.patch(f"/api/users/{uid}", json={"disabled": False}).status_code == 200
    sign_in(anon(app), "hana", "a-long-host-password")


def test_admin_cannot_lock_themselves_out_or_remove_the_last_admin(client, app):
    me = client.get("/api/auth/state").json()["user"]["id"]
    for change in ({"disabled": True}, {"role": "host"}):
        r = client.patch(f"/api/users/{me}", json=change)
        assert r.status_code == 400 and r.json()["code"] == "self_lockout"

    # promote a second admin who has completed first sign-in
    uid, temp = create_host(client, "deputy")
    assert client.patch(f"/api/users/{uid}", json={"role": "admin"}).status_code == 200
    deputy = anon(app)
    sign_in(deputy, "deputy", temp)
    assert deputy.post("/api/auth/password", json={"current": temp, "new": "deputy long passphrase"}).status_code == 200

    # the deputy may demote/disable the original admin (there are two active admins)...
    assert deputy.patch(f"/api/users/{me}", json={"disabled": True}).status_code == 200
    # ...but can never demote themselves, which would leave zero active admins
    r = deputy.patch(f"/api/users/{uid}", json={"role": "host"})
    assert r.status_code == 400 and r.json()["code"] == "self_lockout"


# ------------------------------------------------------------------ isolation between hosts
def test_hosts_only_see_and_touch_their_own_quizzes(client, app):
    a_id, a = host_session(app, client, "alice")
    b_id, b = host_session(app, client, "bob")
    qa, qb = add_quiz(a, QUIZ.replace("Mine", "Alice quiz")), add_quiz(b, QUIZ.replace("Mine", "Bob quiz"))

    assert [q["title"] for q in a.get("/api/quizzes").json()] == ["Alice quiz"]
    assert [q["title"] for q in b.get("/api/quizzes").json()] == ["Bob quiz"]
    for verb, path, body in [
        ("get", f"/api/quizzes/{qb}", None), ("put", f"/api/quizzes/{qb}", {"title": "pwned", "questions": []}),
        ("delete", f"/api/quizzes/{qb}", None), ("get", f"/api/quizzes/{qb}/export", None),
        ("post", f"/api/quizzes/{qb}/duplicate", None),
    ]:
        r = getattr(a, verb)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (verb, path)                                # 404, not 403: existence isn't leaked
    assert a.post("/api/games", json={"quiz_id": qb}).status_code == 404         # can't host someone else's quiz
    assert b.get(f"/api/quizzes/{qb}").json()["title"] == "Bob quiz"             # untouched


def test_hosts_only_see_their_own_games_results_and_csv(client, app):
    _, a = host_session(app, client, "alice")
    _, b = host_session(app, client, "bob")
    ga = a.post("/api/games", json={"quiz_id": add_quiz(a)}).json()
    assert a.get(f"/api/games/{ga['id']}/results").status_code == 200
    assert a.get(f"/api/games/{ga['id']}/results.csv").status_code == 200
    assert b.get(f"/api/games/{ga['id']}/results").status_code == 404
    assert b.get(f"/api/games/{ga['id']}/results.csv").status_code == 404
    assert [g["id"] for g in a.get("/api/games").json()] == [ga["id"]] and b.get("/api/games").json() == []


def test_super_admin_sees_everything_with_owner_names_and_can_filter_to_mine(client, app):
    _, a = host_session(app, client, "alice")
    qa = add_quiz(a)
    mine = add_quiz(client, QUIZ.replace("Mine", "Boss quiz"))
    allq = {q["id"]: q for q in client.get("/api/quizzes").json()}
    assert set(allq) == {qa, mine} and allq[qa]["owner_name"] == "Alice" and allq[qa]["mine"] is False and allq[mine]["mine"] is True
    assert [q["id"] for q in client.get("/api/quizzes?scope=mine").json()] == [mine]
    assert client.get(f"/api/quizzes/{qa}").status_code == 200                   # admin may open/host any quiz
    assert client.post(f"/api/quizzes/{qa}/duplicate").status_code == 200        # the copy becomes the admin's own
    assert len([q for q in client.get("/api/quizzes?scope=mine").json()]) == 2


def test_host_ws_rejects_other_owner_and_anonymous(client, app):
    _, a = host_session(app, client, "alice")
    _, b = host_session(app, client, "bob")
    code = a.post("/api/games", json={"quiz_id": add_quiz(a)}).json()["code"]
    with pytest.raises(WebSocketDisconnect) as exc:
        with b.websocket_connect(f"/ws/host/{code}") as ws:
            ws.receive_json()
    assert exc.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as exc:
        with anon(app).websocket_connect(f"/ws/host/{code}") as ws:
            ws.receive_json()
    assert exc.value.code == 4401
    with a.websocket_connect(f"/ws/host/{code}") as ws:                          # the owner is fine
        assert ws.receive_json()["v"]["phase"] == "lobby"
    with client.websocket_connect(f"/ws/host/{code}") as ws:                     # and so is the Super Admin
        assert ws.receive_json()["v"]["code"] == code


def test_a_host_still_pending_a_password_change_cannot_open_the_host_websocket(client, app):
    _, temp = create_host(client, "hana")
    c = anon(app)
    sign_in(c, "hana", temp)
    code = client.post("/api/games", json={"quiz_id": add_quiz(client)}).json()["code"]
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect(f"/ws/host/{code}") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


# ------------------------------------------------------------------ misc hardening
def test_player_facing_routes_stay_public(client, app):
    code = client.post("/api/games", json={"quiz_id": add_quiz(client)}).json()["code"]
    with TestClient(app) as p:                                                   # no cookie, no header
        assert p.get(f"/api/game/{code}").status_code == 200
        assert p.post("/api/join", json={"code": code, "nickname": "Zed"}).status_code == 200
        assert p.get(f"/qr/{code}.svg").status_code == 200
        with p.websocket_connect(f"/ws/screen/{code}") as ws:
            assert "q_correct" not in ws.receive_json()["v"]


def test_security_headers_and_no_store_on_api(client):
    r = client.get("/api/quizzes")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN" and r.headers["Referrer-Policy"] == "same-origin"


def test_reset_password_cli(tmp_path, capsys, monkeypatch):
    from quickz import __main__ as cli
    app = make_app(tmp_path)
    with anon(app) as c:
        setup_admin(c)
    monkeypatch.setenv("QUICKZ_DATA", str(tmp_path))
    assert cli.reset_password(["BOSS"]) == 0
    temp = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("Temporary password:")][0].split(": ")[1]
    c2 = anon(make_app(tmp_path))
    assert sign_in(c2, "boss", temp)["must_change"] is True
    assert cli.reset_password(["nobody"]) == 1

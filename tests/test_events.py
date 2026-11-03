"""Events: register ahead of time with a PIN, walk straight in when the host opens the lobby."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from conftest import CSRF, host_session
from quickz import events as ev_mod
from quickz.engine import AVATARS

QUIZ = "# T\nQ: One\n- A\n- B*\n"


@pytest.fixture
def host(client):
    qid = client.post("/api/quizzes/import", json={"text": QUIZ}).json()["id"]
    ev = client.post("/api/events", json={"title": "Spring Quiz Night", "description": "Bring your brain.",
                                          "starts_at": time.time() + 7 * 86400, "tz": "Europe/London", "quiz_id": qid}).json()
    return client, ev


@pytest.fixture
def visitor(app):
    with TestClient(app) as c:                     # an anonymous phone
        yield c


def reg(visitor, slug, name="Priya Sharma", email="priya@example.com", pin="4821", **over):
    return visitor.post(f"/api/event/{slug}/register", json={"name": name, "email": email, "pin": pin, "consent": True, **over})


def test_pin_and_helpers():
    h = ev_mod.hash_pin("4821")
    assert ev_mod.verify_pin("4821", h) and not ev_mod.verify_pin("4822", h) and not ev_mod.verify_pin("4821", "garbage")
    assert h != ev_mod.hash_pin("4821")                                    # salted
    assert [ev_mod.clean_pin(p) for p in ("0042", " 1234 ", "12345", "12a4", "", None)] == ["0042", "1234", None, None, None, None]
    assert len(ev_mod.new_pin()) == 4
    s = ev_mod.make_slug("Spring Quiz Night!!")
    assert s.startswith("spring-quiz-night-") and ev_mod.make_slug("???").startswith("quiz-")
    assert ev_mod.clean_tz("Europe/London") == "Europe/London" and ev_mod.clean_tz("<script>") == "UTC"


def test_create_event_and_public_page(host, visitor):
    c, ev = host
    assert ev["state"] == "open" and ev["registered"] == 0 and ev["url"].endswith("/e/" + ev["slug"])
    info = visitor.get(f"/api/event/{ev['slug']}?groups=1").json()
    assert info["title"] == "Spring Quiz Night" and info["registration"] == "open" and len(info["avatar_groups"]) >= 5
    assert "game_code" not in info and "quiz_id" not in info and "registrations" not in info      # nothing private is public
    assert "avatar_groups" not in visitor.get(f"/api/event/{ev['slug']}").json()                  # the polled call stays small
    assert visitor.get("/api/event/nope-1234").status_code == 404
    assert visitor.get(f"/e/{ev['slug']}").status_code == 200
    assert visitor.get(f"/qr/event/{ev['slug']}.svg").headers["content-type"].startswith("image/svg")


def test_registration_rules(host, visitor):
    _, ev = host
    s = ev["slug"]
    assert reg(visitor, s, name="Priya").status_code == 400                                  # first and last name
    assert reg(visitor, s, email="nope").status_code == 400
    assert reg(visitor, s, pin="12").status_code == 400
    assert reg(visitor, s, pin="abcd").status_code == 400
    assert visitor.post(f"/api/event/{s}/register", json={"name": "Priya Sharma", "email": "p@e.co", "pin": "1234"}).status_code == 400   # no consent
    ok = reg(visitor, s, avatar=AVATARS[50], email="Priya@Example.com")
    assert ok.status_code == 200
    body = ok.json()
    assert body["name"] == "Priya Sharma" and body["avatar"] == AVATARS[50] and body["email"] == "priya@example.com" and len(body["ticket"]) > 20
    assert reg(visitor, s, name="Someone Else", email="PRIYA@example.com").status_code == 409     # one registration per email
    assert visitor.get(f"/api/event/{s}").json()["registered"] == 1


def test_sign_in_with_pin_is_throttled(host, visitor):
    _, ev = host
    s = ev["slug"]
    first = reg(visitor, s).json()
    ok = visitor.post(f"/api/event/{s}/signin", json={"email": "PRIYA@example.com", "pin": "4821"})
    assert ok.status_code == 200 and ok.json()["ticket"] == first["ticket"]
    for _ in range(5):
        assert visitor.post(f"/api/event/{s}/signin", json={"email": "priya@example.com", "pin": "0000"}).status_code == 403
    locked = visitor.post(f"/api/event/{s}/signin", json={"email": "priya@example.com", "pin": "4821"})
    assert locked.status_code == 429 and "Retry-After" in locked.headers                      # even the right PIN waits
    assert visitor.post(f"/api/event/{s}/signin", json={"email": "ghost@example.com", "pin": "1111"}).status_code in (403, 429)


def test_walking_in_when_the_host_opens_the_lobby(host, visitor):
    c, ev = host
    s = ev["slug"]
    t = reg(visitor, s).json()["ticket"]
    early = visitor.post(f"/api/event/{s}/enter", json={"ticket": t})
    assert early.status_code == 409                                                           # nothing to enter yet
    assert visitor.post(f"/api/event/{s}/me", json={"ticket": "wrong"}).status_code == 404
    assert visitor.post(f"/api/event/{s}/me", json={"ticket": t}).json()["name"] == "Priya Sharma"

    code = c.post(f"/api/events/{ev['id']}/open-lobby").json()["code"]
    assert c.post(f"/api/events/{ev['id']}/open-lobby").json()["code"] == code                # idempotent
    assert visitor.get(f"/api/event/{s}").json()["state"] == "live"
    # no form on the day: the ticket is the way in, with the registered name and avatar
    got = visitor.post(f"/api/event/{s}/enter", json={"ticket": t}).json()
    assert got["code"] == code and got["token"]
    again = visitor.post(f"/api/event/{s}/enter", json={"ticket": t}).json()
    assert again["token"] == got["token"]                                                     # same person, same seat
    info = c.get(f"/api/game/{code}").json()
    assert info["real_names"] and info["ask_email"]
    detail = c.get(f"/api/events/{ev['id']}").json()
    assert detail["arrived"] == 1 and detail["registrations"][0]["arrived"] is True and detail["state"] == "live"

    # walk-ins are welcome while the lobby is open ...
    assert reg(visitor, s, name="Walk In", email="walk@example.com").status_code == 200
    # ... and the quiz starting closes the door to NEW people, not to registered ones
    game = c.app.state.manager.get(code)
    asyncio.run(game.next())
    assert visitor.get(f"/api/event/{s}").json()["state"] == "running"
    assert reg(visitor, s, name="Too Late", email="late@example.com").status_code == 403
    walkin = visitor.post(f"/api/event/{s}/signin", json={"email": "walk@example.com", "pin": "4821"}).json()
    assert visitor.post(f"/api/event/{s}/enter", json={"ticket": walkin["ticket"]}).status_code == 200


def test_a_removed_player_cannot_walk_back_in(host, visitor):
    c, ev = host
    s = ev["slug"]
    t = reg(visitor, s).json()["ticket"]
    code = c.post(f"/api/events/{ev['id']}/open-lobby").json()["code"]
    visitor.post(f"/api/event/{s}/enter", json={"ticket": t})
    game = c.app.state.manager.get(code)
    player = next(iter(game.players.values()))
    asyncio.run(game.kick(player.id))
    assert visitor.post(f"/api/event/{s}/enter", json={"ticket": t}).status_code == 403


def test_open_lobby_needs_a_quiz(client, visitor):
    ev = client.post("/api/events", json={"title": "No quiz yet", "starts_at": time.time() + 100}).json()
    assert client.post(f"/api/events/{ev['id']}/open-lobby").status_code == 400
    qid = client.post("/api/quizzes/import", json={"text": QUIZ}).json()["id"]
    assert client.patch(f"/api/events/{ev['id']}", json={"quiz_id": qid}).json()["quiz_title"] == "T"
    assert client.post(f"/api/events/{ev['id']}/open-lobby").status_code == 200


def test_closing_capacity_and_finished(host, visitor):
    c, ev = host
    s = ev["slug"]
    assert c.patch(f"/api/events/{ev['id']}", json={"capacity": 1}).json()["capacity"] == 1
    assert reg(visitor, s).status_code == 200
    full = reg(visitor, s, name="Second Person", email="b@example.com")
    assert full.status_code == 403 and "full" in full.json()["error"]
    assert visitor.get(f"/api/event/{s}").json()["registration"] == "full"
    c.patch(f"/api/events/{ev['id']}", json={"capacity": 100, "reg_open": False})
    assert reg(visitor, s, name="Third Person", email="c@example.com").status_code == 403
    assert visitor.get(f"/api/event/{s}").json()["registration"] == "closed"
    c.patch(f"/api/events/{ev['id']}", json={"reg_open": True})
    code = c.post(f"/api/events/{ev['id']}/open-lobby").json()["code"]
    asyncio.run(c.app.state.manager.get(code).finish())
    assert visitor.get(f"/api/event/{s}").json()["state"] == "finished"
    assert visitor.post(f"/api/event/{s}/enter", json={"ticket": reg(visitor, s, email="x@e.co").json().get("ticket", "")}).status_code in (403, 404, 410)


def test_calendar_file(host, visitor):
    c, ev = host
    r = visitor.get(f"/e/{ev['slug']}.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    text = r.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    for want in ("SUMMARY:Spring Quiz Night", f"UID:{ev['slug']}@quickz", "TRIGGER:-P1D", "TRIGGER:-PT15M", "DTSTART:", "DTEND:", f"/e/{ev['slug']}"):
        assert want in text.replace("\r\n ", ""), want
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))                       # folded as the standard requires
    start = next(l for l in text.split("\r\n") if l.startswith("DTSTART:"))[8:]
    end = next(l for l in text.split("\r\n") if l.startswith("DTEND:"))[6:]
    assert start.endswith("Z") and end > start
    tricky = ev_mod.make_ics({**c.app.state.db.get_event(ev["id"]), "title": "A, B; C\nD", "description": ""}, "http://x/e/y")
    assert "SUMMARY:A\\, B\\; C\\nD" in tricky


def test_host_manages_registrations(host, visitor):
    c, ev = host
    s = ev["slug"]
    t = reg(visitor, s, name="=cmd Evil", email="e@example.com").json()["ticket"]
    reg(visitor, s, name="Bea Lee", email="bea@example.com")
    rows = c.get(f"/api/events/{ev['id']}").json()["registrations"]
    assert [r["email"] for r in rows] == ["e@example.com", "bea@example.com"]
    csv_text = c.get(f"/api/events/{ev['id']}/registrations.csv").text
    assert "'=cmd Evil" in csv_text and "bea@example.com" in csv_text and csv_text.splitlines()[0] == "Name,Email,Registered (UTC),Arrived"
    new_pin = c.post(f"/api/events/{ev['id']}/registrations/{rows[1]['id']}/reset-pin").json()["pin"]
    assert len(new_pin) == 4
    assert visitor.post(f"/api/event/{s}/signin", json={"email": "bea@example.com", "pin": new_pin}).status_code == 200
    assert visitor.post(f"/api/event/{s}/signin", json={"email": "bea@example.com", "pin": "4821"}).status_code == 403    # the old PIN is dead
    assert c.delete(f"/api/events/{ev['id']}/registrations/{rows[0]['id']}").status_code == 200
    assert visitor.post(f"/api/event/{s}/me", json={"ticket": t}).status_code == 404
    assert c.delete(f"/api/events/{ev['id']}/registrations/9999").status_code == 404


def test_people_can_delete_their_own_registration(host, visitor):
    _, ev = host
    s = ev["slug"]
    t = reg(visitor, s).json()["ticket"]
    assert visitor.post(f"/api/event/{s}/forget", json={"ticket": t}).status_code == 200
    assert visitor.post(f"/api/event/{s}/me", json={"ticket": t}).status_code == 404
    assert reg(visitor, s).status_code == 200                                                  # and may register afresh


def test_deleting_an_event_deletes_the_personal_data(host, visitor):
    c, ev = host
    reg(visitor, ev["slug"])
    assert c.delete(f"/api/events/{ev['id']}").status_code == 200
    assert visitor.get(f"/api/event/{ev['slug']}").status_code == 404
    assert c.app.state.db._all("SELECT * FROM registrations") == []


def test_hosts_only_see_their_own_events(app, host):
    admin, ev = host
    _, other = host_session(app, admin, "carol")
    assert other.get("/api/events").json() == []
    assert other.get(f"/api/events/{ev['id']}").status_code == 404
    assert other.patch(f"/api/events/{ev['id']}", json={"title": "Hijack"}).status_code == 404
    assert other.delete(f"/api/events/{ev['id']}").status_code == 404
    assert other.post(f"/api/events/{ev['id']}/open-lobby").status_code == 404
    mine = other.post("/api/events", json={"title": "Carol's quiz", "starts_at": time.time() + 60}).json()
    assert [e["id"] for e in other.get("/api/events").json()] == [mine["id"]]
    assert {e["id"] for e in admin.get("/api/events").json()} == {ev["id"], mine["id"]}        # the Super Admin sees all
    assert [e["id"] for e in admin.get("/api/events?scope=mine").json()] == [ev["id"]]
    # a host cannot attach someone else's quiz
    other_quiz = admin.post("/api/quizzes/import", json={"text": QUIZ}).json()["id"]
    assert other.patch(f"/api/events/{mine['id']}", json={"quiz_id": other_quiz}).status_code == 404


def test_event_endpoints_for_hosts_need_a_session(app, host):
    _, ev = host
    with TestClient(app, headers=CSRF) as anon:
        for method, path in (("get", "/api/events"), ("get", f"/api/events/{ev['id']}"), ("post", "/api/events"),
                             ("get", f"/api/events/{ev['id']}/registrations.csv"), ("post", f"/api/events/{ev['id']}/open-lobby")):
            assert getattr(anon, method)(path).status_code == 401, path


def test_validation_of_event_fields(client):
    bad = client.post("/api/events", json={"title": "  ", "starts_at": 1})
    assert bad.status_code == 400
    assert client.post("/api/events", json={"title": "Ok", "starts_at": "soon"}).status_code == 400
    ev = client.post("/api/events", json={"title": "Ok", "starts_at": 1, "duration_min": 99999, "capacity": 99999, "tz": "x y"}).json()
    assert ev["duration_min"] == 720 and ev["capacity"] == 5000 and ev["tz"] == "UTC"

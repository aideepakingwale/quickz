"""The Super Admin's Settings screen: limits and security settings that change while the server runs, and the clean-up of old
quizzes and unused uploaded files."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from quickz import auth, engine
from tests.conftest import CSRF, host_session, make_app, setup_admin

QUESTION = {"id": "q1", "type": "mcq", "text": "2+2?", "options": [{"id": "a", "text": "4"}, {"id": "b", "text": "5"}], "correct": ["a"], "time_limit": 20, "points": 1000}


@pytest.fixture
def admin(app):
    c = TestClient(app, headers=CSRF)
    setup_admin(c)
    return c


def put(admin, **body):
    return admin.put("/api/admin/settings", json=body)


def value(view, key):
    return next(s for s in view["settings"] if s["key"] == key)


def test_only_the_super_admin_can_see_or_change_settings_and_clean_up(app, admin):
    _, hana = host_session(app, admin, "hana")
    for method, path, body in (("get", "/api/admin/settings", None), ("put", "/api/admin/settings", {"max_players": 100}), ("post", "/api/admin/settings/reset", {"keys": "all"}),
                               ("get", "/api/admin/storage", None), ("get", "/api/admin/quizzes", None), ("post", "/api/admin/quizzes/delete", {"ids": [1]}),
                               ("post", "/api/admin/uploads/cleanup", {"dry_run": True})):
        r = getattr(hana, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path
    assert TestClient(app, headers=CSRF).get("/api/admin/settings").status_code == 401


def test_the_screen_lists_every_setting_with_its_default_range_and_group(admin):
    view = admin.get("/api/admin/settings").json()
    keys = {s["key"] for s in view["settings"]}
    assert keys == {"max_players", "max_questions", "max_image_mb", "max_gif_mb", "max_music_mb", "max_media_mb", "session_days", "password_min", "login_attempts", "login_lock_minutes"}
    s = value(view, "max_image_mb")
    assert (s["default"], s["value"], s["min"], s["max"], s["unit"], s["changed"]) == (2, 2, 1, 5, "MB", False)
    assert {s["group"] for s in view["settings"]} <= {g["id"] for g in view["groups"]}


def test_a_change_is_validated_applied_at_once_and_recorded(app, admin):
    view = put(admin, max_players=800, max_questions=50, session_days=14).json()
    assert [value(view, k)["value"] for k in ("max_players", "max_questions", "session_days")] == [800, 50, 14]
    assert value(view, "max_players")["changed"] is True and value(view, "max_image_mb")["changed"] is False
    assert engine.MAX_PLAYERS == 800 and app.state.settings.max_players == 800 and app.state.settings.session_days == 14
    assert view["changes"][0]["username"] == "boss" and "players per game 500 to 800" in view["changes"][0]["message"]


@pytest.mark.parametrize("body", [{"max_players": 5}, {"max_players": 99999}, {"max_players": "many"}, {"password_min": 4}, {"nonsense": 1}, {"max_image_mb": 0}, {}])
def test_bad_values_are_refused_and_nothing_changes(app, admin, body):
    r = put(admin, **body)
    assert r.status_code == 400 and r.json()
    assert value(admin.get("/api/admin/settings").json(), "max_players")["value"] == 500


def test_the_upload_limits_follow_the_settings(admin):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert admin.post("/api/uploads", content=png + b"\x00" * (1500 * 1024)).status_code == 200
    put(admin, max_image_mb=1)
    r = admin.post("/api/uploads", content=png + b"\x00" * (1500 * 1024))
    assert r.status_code == 413 and "max 1 MB" in r.text
    put(admin, max_image_mb=4)
    assert admin.post("/api/uploads", content=png + b"\x00" * (3000 * 1024)).status_code == 200
    state = admin.get("/api/auth/state").json()
    assert state["max_image_mb"] == 4 and state["max_music_mb"] == 5


def test_the_question_limit_applies_to_saving_a_quiz(admin):
    put(admin, max_questions=10)
    many = [{**QUESTION, "id": f"q{i}"} for i in range(11)]
    r = admin.post("/api/quizzes", json={"title": "Long", "questions": many})
    assert r.status_code == 400 and "at most 10 questions" in r.text
    assert admin.post("/api/quizzes", json={"title": "Fine", "questions": many[:10]}).status_code == 200


def test_the_password_rule_and_sign_in_lock_follow_the_settings(app, admin):
    put(admin, password_min=14, login_attempts=3, login_lock_minutes=2)
    assert auth.MIN_PASSWORD == 14 and app.state.guard.user_limit == 3 and app.state.guard.lock == 120
    with pytest.raises(auth.AuthError, match="at least 14"):
        auth.validate_password("only-twelve!!", "someone")
    assert auth.validate_password("long enough password 99", "someone")


def test_settings_survive_a_restart_and_reset_goes_back_to_the_default(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app, headers=CSRF) as c:
        setup_admin(c)
        put(c, max_players=900, max_music_mb=8)
    again = make_app(tmp_path)                                  # the server starts again on the same data
    assert engine.MAX_PLAYERS == 900 and again.state.settings.max_music_mb == 8
    with TestClient(again, headers=CSRF) as c:
        setup_admin(c)
        view = c.post("/api/admin/settings/reset", json={"keys": ["max_players"]}).json()
        assert value(view, "max_players")["value"] == 500 and value(view, "max_music_mb")["value"] == 8
        assert engine.MAX_PLAYERS == 500 and "Reset to the default: players per game" in view["changes"][0]["message"]
        assert value(c.post("/api/admin/settings/reset", json={"keys": "all"}).json(), "max_music_mb")["value"] == 5
    assert make_app(tmp_path).state.settings.max_music_mb == 5


def test_setting_a_value_back_to_the_default_stops_storing_it(app, admin):
    put(admin, max_players=900)
    put(admin, max_players=500)
    assert "max_players" not in app.state.db.app_settings()


# ---- clean-up: quizzes ---------------------------------------------------------------------------------------------------

def make_quiz(client, title, **extra):
    return client.post("/api/quizzes", json={"title": title, "questions": [QUESTION], **extra}).json()["id"]


def test_the_admin_sees_every_hosts_quizzes_with_what_a_clean_up_needs(app, admin):
    _, hana = host_session(app, admin, "hana")
    a, b = make_quiz(admin, "Boss quiz"), make_quiz(hana, "Hana quiz")
    rows = {r["title"]: r for r in admin.get("/api/admin/quizzes").json()}
    assert set(rows) == {"Boss quiz", "Hana quiz"}
    r = rows["Hana quiz"]
    assert r["owner_name"] and r["question_count"] == 1 and r["plays"] == 0 and r["last_played"] is None and r["bytes"] > 0 and r["updated_at"] > 0


def test_bulk_delete_removes_only_the_chosen_quizzes_and_keeps_game_results(app, admin):
    from quickz.engine import GameManager
    _, hana = host_session(app, admin, "hana")
    old, other, keep = make_quiz(hana, "Old quiz"), make_quiz(hana, "Another old"), make_quiz(admin, "Keep me")
    game = GameManager(app.state.db).create(old, {})
    game_id = game.id
    app.state.db.create_event("spring", 1, "Spring", "", time.time() + 99999, 60, "Europe/London", old, 100)
    r = admin.post("/api/admin/quizzes/delete", json={"ids": [old, other, 99999]})
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert {q["title"] for q in admin.get("/api/admin/quizzes").json()} == {"Keep me"}
    assert app.state.db.get_game(game_id)["title"] == "Old quiz"                       # the game was played from its own copy and keeps its results
    assert admin.get(f"/api/games/{game_id}/results").status_code == 200
    assert app.state.db._one("SELECT quiz_id FROM events WHERE slug='spring'")["quiz_id"] is None          # the event is kept, without a quiz
    entry = admin.get("/api/admin/ai/audit", params={"action": "cleanup"}).json()["rows"][0]
    assert entry["username"] == "boss" and "Deleted 2 quizzes" in entry["message"] and "Old quiz" in entry["message"]


def test_bulk_delete_asks_for_a_sensible_list(admin):
    assert admin.post("/api/admin/quizzes/delete", json={"ids": []}).status_code == 400
    assert admin.post("/api/admin/quizzes/delete", json={"ids": ["x"]}).status_code == 400
    assert admin.post("/api/admin/quizzes/delete", json={"ids": list(range(501))}).status_code == 400
    assert admin.post("/api/admin/quizzes/delete", json={}).status_code == 400


# ---- clean-up: unused uploaded files ----------------------------------------------------------------------------------------

def stored_files(app):
    return {p.name for p in app.state.settings.uploads_dir.iterdir()}


def age(app, name, hours):
    path = app.state.settings.uploads_dir / name
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_unused_uploads_are_found_and_removed_but_used_and_recent_ones_are_kept(app, admin):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000
    up = lambda: admin.post("/api/uploads", content=png).json()["url"].rsplit("/", 1)[-1]
    cover, bg, in_question, orphan, fresh = up(), up(), up(), up(), up()
    make_quiz(admin, "Uses files", cover=f"/uploads/{cover}", screen_bg=f"/uploads/{bg}")
    admin.post("/api/quizzes", json={"title": "Picture", "questions": [{**QUESTION, "image": f"/uploads/{in_question}"}]})
    for name in (cover, bg, in_question, orphan):
        age(app, name, 48)                                                                  # fresh stays too new to touch
    storage = admin.get("/api/admin/storage").json()
    assert storage["uploads"]["files"] == 5 and storage["uploads"]["unused_files"] == 1 and storage["uploads"]["unused_bytes"] > 2000
    assert admin.post("/api/admin/uploads/cleanup", json={}).json() == {"dry_run": True, "unused_files": 1, "unused_bytes": storage["uploads"]["unused_bytes"]}
    assert orphan in stored_files(app)                                                      # a preview deletes nothing
    done = admin.post("/api/admin/uploads/cleanup", json={"dry_run": False}).json()
    assert done["deleted"] == 1 and done["freed_bytes"] > 2000 and done["storage"]["uploads"]["unused_files"] == 0
    assert stored_files(app) == {cover, bg, in_question, fresh}
    assert "Deleted 1 unused uploaded file" in admin.get("/api/admin/ai/audit", params={"action": "cleanup"}).json()["rows"][0]["message"]


def test_a_file_used_by_a_game_or_a_deleted_quizs_game_is_still_kept(app, admin):
    from quickz.engine import GameManager
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 500
    name = admin.post("/api/uploads", content=png).json()["url"].rsplit("/", 1)[-1]
    qid = admin.post("/api/quizzes", json={"title": "Pic", "questions": [{**QUESTION, "image": f"/uploads/{name}"}]}).json()["id"]
    GameManager(app.state.db).create(qid, {})
    admin.post("/api/admin/quizzes/delete", json={"ids": [qid]})
    age(app, name, 48)
    assert admin.get("/api/admin/storage").json()["uploads"]["unused_files"] == 0          # the finished game still shows that picture


def test_storage_summary_counts_the_data(app, admin):
    make_quiz(admin, "One")
    s = admin.get("/api/admin/storage").json()
    assert s["counts"]["quizzes"] == 1 and s["counts"]["users"] == 1 and s["db_bytes"] > 0


def test_the_recent_changes_list_shows_settings_and_clean_ups_together(admin):
    make_quiz(admin, "Old")
    qid = admin.get("/api/admin/quizzes").json()[0]["id"]
    put(admin, session_days=10)
    admin.post("/api/admin/quizzes/delete", json={"ids": [qid]})
    messages = [c["message"] for c in admin.get("/api/admin/settings").json()["changes"]]
    assert messages[0].startswith("Deleted 1 quiz") and messages[1].startswith("Changed stay signed in for")


def test_the_settings_page_says_whether_gif_shrinking_is_installed(admin, monkeypatch):
    from quickz import gifshrink
    assert admin.get("/api/admin/settings").json()["features"] == {"gif_shrink": True}
    monkeypatch.setattr(gifshrink, "HAVE_PILLOW", False)
    assert admin.get("/api/admin/settings").json()["features"] == {"gif_shrink": False}
    r = admin.post("/api/uploads", content=b"GIF89a" + b"\x00" * (3 * 1024 * 1024))
    assert r.status_code == 413 and "cannot shrink GIFs" in r.text                      # a clear message instead of a crash

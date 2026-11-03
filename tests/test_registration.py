"""Registration: real full names, one registration per email, and the avatar library."""
import pytest

from quickz.db import Database
from quickz.engine import AVATAR_GROUPS, AVATARS, GameError, GameManager, clean_avatar, is_full_name
from quickz.questions import parse_text_quiz

QUIZ = "# T\nQ: One\n- A\n- B*\n"


def make(settings):
    db = Database(":memory:")
    qid = db.create_quiz(parse_text_quiz(QUIZ))
    return GameManager(db).create(qid, settings)


def test_a_full_name_needs_two_words_with_letters():
    assert is_full_name("Priya Sharma") and is_full_name("Anne-Marie O'Neil") and is_full_name("Jan van der Berg")
    assert not is_full_name("Priya") and not is_full_name("Priya 123") and not is_full_name("  ")


def test_real_names_game_asks_for_first_and_last_name():
    g = make({"real_names": True, "ask_email": True})
    with pytest.raises(GameError, match="first and last"):
        g.join("Priya", "p@example.com")
    p = g.join("  Priya   Sharma ", "p@example.com")
    assert p.nickname == "Priya Sharma"
    long_name = "Maximilian Alexander Montgomery-Wellington"      # 41 chars: a real name gets 40, not 20
    assert len(g.join(long_name, "m@example.com").nickname) == 40


def test_nickname_games_are_unchanged():
    g = make({})
    assert g.join("Fox").nickname == "Fox"
    assert len(g.join("x" * 50).nickname) == 20
    with pytest.raises(GameError, match="already taken"):
        g.join("fox")


def test_one_registration_per_email():
    g = make({"real_names": True, "ask_email": True})
    ann = g.join("Ann Lee", "Ann@Example.com")
    with pytest.raises(GameError, match="another name"):
        g.join("Bea Lee", "ann@example.com")                    # same email, different person
    assert g.join("ann lee", "ANN@example.com") is ann          # same person on a new phone: picks the registration up
    assert len(g.players) == 1


def test_two_people_may_share_a_name_when_their_emails_differ():
    g = make({"real_names": True, "ask_email": True})
    a = g.join("John Smith", "j1@example.com")
    b = g.join("John Smith", "j2@example.com")
    c = g.join("John Smith", "j3@example.com")
    assert [a.nickname, b.nickname, c.nickname] == ["John Smith", "John Smith (2)", "John Smith (3)"]
    assert g.join("John Smith", "j2@example.com") is b          # the second John coming back is still the second John
    # without email there is nothing to tell them apart by
    h = make({"real_names": True})
    h.join("John Smith")
    with pytest.raises(GameError, match="already taken"):
        h.join("john smith")


def test_a_removed_player_can_register_again():
    g = make({"real_names": True, "ask_email": True})
    p = g.join("Ann Lee", "ann@example.com")
    p.kicked = True
    assert g.join("Ann Lee", "ann@example.com") is not p


def test_avatar_library_is_big_tidy_and_plain_emoji():
    assert len(AVATARS) >= 150
    assert len(AVATARS) == sum(len(items) for _, items in AVATAR_GROUPS)    # no avatar sits in two groups
    for name, items in AVATAR_GROUPS:
        assert name and len(items) <= 24                                     # one tidy grid per tab
    assert not any("️" in a or "‍" in a for a in AVATARS)         # no joined sequences or variation selectors
    for old in "🦊🐼🐯🦁🐸🐙🦄🐧🐵🐨🦉🐳🐢🦖🐝🦋🌵🍕🚀⚡🎸👾🤖🎩":
        assert old in AVATARS                                                 # players stored with the first 24 stay valid


def test_unknown_or_malformed_avatars_get_a_random_valid_one():
    assert clean_avatar(AVATARS[40]) == AVATARS[40]
    for bad in (None, "", "<b>", ["🦊"], {"a": 1}, 7):
        assert clean_avatar(bad) in AVATARS


def test_game_info_tells_the_join_screen_what_to_ask(client):
    qid = client.post("/api/quizzes/import", json={"text": QUIZ}).json()["id"]
    code = client.post("/api/games", json={"quiz_id": qid, "settings": {"real_names": True, "ask_email": True}}).json()["code"]
    info = client.get(f"/api/game/{code}").json()
    assert info["real_names"] and info["ask_email"]
    assert [g[0] for g in info["avatar_groups"]] == [n for n, _ in AVATAR_GROUPS]
    bad = client.post("/api/join", json={"code": code, "nickname": "Priya", "email": "p@example.com"})
    assert bad.status_code == 400 and "first and last" in bad.json()["error"]
    ok = client.post("/api/join", json={"code": code, "nickname": "Priya Sharma", "email": "p@example.com", "avatar": AVATARS[100]}).json()
    assert ok["nickname"] == "Priya Sharma" and ok["avatar"] == AVATARS[100]

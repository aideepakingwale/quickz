"""Backend support for the UX features: avatars, streaks, rank movement, live distribution, quiz helpers."""
import asyncio

import pytest

from quickz.db import Database
from quickz.engine import AVATARS, Game, GameManager
from quickz.questions import parse_text_quiz

QUIZ = """# T
Q: One
- A
- B*
time: 20

Q: Two
- A*
- B
time: 20

Q: Buzz
type: buzzer
"""


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self, code=1000):
        pass

    def last(self):
        return [m for m in self.sent if m["t"] == "state"][-1]["v"]


def make(settings=None):
    db = Database(":memory:")
    qid = db.create_quiz(parse_text_quiz(QUIZ))
    mgr = GameManager(db)
    return db, mgr, mgr.create(qid, settings or {"speed_bonus": False})


def wrong_id(g):
    return next(o["id"] for o in g.q["options"] if o["id"] not in g.q["correct"])


def run(coro):
    return asyncio.run(coro)


def test_avatar_is_validated_and_defaults_to_random_choice():
    _, _, g = make()
    assert g.join("Ann", avatar=AVATARS[3]).avatar == AVATARS[3]
    assert g.join("Bob", avatar="<script>").avatar in AVATARS
    assert g.join("Cy").avatar in AVATARS


def test_avatar_survives_restore():
    async def go():
        db, mgr, g = make()
        p = g.join("Ann", avatar=AVATARS[5])
        mgr2 = GameManager(db)
        mgr2.restore()
        assert mgr2.get(g.code).players[p.id].avatar == AVATARS[5]

    run(go())


def test_streaks_grow_on_correct_and_reset_on_wrong_or_missed():
    async def go():
        _, _, g = make()
        a, b = g.join("Ann"), g.join("Bob")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.submit(a, g.q["correct"])
        await g.submit(b, [wrong_id(g)])
        assert (a.streak, b.streak) == (1, 0)
        await g.next()
        await g.submit(a, g.q["correct"])       # b does not answer
        await g.end_question()
        assert (a.streak, b.streak) == (2, 0)
        v = g.view_player(a, g._ranks())
        assert v["me"]["streak"] == 2

    run(go())


def test_streak_not_broken_by_sitting_out_a_buzzer_question():
    async def go():
        _, _, g = make()
        a = g.join("Ann")
        await g.connect("player", FakeWS(), a)
        await g.next()
        await g.submit(a, g.q["correct"])
        await g.next()
        await g.submit(a, g.q["correct"])
        await g.next()                           # buzzer: Ann never buzzes
        await g.end_question()
        assert a.streak == 2

    run(go())


def test_rank_delta_reported_after_reveal():
    async def go():
        _, _, g = make()
        a, b = g.join("Ann"), g.join("Bob")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.submit(b, g.q["correct"])        # Bob overtakes Ann (joined first, so she led the 0-0 tie)
        await g.submit(a, [wrong_id(g)])
        assert g.phase == "reveal"
        ranks = g._ranks()
        board = g.view_screen(ranks)["leaderboard"]
        assert [r["nickname"] for r in board] == ["Bob", "Ann"]
        assert board[0]["delta"] == 1 and board[1]["delta"] == -1
        assert g.view_player(b, ranks)["me"]["delta"] == 1
        assert g.view_player(a, ranks)["leaderboard"][0]["delta"] == 1
        # deltas are never exposed while a question is open
        await g.next()
        assert "delta" not in g.view_screen(g._ranks())["leaderboard"][0]

    run(go())


def test_host_gets_live_distribution_but_screen_does_not():
    async def go():
        _, _, g = make()
        a, b = g.join("Ann"), g.join("Bob")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.submit(a, g.q["correct"])
        ranks = g._ranks()
        dist = g.view_host(ranks)["live_dist"]
        assert sum(dist.values()) == 1 and dist[g.q["correct"][0]] == 1
        assert "live_dist" not in g.view_screen(ranks)
        assert "live_dist" not in g.view_player(a, ranks)

    run(go())


def test_reveal_reports_fastest_and_final_stats():
    async def go():
        _, _, g = make()
        a = g.join("Ann")
        await g.connect("player", FakeWS(), a)
        await g.next()
        await g.submit(a, g.q["correct"])
        assert g._reveal()["fastest"]["nickname"] == "Ann"
        await g.finish()
        stats = g.view_player(a, g._ranks())["stats"]
        assert stats["correct"] == 1 and stats["questions"] == 1 and stats["fastest_ms"] is not None

    run(go())


def test_lobby_screen_lists_names_with_avatars_and_players_get_count_updates():
    async def go():
        _, _, g = make()
        ws = FakeWS()
        a = g.join("Ann", avatar=AVATARS[0])
        await g.connect("player", ws, a)
        g.join("Bob")                              # triggers the debounced lobby push to players
        await asyncio.sleep(1.2)
        assert ws.last()["players"] == 2
        names = g.view_screen(g._ranks())["names"]
        assert names[0] == {"n": "Ann", "a": AVATARS[0]}

    run(go())


def test_join_api_returns_avatar(client):
    qid = client.post("/api/quizzes/import", json={"text": QUIZ}).json()["id"]
    code = client.post("/api/games", json={"quiz_id": qid}).json()["code"]
    r = client.post("/api/join", json={"code": code, "nickname": "Zed", "avatar": AVATARS[7]}).json()
    assert r["avatar"] == AVATARS[7]


def test_parse_endpoint_validates_without_saving(client):
    ok = client.post("/api/quizzes/parse", json={"text": QUIZ})
    assert ok.status_code == 200 and len(ok.json()["questions"]) == 3
    assert client.get("/api/quizzes").json() == []          # nothing was stored
    assert client.post("/api/quizzes/parse", json={"text": "no questions here"}).status_code == 400


def test_duplicate_and_quiz_list_stats(client):
    qid = client.post("/api/quizzes/import", json={"text": QUIZ}).json()["id"]
    dup = client.post(f"/api/quizzes/{qid}/duplicate").json()["id"]
    titles = {q["id"]: q for q in client.get("/api/quizzes").json()}
    assert titles[dup]["title"].endswith("(copy)")
    q = titles[qid]
    assert q["question_count"] == 3 and q["duration"] == 20 + 20 + 30 and q["has_buzzer"] and q["plays"] == 0
    client.post("/api/games", json={"quiz_id": qid})
    q = {x["id"]: x for x in client.get("/api/quizzes").json()}[qid]
    assert q["plays"] == 1 and q["last_played"]


def test_pages_include_copyright_credit(client):
    js = client.get("/static/js/common.js").text
    assert "Designed and Developed by" in js and "Ai with DI" in js and "Deepak Ingwale" in js
    assert "Designed and Developed by" in client.get("/static/js/screen.js").text

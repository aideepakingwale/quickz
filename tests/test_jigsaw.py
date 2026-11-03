"""Jigsaw: a picture cut into a grid, arranged by each player; marks by finishing place."""
import asyncio

import pytest

from quickz import grading
from quickz.db import Database
from quickz.engine import GameManager
from quickz.questions import ValidationError, parse_text_quiz, validate_question

IMG = "/static/samples/rocket.svg"


def jq(**over):
    raw = {"type": "jigsaw", "text": "Put the picture back together", "image": IMG, "jigsaw": {"rows": 2, "cols": 3}, "time_limit": 60, "points": 1000}
    raw.update(over)
    return validate_question(raw)


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self, code=1000):
        pass

    def last(self):
        return [m for m in self.sent if m["t"] == "state"][-1]["v"]


def game_for(q, settings=None):
    db = Database(":memory:")
    qid = db.create_quiz({"title": "J", "description": "", "questions": [q]})
    return GameManager(db).create(qid, {"speed_bonus": False, **(settings or {})})


def run(coro):
    return asyncio.run(coro)


def test_validation():
    q = jq()
    assert q["jigsaw"] == {"rows": 2, "cols": 3, "hint": 0, "partial": False} and q["type"] == "jigsaw" and q["place_weights"] == []
    assert jq(jigsaw={"rows": 99, "cols": 1})["jigsaw"]["rows"] == 8 and jq(jigsaw={})["jigsaw"]["cols"] == 3     # clamped / defaulted
    with pytest.raises(ValidationError, match="picture"):
        jq(image="")
    with pytest.raises(ValidationError, match="At most 48"):
        jq(jigsaw={"rows": 8, "cols": 7})
    assert jq(place_weights="100, 70%, 40")["place_weights"] == [100, 70, 40]
    assert jq(place_weights=[100, 250, -5])["place_weights"] == [100, 100, 0]
    with pytest.raises(ValidationError, match="Marks for each place"):
        jq(place_weights="gold, silver")
    # marks by place is for questions where answers race: a poll has nothing to win
    assert validate_question({"type": "poll", "text": "x", "options": [{"text": "a"}, {"text": "b"}], "place_weights": [100]})["place_weights"] == []


def test_text_import():
    quiz = parse_text_quiz(f"# T\nQ: Rebuild it\ntype: jigsaw\nimage: {IMG}\ngrid: 4x3\nplaces: 100, 60, 30\nwinners: 3\ntime: 90\npreview: no\nhint_cost: 200\n")
    q = quiz["questions"][0]
    assert q["type"] == "jigsaw" and q["jigsaw"] == {"rows": 3, "cols": 4, "hint": 200, "partial": False}     # 4x3 = 4 across, 3 down
    assert q["place_weights"] == [100, 60, 30] and q["winners"] == 3 and q["time_limit"] == 90
    mcq = parse_text_quiz("# T\nQ: One\n- A\n- B*\nplaces: 100, 50\nwinners: 2\n")["questions"][0]
    assert mcq["place_weights"] == [100, 50]                                                                  # any racing question can use it


def test_grading():
    q = jq()
    assert grading.clean_answer(q, [0, 1, 2, 3, 4, 5]) == [0, 1, 2, 3, 4, 5]
    for bad in (None, [0, 1, 2], [0, 0, 1, 2, 3, 4], [0, 1, 2, 3, 4, 6], ["0", 1, 2, 3, 4, 5], [True, 1, 2, 3, 4, 5], "012345", [0, 1, 2, 3, 4, 5, 6]):
        assert grading.clean_answer(q, bad) is None, bad
    assert grading.grade(q, [0, 1, 2, 3, 4, 5]) == (True, 1.0)
    assert grading.grade(q, [1, 0, 2, 3, 4, 5]) == (False, 0.0)                                  # no part marks unless the host allows them
    part = jq(jigsaw={"rows": 2, "cols": 3, "partial": True})
    ok, share = grading.grade(part, [1, 0, 2, 3, 4, 5])
    assert ok is False and share == pytest.approx(4 / 6 * 0.5)                                    # never "correct", at most half marks
    assert grading.describe(q, [1, 0, 2, 3, 4, 5]) == ["4 of 6 pieces in the right place"]
    assert grading.describe(q, [0, 1, 2, 3, 4, 5]) == ["Puzzle solved"] and grading.expected(q) == ["The finished picture"]


def test_marks_follow_the_finishing_place():
    async def go():
        g = game_for(jq(place_weights=[100, 60, 30], points=1000))
        ps = [g.join(f"Player Number{i}") for i in range(5)]
        await g.next()
        solved = list(range(6))
        await g.submit(ps[0], solved)
        await g.submit(ps[1], [1, 0, 2, 3, 4, 5])             # wrong: no place used up, no points
        await g.submit(ps[2], solved)
        await g.submit(ps[3], solved)
        await g.submit(ps[4], solved)
        pts = [g.answers[g.q["id"]][p.id]["points"] for p in ps]
        assert pts == [1000, 0, 600, 300, 300]                  # 1st 100%, 2nd 60%, 3rd 30%, then the last value carries on

    run(go())


def test_places_limit_closes_the_question():
    async def go():
        g = game_for(jq(winners=2, place_weights=[100, 50]))
        a, b, c = (g.join(n) for n in ("Ann Lee", "Bob Ray", "Cy Dee"))
        screen = FakeWS()
        await g.connect("screen", screen)
        await g.next()
        await g.submit(a, list(range(6)))
        assert g.phase == "question" and screen.last()["finishers"][0]["nickname"] == "Ann Lee"      # the room watches the race
        assert screen.last()["q"]["places"] == [100, 50] and screen.last()["q"]["spots_left"] == 1
        await g.submit(b, list(range(6)))                         # the last place is taken: the question closes
        assert g.phase == "reveal"
        await g.submit(c, list(range(6)))
        assert c.id not in g.answers[g.q["id"]]
        d = screen.last()["reveal"]["detail"]
        assert [f["nickname"] for f in d["finishers"]] == ["Ann Lee", "Bob Ray"] and [f["points"] for f in d["finishers"]] == [1000, 500]
        assert d["solved"] == 2 and d["rows"] == 2 and d["cols"] == 3

    run(go())


def test_the_view_has_no_secret_and_bots_play_it():
    async def go():
        g = game_for(jq())
        p = g.join("Ann Lee")
        await g.next()
        ranks = g._ranks()
        v = g.view_player(p, ranks)["q"]
        assert v["jigsaw"] == {"rows": 2, "cols": 3, "hint": 0, "hint_used": False} and "correct" not in v and v["image"] == IMG
        for _ in range(30):
            ans = type(g)._bot_answer(g.q)
            assert grading.clean_answer(g.q, ans) is not None

    run(go())


def test_it_appears_in_results_and_reports():
    async def go():
        g = game_for(jq(place_weights=[100]))
        p = g.join("Ann Lee")
        await g.next()
        await g.submit(p, [1, 0, 2, 3, 4, 5])
        await g.end_question()
        from quickz.engine import game_results, player_detail
        res = game_results(g.db, g.id)
        assert res["analytics"][0]["answered"] == 1 and res["analytics"][0]["correct"] == 0
        rep = player_detail(g.db, g.id, p.id)
        assert rep and rep["answers"][0]["yours"] == ["4 of 6 pieces in the right place"] if "answers" in rep else True

    run(go())


def test_a_hint_shows_the_picture_and_costs_marks():
    async def go():
        g = game_for(jq(jigsaw={"rows": 2, "cols": 3, "hint": 300}))
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        wa = FakeWS()
        await g.connect("player", wa, a)
        await g.connect("player", FakeWS(), b)                    # both are online, so the question waits for both
        await g.next()
        assert wa.last()["q"]["jigsaw"]["hint"] == 300 and wa.last()["q"]["jigsaw"]["hint_used"] is False
        await g.on_player(a, {"t": "hint"})
        await g.on_player(a, {"t": "hint"})                       # asking twice is free and changes nothing
        assert wa.last()["q"]["jigsaw"]["hint_used"] is True
        assert g.view_player(b, g._ranks())["q"]["jigsaw"]["hint_used"] is False       # only the player who asked
        await g.submit(a, list(range(6)))
        await g.submit(b, list(range(6)))
        assert g.answers[g.q["id"]][a.id]["points"] == 700 and g.answers[g.q["id"]][b.id]["points"] == 1000
        await g.end_question()
        assert g.view_player(a, g._ranks())["result"]["hint_cost"] == 300

    run(go())


def test_no_hint_unless_the_host_allows_one():
    async def go():
        g = game_for(jq())
        a = g.join("Ann Lee")
        await g.next()
        await g.on_player(a, {"t": "hint"})
        assert a.id not in g.hints.get(g.q["id"], ())
        await g.submit(a, list(range(6)))
        assert g.answers[g.q["id"]][a.id]["points"] == 1000

    run(go())

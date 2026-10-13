"""Questions added while a game runs: next/end, pushed into the quiz, after the game finished, and tie-breakers."""
import asyncio

import pytest

from quickz.db import Database
from quickz.engine import GameError, GameManager
from quickz.questions import validate_quiz


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)

    async def close(self, code=1000):
        pass

    def view(self):
        return [m for m in self.sent if m["t"] == "state"][-1]["v"]


def run(coro):
    return asyncio.run(coro)


def mcq(text="Q?", **kw):
    return {"type": "mcq", "text": text, "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct": ["a"],
            "time_limit": 20, "points": 1000, "speed_weight": 0, **kw}


def setup(n_questions=2, players=4, **settings):
    db = Database(":memory:")
    qid = db.create_quiz(validate_quiz({"title": "T", "questions": [mcq(f"Q{i}") for i in range(n_questions)]}))
    mgr = GameManager(db)
    g = mgr.create(qid, settings)
    ps = [g.join(f"P{i}") for i in range(players)]
    return db, qid, g, ps


async def connect(g, ps):
    socks = {}
    for p in ps:
        socks[p.id] = FakeWS()
        await g.connect("player", socks[p.id], p)
    return socks


def texts(g):
    from quickz.questions import strip_tags
    return [strip_tags(q["text"]) for q in g.questions]


# ------------------------------------------------------------------ placement
def test_added_question_goes_next_by_default_or_to_the_end_on_request():
    async def go():
        _, _, g, _ = setup(3)
        await g.next()                                              # on Q0
        await g.add_question(mcq("NEXT"))
        await g.add_question(mcq("LAST"), where="end")
        assert texts(g) == ["Q0", "NEXT", "Q1", "Q2", "LAST"]

    run(go())


def test_invalid_questions_are_rejected_without_touching_the_game():
    async def go():
        _, _, g, _ = setup(2)
        with pytest.raises(GameError):
            await g.add_question({"type": "mcq", "text": ""})
        assert texts(g) == ["Q0", "Q1"]

    run(go())


# ------------------------------------------------------------------ push into the quiz
def test_save_to_quiz_inserts_it_where_it_was_added_and_otherwise_stays_game_only():
    async def go():
        db, qid, g, _ = setup(3)
        await g.next()
        await g.add_question(mcq("SAVED"), save=True)               # after Q0 in the game and in the quiz
        await g.add_question(mcq("GAME ONLY"))
        saved = [q["text"] for q in db.get_quiz(qid)["questions"]]
        assert saved == ["Q0", "SAVED", "Q1", "Q2"]
        await g.add_question(mcq("ENDING"), where="end", save=True)
        assert [q["text"] for q in db.get_quiz(qid)["questions"]][-1] == "ENDING"
        assert db.get_game(g.id)["questions"][1]["text"] == "GAME ONLY"   # game copy persisted too

    run(go())


def test_saving_to_a_deleted_quiz_still_adds_to_the_game_and_says_so():
    async def go():
        db, qid, g, _ = setup(2)
        db.delete_quiz(qid)
        with pytest.raises(GameError, match="could not be saved"):
            await g.add_question(mcq("X"), save=True)
        assert "X" in texts(g)

    run(go())


# ------------------------------------------------------------------ after the game finished
def test_a_finished_game_can_be_reopened_for_one_more_question():
    async def go():
        db, _, g, ps = setup(1)
        socks = await connect(g, ps)
        await g.next()
        await g.submit(ps[0], ["a"])
        await g.end_question()
        await g.next()
        assert g.phase == "finished" and db.get_game(g.id)["status"] == "finished"
        await g.add_question(mcq("TIE-BREAK"))
        assert g.phase == "reveal" and db.get_game(g.id)["status"] == "open"      # live again, on the last reveal
        await g.next()
        assert g.phase == "question" and texts(g)[g.index] == "TIE-BREAK"
        assert socks[ps[0].id].view()["phase"] == "question"
        await g.end_question()
        await g.next()
        assert g.phase == "finished"                                               # and finishes again

    run(go())


def test_a_reopened_game_survives_a_restart():
    async def go():
        db, _, g, ps = setup(1)
        await connect(g, ps)
        await g.next()
        await g.end_question()
        await g.next()
        await g.add_question(mcq("EXTRA"))
        m2 = GameManager(db)
        m2.restore()
        assert m2.get(g.code) is not None and m2.get(g.code).phase == "reveal" and "EXTRA" in texts(m2.get(g.code))

    run(go())


# ------------------------------------------------------------------ tie detection
def test_ties_at_the_podium_are_reported_to_the_host_only():
    async def go():
        _, _, g, ps = setup(1, players=5)
        await connect(g, ps)
        await g.next()
        for p, pts in zip(ps, (900, 900, 500, 500, 100)):
            p.score = pts
        await g.end_question()
        groups = g.view_host(g._ranks())["tie_groups"]
        assert [(x["rank"], x["score"], sorted(pl["nickname"] for pl in x["players"])) for x in groups] == [(1, 900, ["P0", "P1"]), (3, 500, ["P2", "P3"])]
        assert "tie_groups" not in g.view_screen(g._ranks()) and "tie_groups" not in g.view_player(ps[0], g._ranks())

    run(go())


def test_ties_outside_the_podium_and_zero_score_ties_are_not_flagged():
    async def go():
        _, _, g, ps = setup(1, players=6)
        await connect(g, ps)
        await g.next()
        for p, pts in zip(ps, (900, 800, 700, 600, 100, 100)):
            p.score = pts
        assert g.view_host(g._ranks())["tie_groups"] == []                 # the tie is for 5th: nobody cares
        for p in ps:
            p.score = 0
        assert g.view_host(g._ranks())["tie_groups"] == []                 # nobody has scored yet

    run(go())


# ------------------------------------------------------------------ tie-breaker: only the tied players answer
def test_only_the_chosen_players_can_answer_and_the_question_ends_when_they_have():
    async def go():
        _, _, g, ps = setup(1, players=4)
        socks = await connect(g, ps)
        await g.next()
        await g.end_question()
        await g.add_question(mcq("TIE", winners=0), only=[ps[0].id, ps[1].id])
        await g.next()
        assert texts(g)[g.index] == "TIE"
        assert socks[ps[0].id].view()["q"]["can_play"] is True and socks[ps[2].id].view()["q"]["can_play"] is False
        assert [x["nickname"] for x in socks[ps[2].id].view()["q"]["only_players"]] == ["P0", "P1"]
        assert g.view_screen(g._ranks())["eligible"] == 2                  # the projector counts only those two
        await g.submit(ps[2], ["a"])                                       # a spectator tries: ignored
        assert not g.answers.get(g.q["id"], {}).get(ps[2].id) and ps[2].score == 0
        await g.submit(ps[0], ["a"])
        assert g.phase == "question"
        await g.submit(ps[1], ["b"])
        assert g.phase == "reveal"                                         # both tied players answered: no need to wait for the rest

    run(go())


def test_a_tie_breaker_can_be_a_single_winner_race():
    async def go():
        _, _, g, ps = setup(1, players=4)
        socks = await connect(g, ps)
        await g.next()
        await g.end_question()
        ps[0].score = ps[1].score = 1500
        await g.add_question(mcq("TIE", winners=1, points=1000), only=[ps[0].id, ps[1].id])
        await g.next()
        await g.submit(ps[1], ["a"])
        assert g.phase == "reveal" and ps[1].score == 2500 and ps[0].score == 1500     # the tie is broken
        assert socks[ps[3].id].view()["reveal"]["claimed"][0]["nickname"] == "P1"
        ranks = g._ranks()
        assert ranks["order"][0].nickname == "P1"

    run(go())


def test_tie_breaker_restrictions_apply_to_buzzers_and_bots_and_validate_the_player_list():
    async def go():
        _, _, g, ps = setup(1, players=3)
        await connect(g, ps)
        await g.add_bots(4)
        await g.next()
        await g.end_question()
        await g.add_question({"type": "buzzer", "text": "BUZZ"}, only=[ps[0].id])
        await g.next()
        assert g._bot_tasks == []                                          # no bot is tied, so none of them play
        await g.buzz(ps[1])                                                # not tied: ignored
        await asyncio.sleep(0.2)
        assert g.holder is None
        await g.buzz(ps[0])
        await asyncio.sleep(0.2)
        assert g.holder == ps[0].id
        with pytest.raises(GameError):
            await g.add_question(mcq("X"), only=[9999])                    # unknown player ids
        with pytest.raises(GameError):
            await g.add_question(mcq("X"), only="everyone")

    run(go())


def test_the_restriction_is_not_copied_into_the_saved_quiz():
    async def go():
        db, qid, g, ps = setup(1)
        await g.next()
        await g.end_question()
        await g.add_question(mcq("TIE"), only=[ps[0].id], save=True)
        saved = db.get_quiz(qid)["questions"][-1]
        assert saved["text"] == "TIE" and "only" not in saved             # player ids mean nothing in the library

    run(go())


def test_outline_marks_tie_breakers_for_the_host():
    async def go():
        _, _, g, ps = setup(1)
        await g.next()
        await g.end_question()
        await g.add_question(mcq("TIE"), only=[ps[0].id])
        outline = g.view_host(g._ranks())["outline"]
        assert [o["tie"] for o in outline] == [False, True]

    run(go())

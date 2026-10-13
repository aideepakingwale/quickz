"""'How many players can score on this question': everyone / only the first correct answer / the first N."""
import asyncio
import time

from quickz.db import Database
from quickz.engine import GameManager
from quickz.questions import parse_text_quiz, validate_question, validate_quiz


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


def mcq(**kw):
    return {"type": "mcq", "text": "Q?", "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct": ["a"],
            "time_limit": 20, "points": 1000, **kw}


def setup(questions, players=4, **settings):
    db = Database(":memory:")
    qid = db.create_quiz(validate_quiz({"title": "T", "questions": questions}))
    mgr = GameManager(db)
    g = mgr.create(qid, settings)
    ps = [g.join(f"P{i}") for i in range(players)]
    return db, mgr, g, ps


async def connect(g, ps):
    socks = {}
    for p in ps:
        socks[p.id] = FakeWS()
        await g.connect("player", socks[p.id], p)
    return socks


def test_flag_is_validated_and_ignored_for_buzzer_questions():
    assert validate_question(mcq())["winners"] == 0                       # default: everyone can score
    assert validate_question(mcq(winners=1))["winners"] == 1
    assert validate_question(mcq(winners=500))["winners"] == 50 and validate_question(mcq(winners=-3))["winners"] == 0
    assert validate_question(mcq(winners="x"))["winners"] == 0
    assert validate_question({"type": "buzzer", "text": "B", "winners": 3})["winners"] == 0
    assert parse_text_quiz("# T\nQ: One\n- A*\n- B\nwinners: 2\n")["questions"][0]["winners"] == 2


def test_only_the_first_correct_answer_wins_and_gets_100_percent_whatever_the_speed_weight():
    async def go():
        _, _, g, ps = setup([mcq(winners=1, speed_weight=100)], speed_bonus=True)
        socks = await connect(g, ps)
        await g.next()
        assert g._public_q("player")["winners"] == 1 and g._public_q("player")["spots_left"] == 1
        g.started_ms -= 19_000                                              # nearly out of time: normally ~5% of the points
        await g.submit(ps[2], ["a"])
        assert ps[2].score == 1000                                          # ...but the only winner earns 100%
        assert g.phase == "reveal"                                          # the question closed the moment it was won
        r = socks[ps[0].id].view()["reveal"]
        assert r["closed_by_limit"] is True and r["winners"] == 1
        c = r["claimed"][0]
        assert (c["nickname"], c["rank"], c["points"]) == ("P2", 1, 1000) and c["ms"] >= 19_000
        assert abs(c["at"] - time.time() * 1000) < 5000                     # the wall-clock time it was submitted
        await g.submit(ps[0], ["a"])                                        # too late: already given
        assert ps[0].score == 0 and not g.answers[g.q["id"]].get(ps[0].id)

    run(go())


def test_everyone_sees_who_won_including_players_who_never_answered():
    async def go():
        _, _, g, ps = setup([mcq(winners=1)])
        socks = await connect(g, ps)
        await g.next()
        await g.submit(ps[1], ["a"])
        for p in ps:
            v = socks[p.id].view()
            assert v["reveal"]["claimed"][0]["nickname"] == "P1" and v["reveal"]["claimed"][0]["avatar"]
        assert socks[ps[0].id].view()["result"]["answered"] is False
        assert socks[ps[1].id].view()["result"]["points"] == 1000

    run(go())


def test_first_n_correct_answers_score_and_wrong_answers_do_not_use_a_place():
    async def go():
        _, _, g, ps = setup([mcq(winners=3, speed_weight=0)], players=6)
        socks = await connect(g, ps)
        await g.next()
        assert g._public_q("player")["spots_left"] == 3
        await g.submit(ps[0], ["b"])                                        # wrong: takes nothing
        await g.submit(ps[1], ["a"])
        assert g.phase == "question" and g._public_q("player")["spots_left"] == 2
        assert socks[ps[5].id].view()["q"]["spots_left"] == 2               # players who haven't answered see it live
        await g.submit(ps[2], ["a"])
        assert socks[ps[5].id].view()["q"]["spots_left"] == 1
        await g.submit(ps[3], ["a"])                                        # third correct answer: closes the question
        assert g.phase == "reveal"
        claimed = socks[ps[5].id].view()["reveal"]["claimed"]
        assert [c["nickname"] for c in claimed] == ["P1", "P2", "P3"] and [c["rank"] for c in claimed] == [1, 2, 3]
        assert [p.score for p in ps[:4]] == [0, 1000, 1000, 1000]
        await g.submit(ps[4], ["a"])                                        # a fourth can't score any more
        assert ps[4].score == 0

    run(go())


def test_with_a_limit_above_one_the_normal_speed_weighting_still_applies():
    async def go():
        _, _, g, ps = setup([mcq(winners=2, speed_weight=100)], players=3)
        await connect(g, ps)
        await g.next()
        g.started_ms -= 10_000
        await g.submit(ps[0], ["a"])
        assert 480 <= ps[0].score <= 520                                    # half time, full weight: ~50%

    run(go())


def test_if_nobody_wins_before_the_timer_the_claimed_list_is_empty():
    async def go():
        _, _, g, ps = setup([mcq(winners=1)])
        socks = await connect(g, ps)
        await g.next()
        await g.submit(ps[0], ["b"])
        await g.end_question()
        r = socks[ps[1].id].view()["reveal"]
        assert r["claimed"] == [] and r["closed_by_limit"] is False and r["winners"] == 1

    run(go())


def test_unlimited_questions_are_unchanged():
    async def go():
        _, _, g, ps = setup([mcq(speed_weight=0)], players=3, auto_reveal=False)
        await connect(g, ps)
        await g.next()
        for p in ps:
            await g.submit(p, ["a"])
        assert g.phase == "question" and [p.score for p in ps] == [1000, 1000, 1000]
        q = g._public_q("player")
        assert q["winners"] == 0 and q["spots_left"] is None
        await g.end_question()
        assert g._reveal()["claimed"] == [] and g._reveal()["closed_by_limit"] is False

    run(go())


def test_answer_times_survive_a_restart():
    async def go():
        db, mgr, g, ps = setup([mcq(winners=2), mcq()], players=3)
        await connect(g, ps)
        await g.next()
        await g.submit(ps[0], ["a"])
        await g.submit(ps[1], ["a"])
        before = g._reveal()["claimed"]
        mgr2 = GameManager(db)
        mgr2.restore()
        after = mgr2.get(g.code)._reveal()["claimed"]
        assert [(c["nickname"], c["rank"], c["points"]) for c in after] == [(c["nickname"], c["rank"], c["points"]) for c in before]
        assert all(abs(a["at"] - b["at"]) < 2 for a, b in zip(after, before))      # same wall-clock times

    run(go())


def test_host_and_screen_views_carry_the_winner_details_too():
    async def go():
        _, _, g, ps = setup([mcq(winners=1)])
        await connect(g, ps)
        await g.next()
        await g.submit(ps[0], ["a"])
        ranks = g._ranks()
        for view in (g.view_screen(ranks), g.view_host(ranks)):
            assert view["reveal"]["claimed"][0]["nickname"] == "P0"

    run(go())

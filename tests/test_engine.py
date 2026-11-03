import asyncio

import pytest

from quickz.db import Database
from quickz.engine import Game, GameError, GameManager, game_results
from quickz.questions import parse_text_quiz, validate_quiz

QUIZ = """# T
Q: Pick B
- A
- B*
- C
time: 20
points: 1000

Q: Buzz it
type: buzzer
points: 500

Q: Both
- X*
- Y*
- Z
"""


class FakeWS:
    def __init__(self):
        self.sent, self.closed = [], None

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self, code=1000):
        self.closed = code

    def last_view(self):
        return [m for m in self.sent if m["t"] == "state"][-1]["v"]


def make(settings=None):
    db = Database(":memory:")
    qid = db.create_quiz(parse_text_quiz(QUIZ))
    mgr = GameManager(db)
    return db, mgr, mgr.create(qid, settings)


def correct_id(game, i=0):
    return game.questions[i]["correct"][0]


def run(coro):
    return asyncio.run(coro)


def test_scoring_speed_bonus_and_auto_reveal():
    async def go():
        db, mgr, g = make()
        a, b = g.join("Ann"), g.join("Bob")
        ws_a, ws_b = FakeWS(), FakeWS()
        await g.connect("player", ws_a, a)
        await g.connect("player", ws_b, b)
        await g.next()
        assert g.phase == "question" and ws_a.last_view()["phase"] == "question"
        # players never see the correct answer while the question is open
        assert "correct" not in ws_a.last_view()["q"] and "reveal" not in ws_a.last_view()
        await g.submit(a, [correct_id(g)])
        assert g.phase == "question" and ws_a.last_view()["answered"] is True
        wrong = next(o["id"] for o in g.questions[0]["options"] if o["id"] != correct_id(g))
        await g.submit(b, [wrong])
        assert g.phase == "reveal"  # everyone answered
        assert 500 <= a.score <= 1000 and b.score == 0
        v = ws_a.last_view()
        assert v["result"]["correct"] and v["reveal"]["correct"] == [correct_id(g)]
        assert v["me"]["rank"] == 1 and ws_b.last_view()["me"]["rank"] == 2
        # a second answer for the same question is ignored
        before = a.score
        await g.submit(a, [correct_id(g)])
        assert a.score == before

    run(go())


def test_no_speed_bonus_gives_flat_points():
    async def go():
        _, _, g = make({"speed_bonus": False})
        p = g.join("Ann")
        await g.connect("player", FakeWS(), p)
        await g.next()
        await g.submit(p, [correct_id(g)])
        assert p.score == 1000

    run(go())


def test_invalid_choice_rejected_and_multi_all_or_nothing():
    async def go():
        _, _, g = make({"speed_bonus": False})
        p = g.join("Ann")
        await g.connect("player", FakeWS(), p)
        await g.next()
        await g.submit(p, ["nope"])
        assert not g.answers.get(g.questions[0]["id"])
        await g.end_question()
        await g.next()           # buzzer
        await g.end_question()
        await g.next()           # multi
        ids = [o["id"] for o in g.questions[2]["options"]]
        await g.submit(p, ids[:1])  # only one of two correct -> 0
        assert p.score == 0

    run(go())


def test_timer_expiry_reveals():
    async def go():
        _, _, g = make()
        g.questions[0]["time_limit"] = 1
        p = g.join("Ann")
        await g.connect("player", FakeWS(), p)
        await g.next()
        await asyncio.sleep(1.3)
        assert g.phase == "reveal"
        await g.submit(p, [correct_id(g)])  # too late
        assert p.score == 0

    run(go())


def test_buzzer_fairness_lockout_and_resolution():
    async def go():
        db, mgr, g = make()
        a, b, c = g.join("Ann"), g.join("Bob"), g.join("Cy")
        for p in (a, b, c):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.end_question()
        await g.next()  # buzzer question
        assert g.phase == "question" and g.q["type"] == "buzzer"
        # Bob has a slow connection (400 ms RTT): Ann's buzz arrives ~100ms later but is stamped earlier
        await g.on_player(b, {"t": "rtt", "ms": 400})
        await g.buzz(b)
        await asyncio.sleep(0.03)
        await g.buzz(a)
        await asyncio.sleep(0.2)
        assert g.holder == b.id  # stamped 200 ms earlier despite arriving first
        first = g.holder
        assert g.buzz_order[0] == first and len(g.buzz_order) == 2
        assert g.paused_remaining is not None
        # holder wrong -> locked out, timer resumes, others can try
        await g.resolve_buzz(False)
        assert g.holder is None and first in g.lockouts and g.phase == "question"
        await g.buzz(next(p for p in (a, b, c) if p.id == first))  # locked-out player cannot buzz
        await asyncio.sleep(0.2)
        assert g.holder is None
        await g.buzz(c)
        await asyncio.sleep(0.2)
        assert g.holder == c.id
        await g.resolve_buzz(True)
        assert c.score == 500 and g.phase == "reveal"
        assert g._reveal()["buzz_winner"] == "Cy"

    run(go())


def test_buzz_window_picks_lowest_adjusted_time():
    async def go():
        _, _, g = make()
        a, b = g.join("Ann"), g.join("Bob")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.end_question()
        await g.next()
        b.rtt_ms = 400  # b's packet is stamped 200 ms earlier than it arrived
        await g.buzz(a)
        await g.buzz(b)
        await asyncio.sleep(0.25)
        assert g.holder == b.id

    run(go())


def test_everyone_locked_out_ends_question():
    async def go():
        _, _, g = make()
        a = g.join("Ann")
        await g.connect("player", FakeWS(), a)
        await g.next()
        await g.end_question()
        await g.next()
        await g.buzz(a)
        await asyncio.sleep(0.2)
        await g.resolve_buzz(False)
        assert g.phase == "reveal"

    run(go())


def test_join_rules_and_reconnect_token():
    _, _, g = make({"ask_email": True})
    with pytest.raises(GameError):
        g.join("Ann")                       # email required
    with pytest.raises(GameError):
        g.join("Ann", "not-an-email")
    p = g.join("  Ann \x00 ", "ann@example.com")
    assert p.nickname == "Ann" and p.email == "ann@example.com"
    with pytest.raises(GameError):
        g.join("ann", "x@y.co")             # case-insensitive duplicate
    assert g.join("anything", token=p.token) is p
    with pytest.raises(GameError):
        g.join("   ", "x@y.co")


def test_kick_blocks_rejoin_and_frees_nickname():
    async def go():
        _, _, g = make()
        p = g.join("Ann")
        ws = FakeWS()
        await g.connect("player", ws, p)
        await g.kick(p.id)
        assert ws.closed == 4403
        with pytest.raises(GameError):
            g.join("x", token=p.token)
        g.join("Ann")  # nickname free again

    run(go())


def test_quick_add_inserts_after_current_and_persists():
    async def go():
        db, mgr, g = make()
        await g.next()
        raw = {"type": "mcq", "text": "Quick?", "options": [{"id": "y", "text": "y"}, {"id": "n", "text": "n"}], "correct": ["y"]}
        await g.add_question(raw)
        assert g.questions[1]["text"] == "Quick?"
        assert db.get_game(g.id)["questions"][1]["text"] == "Quick?"
        with pytest.raises(GameError):
            await g.add_question({"type": "mcq", "text": ""})

    run(go())


def test_restore_after_restart_keeps_scores_and_interrupted_question():
    async def go():
        db, mgr, g = make()
        p = g.join("Ann")
        await g.connect("player", FakeWS(), p)
        await g.next()
        await g.submit(p, [correct_id(g)])  # sole player answered -> auto reveal
        score = p.score
        await g.next()                       # now mid-question (buzzer)
        assert g.phase == "question"
        mgr2 = GameManager(db)
        mgr2.restore()
        g2 = mgr2.get(g.code)
        assert g2 and g2.phase == "reveal" and g2.index == 1
        assert g2.players[p.id].score == score
        assert g2.join("x", token=p.token).id == p.id

    run(go())


def test_bots_play_and_results_analytics():
    async def go():
        db, mgr, g = make()
        await g.add_bots(5)
        assert sum(p.is_bot for p in g.players.values()) == 5
        g.questions[0]["time_limit"] = 5
        await g.next()
        for _ in range(80):
            await asyncio.sleep(0.1)
            if g.phase == "reveal":
                break
        assert g.phase == "reveal"
        await g.finish()
        res = game_results(db, g.id)
        assert res["game"]["status"] == "finished"
        assert len(res["leaderboard"]) == 5
        assert res["analytics"][0]["answered"] == 5

    run(go())


def test_host_view_has_answers_but_screen_view_does_not():
    async def go():
        _, _, g = make()
        p = g.join("Ann")
        await g.connect("player", FakeWS(), p)
        await g.next()
        ranks = g._ranks()
        assert g.view_host(ranks)["q_correct"] == [correct_id(g)]
        screen = g.view_screen(ranks)
        assert "q_correct" not in screen and "correct" not in screen["q"] and "reveal" not in screen

    run(go())

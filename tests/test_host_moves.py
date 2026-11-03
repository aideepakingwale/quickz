"""Host controls: skip to the next question, and run a question again when nobody answered."""
import asyncio

from quickz import engine
from quickz.db import Database
from quickz.engine import GameManager
from quickz.questions import parse_text_quiz


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self, code=1000):
        pass


def game(n=3):
    db = Database(":memory:")
    text = "# T\n" + "".join(f"Q: Question {i}\n- A*\n- B\ntime: 20\n\n" for i in range(n))
    qid = db.create_quiz(parse_text_quiz(text))
    return GameManager(db).create(qid, {"speed_bonus": False, "reveal_pause": 0})


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_skip_moves_on_without_a_reveal_and_keeps_marks():
    async def go():
        g = game()
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        first = g.q["id"]
        await g.submit(a, g.q["correct"])
        assert g.phase == "question"
        await g.skip()
        assert g.phase == "question" and g.index == 1 and g.q["id"] != first
        assert a.score == 1000 and first in g.answers
        await g.skip()
        assert g.index == 2
        await g.skip()                                   # the last question has nowhere to skip to
        assert g.index == 2 and g.phase == "question"

    run(go())


def test_skip_is_ignored_outside_a_question():
    async def go():
        g = game()
        await g.skip()
        assert g.phase == "lobby"

    run(go())


def test_rerun_when_nobody_answered_uses_the_new_time_limit():
    async def go():
        g = game()
        g.join("Ann Lee")
        await g.next()
        qid = g.q["id"]
        await g.end_question()
        assert g.phase == "reveal"
        await g.rerun(45)
        assert g.phase == "question" and g.index == 0 and g.q["id"] == qid and g.q["time_limit"] == 45
        assert 44_000 < g.ends_ms - g.started_ms <= 45_000
        await g.rerun(10)                                  # only from the reveal
        assert g.q["time_limit"] == 45

    run(go())


def test_rerun_is_refused_once_anyone_answered_and_the_time_is_clamped():
    async def go():
        g = game()
        a = g.join("Ann Lee")
        await g.connect("player", FakeWS(), a)
        await g.connect("player", FakeWS(), g.join("Bob Ray"))
        await g.next()
        await g.submit(a, g.q["correct"])
        await g.end_question()
        await g.rerun(30)
        assert g.phase == "reveal"                         # someone answered: no do-over

        h = game()
        h.join("Cy Dee")
        await h.next()
        await h.end_question()
        await h.rerun(9999)
        assert h.phase == "question" and h.q["time_limit"] == 300
        await h.end_question()
        await h.rerun("nonsense")
        assert h.phase == "reveal"

    run(go())


BUZZ = "# T\nQ: Buzz one\ntype: buzzer\ntime: 20\n\nQ: Buzz two\ntype: buzzer\ntime: 20\n\nQ: Last\n- A*\n- B\ntime: 20\n"


def buzz_game():
    db = Database(":memory:")
    qid = db.create_quiz(parse_text_quiz(BUZZ))
    return GameManager(db).create(qid, {"speed_bonus": False, "reveal_pause": 0})


async def settle():
    await asyncio.sleep(engine.BUZZ_WINDOW_S + 0.15)


def test_skip_a_buzzer_while_someone_is_being_judged():
    async def go():
        g = buzz_game()
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.buzz(a)
        await settle()
        assert g.holder == a.id
        await g.skip()
        assert g.index == 1 and g.phase == "question" and g.holder is None and not g.lockouts and not g.buzz_order
        assert g.answers.get(g.questions[0]["id"]) is None and a.score == 0
        await g.buzz(b)                                    # the next buzzer question works normally
        await settle()
        assert g.holder == b.id
        await g.resolve_buzz(True)
        assert b.score == 1000 and g.phase == "reveal"

    run(go())


def test_skip_before_the_buzz_window_closes_leaves_nothing_behind():
    async def go():
        g = buzz_game()
        a = g.join("Ann Lee")
        await g.connect("player", FakeWS(), a)
        await g.next()
        await g.buzz(a)
        await g.skip()                                     # the buzz was still being collected
        await settle()
        assert g.index == 1 and g.holder is None and not g._buzz_pending

    run(go())


def test_run_a_buzzer_again_when_nobody_buzzed():
    async def go():
        g = buzz_game()
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.end_question()
        assert g.phase == "reveal"
        await g.rerun(60)
        assert g.phase == "question" and g.index == 0 and g.q["time_limit"] == 60 and g.holder is None
        await g.buzz(b)
        await settle()
        assert g.holder == b.id and g.buzz_ms[b.id] >= 0
        await g.resolve_buzz(True)
        assert b.score == 1000 and g.phase == "reveal"
        await g.rerun(30)                                  # someone scored: no do-over
        assert g.phase == "reveal"

    run(go())


def test_a_buzzer_nobody_got_right_can_be_run_again():
    async def go():
        g = buzz_game()
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        await g.buzz(a)
        await settle()
        await g.resolve_buzz(False)
        await g.buzz(b)
        await settle()
        await g.resolve_buzz(False)                        # both locked out: the question closes with nobody right
        assert g.phase == "reveal" and len(g.answers[g.questions[0]["id"]]) == 2
        await g.rerun(40)
        assert g.phase == "question" and not g.lockouts and g.holder is None and not g.answers.get(g.questions[0]["id"])
        await g.buzz(a)                                    # Ann is no longer locked out
        await settle()
        assert g.holder == a.id
        await g.resolve_buzz(True)
        assert a.score == 1000

    run(go())


def test_run_again_when_everyone_was_wrong_clears_the_round_and_refunds_part_marks_and_double_ups():
    async def go():
        db = Database(":memory:")
        text = "# T\nQ: Jigsaw\ntype: jigsaw\nimage: /static/samples/rocket.svg\ngrid: 2x2\npartial: yes\ntime: 60\n\nQ: Next\n- A*\n- B\ntime: 20\n"
        g = GameManager(db).create(db.create_quiz(parse_text_quiz(text)), {"speed_bonus": False, "reveal_pause": 0, "jokers": True})
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        qid = g.q["id"]
        await g.submit(a, [0, 1, 3, 2], joker=True)        # two of four pieces right: part marks, never "correct"; double-up spent
        await g.submit(b, [1, 0, 3, 2])
        assert g.phase == "reveal" and not any(x["correct"] for x in g.answers[qid].values())
        assert a.score > 0 and a.joker_qid == qid
        assert g.view_player(a, g._ranks())["q"]["run"] == 0
        await g.rerun(90)
        assert g.phase == "question" and g.q["time_limit"] == 90 and g.view_player(a, g._ranks())["q"]["run"] == 1
        assert a.score == 0 and b.score == 0 and a.joker_qid is None and not g.answers.get(qid)
        assert not [r for r in db.game_answers(g.id) if r["qid"] == qid]
        await g.submit(a, [0, 1, 2, 3])                    # the second round counts normally and may be answered again
        await g.submit(b, [0, 1, 2, 3])
        assert g.phase == "reveal" and a.score == 1000 and g.answers[qid][b.id]["correct"]
        await g.rerun(30)                                  # somebody got it right: no do-over
        assert g.phase == "reveal"

    run(go())

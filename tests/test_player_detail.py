"""Results drill-down: one player's questions, answers, timings and points."""
import asyncio

import pytest

from conftest import host_session
from quickz.db import Database
from quickz.engine import GameManager, game_results, player_detail
from quickz.questions import validate_quiz


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)

    async def close(self, code=1000):
        pass


def run(coro):
    return asyncio.run(coro)


def q(text, **kw):
    return {"type": "mcq", "rich": True, "text": text, "options": [{"id": "a", "text": "Alpha"}, {"id": "b", "text": "Beta"}, {"id": "c", "text": "<b>Gamma</b>"}],
            "correct": ["b"], "time_limit": 20, "points": 1000, "speed_weight": 0, **kw}


async def play():
    db = Database(":memory:")
    qid = db.create_quiz(validate_quiz({"title": "Report", "questions": [
        q("One"), q("Two"), q("Three"), q("Four", winners=1),
        {"type": "buzzer", "text": "Five", "points": 700}]}))
    g = GameManager(db).create(qid, {"auto_reveal": False})
    ann, bob, cy = g.join("Ann", avatar="🦊"), g.join("Bob"), g.join("Cy")
    for p in (ann, bob, cy):
        await g.connect("player", FakeWS(), p)
    await g.next()                                      # Q1: Ann right, Bob wrong, Cy silent
    await g.submit(ann, ["b"]); await g.submit(bob, ["a"]); await g.end_question()
    await g.next()                                      # Q2: Ann wrong (picks the rich-text option)
    await g.submit(ann, ["c"]); await g.submit(cy, ["b"]); await g.end_question()
    await g.next()                                      # Q3: nobody answers
    await g.end_question()
    await g.next()                                      # Q4: winners=1 -> Bob is first, Ann arrives too late
    await g.submit(bob, ["b"]); await g.submit(ann, ["b"])
    await g.next()                                      # Q5: buzzer, Cy buzzes and is judged right
    await g.buzz(cy)
    await asyncio.sleep(0.2)
    await g.resolve_buzz(True)
    await g.adjust_score(ann.id, -150)                  # host docks Ann 150 points by hand
    await g.finish()
    return db, g, ann, bob, cy


def test_every_question_with_answers_times_and_points():
    async def go():
        db, g, ann, bob, cy = await play()
        d = player_detail(db, g.id, ann.id)
        assert d["player"]["nickname"] == "Ann" and d["player"]["avatar"] == "🦊"
        rows = d["questions"]
        assert [r["index"] for r in rows] == [0, 1, 2, 3, 4]
        r1, r2, r3, r4, r5 = rows
        assert r1["correct"] and r1["earned"] == 1000 and r1["answered"] and r1["elapsed_ms"] is not None
        assert [o["chosen"] for o in r1["options"]] == [False, True, False] and [o["correct"] for o in r1["options"]] == [False, True, False]
        assert r1["submitted_at"] and abs(r1["submitted_at"] - rows[0]["submitted_at"]) < 1                 # wall-clock time of the answer
        assert not r2["correct"] and r2["earned"] == 0 and r2["options"][2]["chosen"] and "<b>Gamma</b>" in r2["options"][2]["text"]
        assert r3["answered"] is False and r3["earned"] == 0 and r3["elapsed_ms"] is None and r3["submitted_at"] is None
        assert r4["answered"] is False and r4["earned"] == 0 and r4["winners"] == 1          # Ann was too late: Bob took the only place
        assert r5["answered"] is False                                                        # didn't buzz
        assert [r["running"] for r in rows] == [1000, 1000, 1000, 1000, 1000]
    run(go())


def test_scores_add_up_and_manual_adjustments_are_shown_separately():
    async def go():
        db, g, ann, bob, cy = await play()
        s = player_detail(db, g.id, ann.id)["summary"]
        assert s["earned"] == 1000 and s["score"] == 850 and s["adjustment"] == -150          # 1000 earned, host took 150 off
        assert s["score"] == s["earned"] + s["adjustment"]
        assert (s["asked"], s["correct"], s["answered"]) == (5, 1, 2) and s["accuracy"] == 20
        assert s["players"] == 3 and s["rank"] in (1, 2, 3)
        assert s["fastest_ms"] is not None and s["avg_correct_ms"] is not None
    run(go())


def test_one_winner_question_shows_who_took_it_and_the_buzzer_judgement():
    async def go():
        db, g, ann, bob, cy = await play()
        bob_q4 = player_detail(db, g.id, bob.id)["questions"][3]
        assert bob_q4["correct"] and bob_q4["earned"] == 1000 and bob_q4["claim_rank"] == 1 and bob_q4["winners"] == 1
        cy5 = player_detail(db, g.id, cy.id)["questions"][4]
        assert cy5["buzzed"] and cy5["correct"] and cy5["earned"] == 700 and cy5["type"] == "buzzer" and cy5["options"] == []
    run(go())


def test_tie_breakers_note_who_was_not_in_the_round_and_unasked_questions_are_skipped():
    async def go():
        db = Database(":memory:")
        qid = db.create_quiz(validate_quiz({"title": "T", "questions": [q("One"), q("Never reached")]}))
        g = GameManager(db).create(qid, {})
        a, b = g.join("A"), g.join("B")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next(); await g.end_question()
        await g.add_question(q("Tie"), only=[a.id])
        await g.finish()                                      # ended early: "Never reached" was never asked
        rows_b = player_detail(db, g.id, b.id)["questions"]
        assert [r["index"] for r in rows_b] == [0]            # only the one that was asked
        g2 = GameManager(db).create(qid, {})
        a2, b2 = g2.join("A"), g2.join("B")
        for p in (a2, b2):
            await g2.connect("player", FakeWS(), p)
        await g2.next(); await g2.end_question()
        await g2.add_question(q("Tie"), only=[a2.id])
        await g2.next(); await g2.end_question()
        rows = player_detail(db, g2.id, b2.id)["questions"]
        assert rows[1]["tie_breaker"] and rows[1]["not_in_round"] and not player_detail(db, g2.id, a2.id)["questions"][1]["not_in_round"]
        assert player_detail(db, g2.id, b2.id)["summary"]["asked"] == 1              # the round he wasn't in doesn't count against him
    run(go())


def test_unknown_game_or_player_and_leaderboard_ids():
    async def go():
        db, g, ann, bob, cy = await play()
        assert player_detail(db, 9999, ann.id) is None and player_detail(db, g.id, 9999) is None
        ids = [r["id"] for r in game_results(db, g.id)["leaderboard"]]
        assert sorted(ids) == sorted([ann.id, bob.id, cy.id])
    run(go())


def test_endpoint_is_owner_scoped(client, app):
    qid = client.post("/api/quizzes/import", json={"text": "# T\nQ: One\n- A*\n- B\n"}).json()["id"]
    game = client.post("/api/games", json={"quiz_id": qid}).json()
    code = game["code"]
    assert client.post("/api/join", json={"code": code, "nickname": "Zed"}).status_code == 200
    pid = next(iter(app.state.manager.get(code).players))
    r = client.get(f"/api/games/{game['id']}/players/{pid}")
    assert r.status_code == 200 and r.json()["player"]["nickname"] == "Zed"
    assert client.get(f"/api/games/{game['id']}/players/99999").status_code == 404
    _, other = host_session(app, client, "otto")
    assert other.get(f"/api/games/{game['id']}/players/{pid}").status_code == 404        # not their game
    assert client.get(f"/api/games/{game['id']}/results").json()["leaderboard"][0]["id"] == pid

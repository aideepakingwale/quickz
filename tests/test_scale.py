"""The speed-ups for hundreds of players must never change what anyone sees: caches are checked against the plain computation."""
import asyncio
import json
import time

from quickz import engine
from quickz.config import Settings
from quickz.db import Database
from quickz.engine import GameError, GameManager
from quickz.questions import validate_quiz
from quickz.server import create_app

import pytest


class WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(json.loads(json.dumps(m)))

    async def close(self, code=1000):
        pass


def run(coro):
    return asyncio.run(coro)


def opts(*t):
    return [{"id": f"o{i}", "text": x} for i, x in enumerate(t)]


QS = [
    {"type": "mcq", "text": "A", "options": opts("a", "b", "c"), "correct": ["o1"], "rich": True, "speed_weight": 0},
    {"type": "order", "text": "Sort", "options": opts("a", "b", "c", "d"), "rich": True},
    {"type": "match", "text": "Pairs", "options": opts("a", "b", "c"), "targets": opts("1", "2", "3"), "rich": True},
    {"type": "text", "text": "T", "answers": ["x"], "rich": True},
]


def make(n=6, **settings):
    db = Database(":memory:")
    g = GameManager(db).create(db.create_quiz(validate_quiz({"title": "T", "questions": QS})), {"auto_reveal": False, **settings})
    ps = [g.join(f"P{i}") for i in range(n)]
    return db, g, ps


def fresh_order(g):
    return [p.id for p in sorted(g._live_players(), key=lambda p: (-p.score, p.joined_at, p.id))]


def test_the_cached_leaderboard_is_always_the_true_leaderboard():
    async def go():
        _, g, ps = make(5)
        assert [p.id for p in g._ranks()["order"]] == fresh_order(g)
        await g.adjust_score(ps[3].id, 500)
        assert [p.id for p in g._ranks()["order"]] == fresh_order(g) and g._ranks()["order"][0].id == ps[3].id
        await g.adjust_score(ps[1].id, 900)
        assert g._ranks()["order"][0].id == ps[1].id
        await g.kick(ps[1].id)
        assert ps[1].id not in g._ranks()["rank"] and g._ranks()["order"][0].id == ps[3].id
        late = g.join("Late")
        assert late.id in g._ranks()["rank"]
        await g.next()
        await g.submit(ps[0], ["o1"])
        assert [p.id for p in g._ranks()["order"]][0] in (ps[3].id, ps[0].id) and [p.id for p in g._ranks()["order"]] == fresh_order(g)

    run(go())


def test_views_are_identical_with_and_without_the_per_push_cache():
    async def go():
        db, g, ps = make(8)
        sockets = {p.id: WS() for p in ps}
        for p in ps:
            await g.connect("player", sockets[p.id], p)
        await g.connect("host", WS()); await g.connect("screen", WS())

        def all_views():
            ranks = g._ranks()
            views = {"p": [g.view_player(p, ranks) for p in ps], "s": g.view_screen(ranks), "h": g.view_host(ranks)}
            for v in views["p"] + [views["s"], views["h"]]:
                v.pop("server_now")                                          # the only thing that legitimately differs between two builds
            return json.dumps(views, sort_keys=True, default=str)

        for step in range(len(QS)):
            await g.next()
            assert g._memo is None
            if g.q["type"] == "mcq":
                for p in ps[:4]:
                    await g.submit(p, ["o1"])
            plain = all_views()
            g._memo = {}
            cached = all_views()
            g._memo = None
            assert plain == cached, f"views differ on question {step}"
            await g.end_question()
            plain = all_views()
            g._memo = {}
            cached = all_views()
            g._memo = None
            assert plain == cached, f"reveal views differ on question {step}"
        # per-player shuffles survive the shared cache: two players' ordering cards need not match, each is stable
        await g.finish()

    run(go())


def test_shuffled_cards_are_still_per_player_under_the_cache():
    async def go():
        _, g, ps = make(12)
        await g.next(); await g.end_question(); await g.next()            # on the ordering question
        g._memo = {}
        seen = {tuple(o["id"] for o in g.view_player(p, g._ranks())["q"]["options"]) for p in ps}
        g._memo = None
        assert len(seen) > 1                                              # not everyone got the same shuffle
        again = {tuple(o["id"] for o in g.view_player(p, g._ranks())["q"]["options"]) for p in ps}
        assert seen == again

    run(go())


def test_active_player_tracking_matches_reality_and_drives_auto_reveal():
    async def go():
        _, g, ps = make(4, auto_reveal=True)
        truth = lambda: {p.id for p in g.players.values() if p.active and not p.kicked}
        socks = {p.id: WS() for p in ps}
        for p in ps[:3]:
            await g.connect("player", socks[p.id], p)                    # the 4th never connects
        assert g._active_ids == truth()
        await g.add_bots(2)
        assert g._active_ids == truth()
        g.disconnect("player", socks[ps[2].id], ps[2])
        assert g._active_ids == truth() and ps[2].id not in g._active_ids
        await g.kick(ps[1].id)
        assert g._active_ids == truth()
        await g.connect("player", socks[ps[2].id], ps[2])
        await g.next()
        bots = [p for p in g.players.values() if p.is_bot]
        for b in g._bot_tasks:
            b.cancel()
        await g.submit(ps[0], ["o1"])
        assert g.phase == "question"                                     # ps[2] and the bots are still to answer
        await g.submit(ps[2], ["o1"])
        for b in bots:
            await g.submit(b, ["o1"])
        assert g.phase == "reveal"                                       # everyone connected has answered: auto reveal

    run(go())


def test_auto_reveal_waits_only_for_the_players_a_tie_breaker_is_about():
    async def go():
        db = Database(":memory:")
        g = GameManager(db).create(db.create_quiz(validate_quiz({"title": "T", "questions": [QS[0]]})), {"auto_reveal": True})
        ps = [g.join(f"P{i}") for i in range(4)]
        for p in ps:
            await g.connect("player", WS(), p)
        await g.next(); await g.end_question()
        await g.add_question({**QS[0], "text": "Decider"}, only=[ps[0].id, ps[1].id])
        await g.next()
        await g.submit(ps[0], ["o1"])
        assert g.phase == "question"
        await g.submit(ps[1], ["o1"])
        assert g.phase == "reveal"                                        # the two tied players answered; the others are only watching

    run(go())


def test_the_player_cap_is_a_setting(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, public_url="http://x", setup_token="t", max_players=3), cloud=False)
    try:
        db = app.state.db
        g = app.state.manager.create(db.create_quiz(validate_quiz({"title": "T", "questions": [QS[0]]})), {})
        for i in range(3):
            g.join(f"P{i}")
        with pytest.raises(GameError):
            g.join("one too many")
    finally:
        engine.MAX_PLAYERS = 500


def test_hundreds_of_players_answering_stays_fast():
    async def go():
        _, g, ps = make(400)
        for p in ps:
            await g.connect("player", WS(), p)
        await g.next()
        t = time.perf_counter()
        for p in ps:
            await g.submit(p, ["o1"])
        answering = time.perf_counter() - t
        t = time.perf_counter()
        await g.end_question()
        reveal = time.perf_counter() - t
        assert answering < 3.0 and reveal < 2.0, (answering, reveal)       # generous: ~0.05 s and ~0.03 s on a laptop

    engine.MAX_PLAYERS = 5000
    try:
        run(go())
    finally:
        engine.MAX_PLAYERS = 500

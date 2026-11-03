"""The show: staged reveal ("final answers locked"), audience reactions, host soundboard cues, crowd sample, buzzer reaction times."""
import asyncio

import pytest

from quickz import engine
from quickz.db import Database
from quickz.engine import CROWD_MAX, GameManager
from quickz.questions import parse_text_quiz

QUIZ = """# T
Q: One
- A
- B*
- C
time: 20

Q: Buzz
type: buzzer
time: 20
"""


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self, code=1000):
        pass

    def of(self, kind):
        return [m for m in self.sent if m["t"] == kind]

    def last_state(self):
        return [m for m in self.sent if m["t"] == "state"][-1]["v"]


def make(settings=None, quiz=QUIZ):
    db = Database(":memory:")
    qid = db.create_quiz(parse_text_quiz(quiz))
    return GameManager(db).create(qid, {"speed_bonus": False, **(settings or {})})


def run(coro):
    return asyncio.run(coro)


def correct_id(g):
    return g.q["correct"][0]


def test_new_settings_are_validated():
    g = make({"reveal_pause": 3, "sound_pack": "arcade", "reactions": True})
    assert (g.settings["reveal_pause"], g.settings["sound_pack"], g.settings["reactions"]) == (3, "arcade", True)
    bad = make({"reveal_pause": 7, "sound_pack": "death-metal", "reactions": "yes"})
    assert (bad.settings["reveal_pause"], bad.settings["sound_pack"], bad.settings["reactions"]) == (0, "gameshow", False)
    for junk in (True, "3", 2.5, None, -1):
        assert engine.clean_setting("reveal_pause", junk)[0] is False

    async def go():
        await g.set_setting("reveal_pause", 5)
        await g.set_setting("sound_pack", "quiet")
        await g.set_setting("reveal_pause", 99)
        await g.set_setting("sound_pack", "nope")
        await g.set_setting("not_a_setting", True)
        assert (g.settings["reveal_pause"], g.settings["sound_pack"]) == (5, "quiet")
        assert "not_a_setting" not in g.settings

    run(go())


def test_no_pause_means_an_instant_reveal():
    async def go():
        g = make()
        g.join("Ann Lee")
        await g.next()
        await g.end_question()
        assert g.phase == "reveal" and not g.suspense

    run(go())


def test_staged_reveal_locks_answers_then_reveals():
    async def go():
        g = make({"reveal_pause": 2})
        g.settings["reveal_pause"] = 0.3                       # a short pause so the test stays quick
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        screen, phone = FakeWS(), FakeWS()
        await g.connect("screen", screen)
        await g.connect("player", phone, b)
        await g.next()
        await g.submit(a, [correct_id(g)])
        await g.end_question()                                 # the clock ran out
        assert g.phase == "question" and g.suspense            # not revealed yet
        assert screen.last_state()["suspense"]["until"] > engine.now_ms() and phone.last_state()["suspense"]
        await g.submit(b, [correct_id(g)])                     # too late: the room is holding its breath
        assert b.id not in g.answers[g.q["id"]]
        await g.add_time(10)                                   # the clock cannot be restarted either
        assert g.suspense
        await asyncio.sleep(0.45)
        assert g.phase == "reveal" and not g.suspense
        assert screen.last_state()["suspense"] is None and screen.last_state()["reveal"]["correct_count"] == 1

    run(go())


def test_pressing_reveal_again_skips_the_wait():
    async def go():
        g = make({"reveal_pause": 5})
        g.join("Ann Lee")
        await g.next()
        await g.end_question()
        assert g.suspense
        await g.end_question()
        assert g.phase == "reveal" and not g.suspense

    run(go())


def test_everyone_answering_also_goes_through_the_pause():
    async def go():
        g = make({"reveal_pause": 5, "auto_reveal": True})
        p = g.join("Ann Lee")
        await g.connect("player", FakeWS(), p)
        await g.next()
        await g.submit(p, [correct_id(g)])
        assert g.phase == "question" and g.suspense
        await g.finish()                                       # ending the game must not leave a drum-roll running
        assert g.phase == "finished" and not g.suspense

    run(go())


def test_pause_applies_to_buzzer_rounds_too():
    async def go():
        g = make({"reveal_pause": 5}, quiz="# T\nQ: Buzz\ntype: buzzer\ntime: 20\n")
        p = g.join("Ann Lee")
        await g.next()
        assert g.q["type"] == "buzzer"
        await g.buzz(p)
        await asyncio.sleep(0.2)
        assert g.holder == p.id
        await g.resolve_buzz(True)                             # judged right: the room waits before the reveal
        assert g.suspense and g.phase == "question"
        await g.resolve_buzz(False)                            # a second ruling during the pause is ignored
        assert g.answers[g.q["id"]][p.id]["correct"] is True
        await g.end_question()
        assert g.phase == "reveal"

    run(go())


def test_reactions_are_whitelisted_throttled_and_batched():
    async def go():
        g = make({"reactions": True})
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        screen, host = FakeWS(), FakeWS()
        await g.connect("screen", screen)
        await g.connect("host", host)
        g.react(a, "👏")
        g.react(a, "👏")                                       # same player within a second: dropped
        g.react(b, "🔥")
        g.react(b, "<script>")                                 # not on the list
        await asyncio.sleep(0.6)
        want = [{"t": "react", "e": {"👏": 1, "🔥": 1},          # "e" keeps an already-open older projector page working
                 "items": [{"e": "👏", "n": "Ann Lee", "a": a.avatar}, {"e": "🔥", "n": "Bob Ray", "a": b.avatar}]}]
        assert screen.of("react") == want and host.of("react") == want       # the projector and the host console both see who threw what
        off = make()                                           # reactions default off
        s2 = FakeWS()
        await off.connect("screen", s2)
        off.react(off.join("Cy Dee"), "👏")
        await asyncio.sleep(0.6)
        assert s2.of("react") == []

    run(go())


def test_a_reaction_storm_is_sampled():
    async def go():
        g = make({"reactions": True})
        host = FakeWS()
        await g.connect("host", host)                          # a host console alone is enough to receive them
        for i in range(60):
            g.react(g.join(f"Player {i} Test"), "👏")
        await asyncio.sleep(0.6)
        assert len(host.of("react")[0]["items"]) == engine.REACT_BATCH_MAX

    run(go())


def test_soundboard_cues_reach_only_the_projector_and_only_known_names():
    async def go():
        g = make()
        screen, host = FakeWS(), FakeWS()
        await g.connect("screen", screen)
        await g.connect("host", host)
        await g.on_host({"t": "cue", "name": "applause"})
        await g.on_host({"t": "cue", "name": "rm -rf"})
        await asyncio.sleep(0.15)
        await g.on_host({"t": "cue", "name": "drumroll"})
        await g.on_host({"t": "cue", "name": "airhorn"})       # pressed within 120 ms of the last one: thinned out
        assert [m["name"] for m in screen.of("cue")] == ["applause", "drumroll"]
        assert host.of("cue") == []

    run(go())


def test_crowd_sample_is_per_option_and_capped():
    async def go():
        g = make()
        screen = FakeWS()
        await g.connect("screen", screen)
        await g.next()
        opts = [o["id"] for o in g.q["options"]]
        for i in range(400):
            p = g.join(f"Player Number{i}", avatar=engine.AVATARS[i % 20])
            await g.submit(p, [opts[0] if i % 4 else opts[1]])
        assert 0 < sum(len(v) for v in g._crowd(g.q).values()) <= CROWD_MAX + len(opts)
        await g.end_question()
        crowd = screen.last_state()["crowd"]
        assert set(crowd) == set(opts) and len(crowd[opts[0]]) > len(crowd[opts[1]]) > 0 and crowd[opts[2]] == []
        phone = FakeWS()
        await g.connect("player", phone, next(iter(g.players.values())))
        assert "crowd" not in phone.last_state()               # phones never receive the sample

    run(go())


def test_buzzer_reaction_times_and_who_was_in_the_photo_finish():
    async def go():
        g = make()
        a, b = g.join("Ann Lee"), g.join("Bob Ray")
        await g.next()
        await g.end_question()
        await g.next()
        assert g.q["type"] == "buzzer"
        await g.buzz(b)
        await g.buzz(a)
        await asyncio.sleep(0.25)
        v = g._buzz_view()
        assert len(v["order"]) == len(v["times"]) == 2 and all(isinstance(t, int) and t >= 0 for t in v["times"])
        assert v["times"][0] <= v["times"][1]

    run(go())


def test_phone_and_server_reaction_lists_match():
    """The phone's bar and tray (play.js) offer exactly what the server will accept (engine.REACTIONS)."""
    import re
    from pathlib import Path
    js = (Path(engine.__file__).parent / "static" / "js" / "play.js").read_text(encoding="utf-8")
    bar = re.findall(r"'([^']+)'", re.search(r"const REACT_BAR = \[([^\]]*)\]", js).group(1))
    more = re.findall(r"'([^']+)'", re.search(r"const REACT_MORE = \[([^\]]*)\]", js).group(1))
    assert bar + more == list(engine.REACTIONS) and len(set(engine.REACTIONS)) == len(engine.REACTIONS) >= 40

"""WYSIWYG rich text (sanitising), audio/video media, and per-question speed weighting."""
import asyncio
import json
import struct

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import CSRF, SETUP_TOKEN, setup_admin
from quickz.config import Settings
from quickz.db import Database
from quickz.engine import GameManager
from quickz.questions import (ValidationError, md_to_html, sanitize_html, strip_tags, upgrade_question,
                              validate_question, validate_quiz)
from quickz.server import create_app


# ======================================================================= rich text
XSS = [
    "<script>alert(1)</script>Hi", "<img src=x onerror=alert(1)>Hi", "<a href=\"javascript:alert(1)\">x</a>",
    "<svg onload=alert(1)></svg>Hi", "<b onmouseover=alert(1)>x</b>", "<iframe src=\"//evil\"></iframe>Hi",
    "<style>@import 'x'</style>Hi", "<p style=\"background:url(javascript:alert(1))\">x</p>",
    "<span style=\"color:red;position:fixed;top:0\">x</span>", "<object data=x></object>Hi", "<math><mi>x</mi></math>Hi",
    "<input autofocus onfocus=alert(1)>", "<div onclick=alert(1)>x</div>", "<<script>script>alert(1)<</script>/script>",
]


@pytest.mark.parametrize("payload", XSS)
def test_sanitizer_removes_every_active_construct(payload):
    out = sanitize_html(payload).lower()
    for bad in ("<script", "onerror", "onload", "onclick", "onmouseover", "onfocus", "javascript:", "<img", "<svg", "<iframe",
                "<a ", "<style", "<object", "<math", "<input", "position", "url(", "@import", "href"):
        assert bad not in out, (payload, out)


def test_sanitizer_keeps_the_formatting_the_editor_produces():
    html = '<p>Water is H<sub>2</sub>O, <strong>E</strong>=mc<sup>2</sup> and <em>x</em> <u>y</u> <s>z</s></p><ul><li>one</li><li>two</li></ul>'
    out = sanitize_html(html)
    for keep in ("<sub>2</sub>", "<sup>2</sup>", "<b>E</b>", "<i>x</i>", "<u>y</u>", "<s>z</s>", "<ul><li>one</li><li>two</li></ul>"):
        assert keep in out, out
    assert "<strong" not in out and "<em" not in out                      # canonical tags only


def test_colour_and_alignment_are_validated_not_trusted():
    assert 'style="color:#d6405a"' in sanitize_html('<span style="color:#D6405A">x</span>')       # normalised to lower case
    assert 'style="color:rgb(214, 64, 90)"' in sanitize_html('<span style="color: rgb(214, 64, 90)">x</span>')
    assert "style" not in sanitize_html('<span style="color:red">x</span>')            # named/expression colours dropped
    assert "style" not in sanitize_html('<span style="color:expression(alert(1))">x</span>')
    assert 'style="text-align:center"' in sanitize_html('<p style="text-align:center">x</p>')
    assert "style" not in sanitize_html('<p style="text-align:justify;float:left">x</p>')


def test_output_is_always_well_formed_and_text_is_escaped():
    assert sanitize_html("<b>open <i>nested") == "<b>open <i>nested</i></b>"
    assert "&lt;" in sanitize_html("5 < 6 & 7 > 3") and "&amp;" in sanitize_html("a & b")
    assert sanitize_html("</b></i>stray") == "stray"


def test_inline_mode_for_answers_flattens_blocks_and_breaks():
    assert sanitize_html("<p>one</p><p>two</p><ul><li>x</li></ul>", inline=True) == "one two x"
    assert "<br" not in sanitize_html("a<br>b", inline=True)
    assert "<b>bold</b>" in sanitize_html("<b>bold</b>", inline=True)


def test_strip_tags_gives_plain_text():
    assert strip_tags("<p>Hello <b>world</b></p><ul><li>a</li><li>b</li></ul>") == "Hello world a b"
    assert strip_tags("a&amp;b &lt;c&gt;") == "a&b <c>"


def test_legacy_markdown_is_converted_once():
    assert md_to_html("**b** *i* `c`\nnext & <x>") == "<b>b</b> <i>i</i> <code>c</code><br>next &amp; &lt;x&gt;"
    q = upgrade_question({"text": "What is **2+2**?", "options": [{"id": "a", "text": "*four*"}]})
    assert q["rich"] is True and "<b>2+2</b>" in q["text"] and q["options"][0]["text"] == "<i>four</i>"
    assert upgrade_question(dict(q)) == q                                             # idempotent


def test_validate_question_sanitises_rich_input_and_counts_plain_length():
    q = validate_question({"type": "mcq", "rich": True, "text": "<p>Hi <b>there</b></p><script>x()</script>",
                           "options": [{"id": "a", "text": "<b>Yes</b><p>block</p>"}, {"id": "b", "text": "No"}], "correct": ["a"]})
    assert "script" not in q["text"] and "<b>there</b>" in q["text"]
    assert q["options"][0]["text"] == "<b>Yes</b> block" and q["rich"] is True
    with pytest.raises(ValidationError):                                               # markup only = no text
        validate_question({"type": "buzzer", "rich": True, "text": "<p><br></p><b></b>"})
    with pytest.raises(ValidationError):                                               # limit is on visible text, not markup
        validate_question({"type": "buzzer", "rich": True, "text": "x" * 1001})
    assert validate_question({"type": "buzzer", "rich": True, "text": "<b>" + "x" * 900 + "</b>"})["text"]


def test_non_rich_input_is_treated_as_markdown_not_html():
    q = validate_question({"type": "buzzer", "text": "<script>alert(1)</script> **x**"})
    assert "<script" not in q["text"] and "&lt;script&gt;" in q["text"] and "<b>x</b>" in q["text"]


def test_old_databases_are_upgraded_on_open(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    legacy = [{"id": "q1", "type": "mcq", "text": "What is **big**?", "image": "", "options": [{"id": "a", "text": "*a*"}, {"id": "b", "text": "b"}],
               "correct": ["a"], "time_limit": 20, "points": 1000, "explanation": ""}]
    db._run("INSERT INTO quizzes(title,description,questions,created_at,updated_at) VALUES('Old','',?,1,1)", (json.dumps(legacy),))
    del db
    q = Database(path).get_quiz(1)["questions"][0]
    assert q["rich"] is True and "<b>big</b>" in q["text"] and q["options"][0]["text"] == "<i>a</i>"


def test_outline_and_analytics_use_plain_text(client):
    qid = client.post("/api/quizzes", json={"title": "R", "questions": [{"type": "buzzer", "rich": True, "text": "<p>Name <b>this</b> tune</p>"}]}).json()["id"]
    code = client.post("/api/games", json={"quiz_id": qid}).json()["code"]
    with client.websocket_connect(f"/ws/host/{code}") as ws:
        assert ws.receive_json()["v"]["outline"][0]["text"] == "Name this tune"


# ======================================================================= media validation
def mq(**media):
    return {"type": "buzzer", "text": "Q", "media": {"kind": "audio", "url": "/uploads/0123456789abcdef.mp3", "where": "screen", **media}}


def test_media_validation():
    ok = validate_question(mq())["media"]
    assert ok == {"kind": "audio", "url": "/uploads/0123456789abcdef.mp3", "where": "screen", "timer_after": False}
    assert validate_question({"type": "buzzer", "text": "Q"})["media"] is None
    for bad in (mq(kind="image"), mq(kind="flash"), mq(url="http://evil/x.mp3"), mq(url="/uploads/../x.mp3"), mq(url="/etc/passwd"),
                mq(url="/uploads/a.exe"), mq(where="everywhere")):
        with pytest.raises(ValidationError):
            validate_question(bad)


def test_timer_after_media_is_ignored_for_buzzer_questions_but_kept_for_mcq():
    assert validate_question(mq(timer_after=True))["media"]["timer_after"] is False
    q = validate_question({"type": "mcq", "text": "Q", "options": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}], "correct": ["a"],
                           "media": {"kind": "video", "url": "/uploads/0123456789abcdef.mp4", "where": "both", "timer_after": True}})
    assert q["media"]["timer_after"] is True and q["media"]["where"] == "both"


def test_speed_weight_is_clamped_and_defaults_to_the_old_behaviour():
    base = {"type": "buzzer", "text": "Q"}
    assert validate_question(base)["speed_weight"] == 50
    assert validate_question({**base, "speed_weight": 250})["speed_weight"] == 100
    assert validate_question({**base, "speed_weight": -4})["speed_weight"] == 0
    assert validate_question({**base, "speed_weight": "abc"})["speed_weight"] == 50


def test_text_import_understands_speed():
    from quickz.questions import parse_text_quiz
    q = parse_text_quiz("# T\nQ: One\n- A*\n- B\nspeed: 80\n")["questions"][0]
    assert q["speed_weight"] == 80


# ======================================================================= uploads
def wav(n_samples=800):
    data = b"\x00\x00" * n_samples
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16) + b"data" + struct.pack("<I", len(data)) + data


SAMPLES = {
    "mp3-id3": (b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64, "audio", ".mp3"),
    "mp3-sync": (b"\xff\xfb\x90\x00" + b"\x00" * 64, "audio", ".mp3"),
    "ogg": (b"OggS\x00\x02" + b"\x00" * 64, "audio", ".ogg"),
    "flac": (b"fLaC" + b"\x00" * 64, "audio", ".flac"),
    "wav": (wav(), "audio", ".wav"),
    "m4a": (b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * 64, "audio", ".m4a"),
    "mp4": (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 64, "video", ".mp4"),
    "webm": (b"\x1aE\xdf\xa3" + b"\x00" * 64, "video", ".webm"),
    "png": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image", ".png"),
}


@pytest.mark.parametrize("name", SAMPLES)
def test_upload_accepts_media_by_content_not_by_name_or_type(client, name):
    blob, kind, ext = SAMPLES[name]
    r = client.post("/api/uploads", content=blob, headers={"content-type": "application/x-lies"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == kind and r.json()["url"].endswith(ext)
    assert client.get(r.json()["url"]).content == blob


@pytest.mark.parametrize("blob", [b"MZ\x90\x00" + b"\x00" * 64, b"<html><script>alert(1)</script></html>", b"#!/bin/sh\nrm -rf /", b"just text, long enough"])
def test_upload_rejects_executables_scripts_and_text(client, blob):
    assert client.post("/api/uploads", content=blob, headers={"content-type": "audio/mpeg"}).status_code == 400


def test_media_size_cap_is_enforced_while_streaming_and_leaves_no_partial_files(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, public_url="http://x", setup_token=SETUP_TOKEN, max_media_mb=1), cloud=False)
    with TestClient(app, headers=CSRF) as c:
        setup_admin(c)
        assert c.post("/api/uploads", content=wav(100_000)).status_code == 200                    # 0.2 MB: fine
        assert c.post("/api/uploads", content=wav(700_000)).status_code == 413                    # 1.4 MB > the 1 MB cap
        big_png = SAMPLES["png"][0] + b"\x00" * (6 * 1024 * 1024)
        assert c.post("/api/uploads", content=big_png).status_code == 413                         # images stay capped at 5 MB
    assert not list((tmp_path / "uploads").glob("*.part"))                                        # nothing half-written left behind


def test_media_is_served_with_range_support_so_video_can_seek(client):
    blob = SAMPLES["mp4"][0] + bytes(range(200))
    url = client.post("/api/uploads", content=blob).json()["url"]
    r = client.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == blob[10:20]
    assert r.headers["content-range"].startswith("bytes 10-19/") and r.headers["accept-ranges"] == "bytes"
    assert client.get(url).headers["cache-control"].startswith("public")


def test_upload_requires_login_and_auth_state_publishes_the_cap(app):
    with TestClient(app, headers=CSRF) as anon:
        assert anon.post("/api/uploads", content=wav()).status_code == 401
        assert anon.get("/api/auth/state").json()["max_media_mb"] == 50


# ======================================================================= scoring
def make_game(questions, settings=None):
    db = Database(":memory:")
    qid = db.create_quiz(validate_quiz({"title": "T", "questions": questions}))
    mgr = GameManager(db)
    return db, mgr.create(qid, settings or {})


def mcq(**kw):
    return {"type": "mcq", "text": "Q?", "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct": ["a"],
            "time_limit": 20, "points": 1000, **kw}


def test_points_formula_for_each_weight():
    _, g = make_game([mcq()])
    for weight, instant, half, last in [(0, 1000, 1000, 1000), (25, 1000, 875, 750), (50, 1000, 750, 500), (100, 1000, 500, 0)]:
        g.questions[0]["speed_weight"] = weight
        q = g.questions[0]
        assert [g._points(q, ms) for ms in (0, 10_000, 20_000)] == [instant, half, last], weight
    assert g._points(g.questions[0], 99_999) == 0                       # clamped to the end of the window


def test_each_question_has_its_own_weight_and_the_game_switch_overrides_all():
    _, g = make_game([mcq(speed_weight=0), mcq(speed_weight=100, points=500)])
    assert g._points(g.questions[0], 20_000) == 1000                    # flat question
    assert g._points(g.questions[1], 20_000) == 0                       # pure-speed question
    _, off = make_game([mcq(speed_weight=100)], {"speed_bonus": False})
    assert off._points(off.questions[0], 20_000) == 1000                # host disabled speed scoring for the game


def test_buzzer_points_stay_flat_and_public_view_reports_effective_weight():
    _, g = make_game([{"type": "buzzer", "text": "B", "speed_weight": 90}, mcq(speed_weight=60)])
    assert g._speed_weight(g.questions[0]) == 0
    g.index = 1
    g.phase = "question"
    assert g._public_q("player")["speed"] == 60


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)

    async def close(self, code=1000):
        pass


def run(coro):
    return asyncio.run(coro)


def test_answer_speed_changes_the_score_per_the_questions_weight():
    async def go():
        _, g = make_game([mcq(speed_weight=100)], {"auto_reveal": False})
        a, b = g.join("Ann"), g.join("Bob")
        for p in (a, b):
            await g.connect("player", FakeWS(), p)
        await g.next()
        g.started_ms -= 10_000                                           # pretend 10 s of the 20 s have passed
        await g.submit(a, ["a"])
        assert 480 <= a.score <= 510                                     # ~50% of 1000 at half time with weight 100
        g.started_ms += 10_000                                           # and Bob answers instantly
        await g.submit(b, ["a"])
        assert b.score > 980 and b.score > a.score

    run(go())


# ======================================================================= media gating
def media_q(where="screen", timer_after=True, **kw):
    return mcq(media={"kind": "audio", "url": "/uploads/0123456789abcdef.mp3", "where": where, "timer_after": timer_after}, **kw)


def test_answers_are_locked_and_the_clock_frozen_until_the_media_ends():
    async def go():
        _, g = make_game([media_q()])
        p = g.join("Ann")
        await g.connect("player", FakeWS(), p)
        await g.next()
        q = g._public_q("player")
        assert q["awaiting_media"] is True and q["remaining_ms"] == 20_000        # frozen at full time
        await g.submit(p, ["a"])
        assert not g.answers.get(g.q["id"])                                         # nobody can answer early
        await g.add_time(10)
        assert g.paused_remaining == 20_000                                         # nor extend a clock that isn't running
        await g.start_after_media()
        q = g._public_q("player")
        assert q["awaiting_media"] is False and q["remaining_ms"] is None
        await g.submit(p, ["a"])
        assert g.answers[g.q["id"]][p.id]["correct"] is True
        assert g.answers[g.q["id"]][p.id]["elapsed_ms"] < 1500                       # speed counts from the END of the clip

    run(go())


def test_speed_is_measured_from_when_the_clip_ends_not_when_the_question_began():
    async def go():
        _, g = make_game([media_q(speed_weight=100)], {"auto_reveal": False})
        p = g.join("Ann")
        await g.connect("player", FakeWS(), p)
        await g.next()
        await asyncio.sleep(0.4)                                                     # the clip is playing
        await g.start_after_media()
        await g.submit(p, ["a"])
        assert p.score > 950                                                         # the 0.4 s of listening didn't cost points

    run(go())


def test_bots_wait_for_the_media_and_a_normal_question_is_unaffected():
    async def go():
        _, g = make_game([media_q(), mcq()])
        await g.add_bots(3)
        await g.next()
        assert g._bot_tasks == [] and g.media_wait is True
        await g.start_after_media()
        assert len(g._bot_tasks) == 3
        await g.end_question()
        await g.next()
        assert g.media_wait is False and g._public_q("player")["awaiting_media"] is False
        assert g.ends_ms > g.started_ms

    run(go())


def test_revealing_or_finishing_while_waiting_clears_the_wait():
    async def go():
        _, g = make_game([media_q(), mcq()])
        await g.next()
        await g.end_question()
        assert g.media_wait is False and g.phase == "reveal"
        await g.finish()
        assert g.media_wait is False

    run(go())


def test_media_url_goes_only_to_the_devices_that_should_play_it_and_never_after_the_question():
    async def go():
        for where, screen, player in (("screen", True, False), ("players", False, True), ("both", True, True)):
            _, g = make_game([media_q(where=where, timer_after=False)])
            await g.next()
            assert bool(g._public_q("screen")["media"]["url"]) is screen, where
            assert bool(g._public_q("player")["media"]["url"]) is player, where
            assert g._public_q("host")["media"]["url"], where                         # the host can always preview
            assert g._public_q("player")["media"]["where"] == where                   # phones still learn it exists
            await g.end_question()
            assert all(g._public_q(r)["media"]["url"] is None for r in ("screen", "player", "host"))   # clips stop at the reveal

    run(go())


def test_replay_bumps_a_nonce_every_screen_notices():
    async def go():
        _, g = make_game([media_q(timer_after=False)])
        await g.next()
        before = g._public_q("screen")["media_nonce"]
        await g.replay_media()
        assert g._public_q("screen")["media_nonce"] == before + 1

    run(go())


def _find_state(ws, pred, n=40):
    """Next host/screen state satisfying pred (messages that don't match are skipped)."""
    for _ in range(n):
        m = ws.receive_json()
        if m.get("t") == "state" and pred(m["v"]):
            return m["v"]
    raise AssertionError("expected state never arrived")


def test_only_a_keyed_projector_can_start_the_timer(client):
    qid = client.post("/api/quizzes", json={"title": "M", "questions": [media_q(rich=True)]}).json()["id"]
    code = client.post("/api/games", json={"quiz_id": qid}).json()["code"]
    key = next(g for g in client.get("/api/games").json() if g["code"] == code)["screen_key"]
    assert key and len(key) >= 8
    with client.websocket_connect(f"/ws/host/{code}") as host:
        assert host.receive_json()["v"]["screen_key"] == key
        host.send_json({"t": "next"})
        assert _find_state(host, lambda v: v["phase"] == "question")["q"]["awaiting_media"] is True

        with client.websocket_connect(f"/ws/screen/{code}") as nokey, client.websocket_connect(f"/ws/screen/{code}?k=wrong") as wrong:
            nokey.send_json({"t": "media_ended"})
            wrong.send_json({"t": "media_ended"})
            # force a fresh push (changing a setting re-sends the host view) and check the clock is STILL frozen
            host.send_json({"t": "set", "key": "auto_reveal", "value": False})
            assert _find_state(host, lambda v: v["phase"] == "question")["q"]["awaiting_media"] is True, \
                "a projector without the key must not be able to start the timer"

            with client.websocket_connect(f"/ws/screen/{code}?k={key}") as keyed:
                keyed.receive_json()
                keyed.send_json({"t": "media_ended"})
                v = _find_state(host, lambda v: v["phase"] == "question" and not v["q"]["awaiting_media"])
                assert v["q"]["remaining_ms"] is None and v["q"]["ends_at"] > v["q"]["started_at"]


def test_projector_sockets_are_capped(client):
    code = client.post("/api/games", json={"quiz_id": client.post("/api/quizzes/import", json={"text": "# T\nQ: One\n- A*\n- B\n"}).json()["id"]}).json()["code"]
    conns = []
    try:
        for _ in range(20):
            cm = client.websocket_connect(f"/ws/screen/{code}")
            cm.__enter__()
            conns.append(cm)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/screen/{code}") as ws:
                ws.receive_json()
        assert exc.value.code == 4429
    finally:
        for cm in conns:
            cm.__exit__(None, None, None)

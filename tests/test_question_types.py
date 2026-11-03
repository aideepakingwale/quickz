"""The question types beyond multiple choice: validation, grading, the engine flow, host judging, slides, jokers, reports."""
import asyncio

import pytest

from quickz import grading
from quickz.db import Database
from quickz.engine import GameManager, game_results, player_detail
from quickz.questions import ValidationError, parse_text_quiz, validate_question, validate_quiz


def run(coro):
    return asyncio.run(coro)


def opts(*texts, image=""):
    return [{"id": f"o{i}", "text": t, "image": image} for i, t in enumerate(texts)]


def setup(questions, players=3, **settings):
    db = Database(":memory:")
    qid = db.create_quiz(validate_quiz({"title": "T", "questions": questions}))
    g = GameManager(db).create(qid, {"auto_reveal": False, **settings})
    ps = [g.join(f"P{i}") for i in range(players)]
    return db, g, ps


# ------------------------------------------------------------------ validation
def test_every_type_validates_and_unknown_ones_do_not():
    good = [
        {"type": "text", "text": "Capital?", "answers": ["Paris"]},
        {"type": "number", "text": "Year?", "number": {"answer": 1969}},
        {"type": "order", "text": "Sort", "options": opts("a", "b", "c")},
        {"type": "match", "text": "Match", "options": opts("a", "b"), "targets": opts("1", "2")},
        {"type": "pin", "text": "Where?", "image": "/uploads/0123456789abcdef.png", "pin": {"x": 40, "y": 50, "r": 9}},
        {"type": "poll", "text": "Fav?", "options": opts("a", "b")},
        {"type": "slide", "text": "Round 2"},
    ]
    for raw in good:
        q = validate_question(raw)
        assert q["type"] == raw["type"]
    with pytest.raises(ValidationError):
        validate_question({"type": "essay", "text": "x"})


def test_options_may_be_pictures_without_text_but_not_empty():
    q = validate_question({"type": "mcq", "text": "Which flag?", "options": [
        {"id": "a", "text": "", "image": "/uploads/0123456789abcdef.png"}, {"id": "b", "text": "B"}], "correct": ["a"]})
    assert q["options"][0]["image"].endswith(".png") and q["options"][1]["image"] == ""
    with pytest.raises(ValidationError):
        validate_question({"type": "mcq", "text": "x", "options": [{"id": "a", "text": ""}, {"id": "b", "text": "B"}], "correct": ["b"]})


def test_pictures_must_come_from_uploads_or_bundled_samples():
    for bad in ("http://evil.example/x.png", "/uploads/../../etc/passwd", "javascript:alert(1)", "/static/samples/../x.svg", "/static/samples/a.svg?x=1"):
        with pytest.raises(ValidationError):
            validate_question({"type": "mcq", "text": "x", "image": bad, "options": opts("a", "b"), "correct": ["o0"]})
    ok = validate_question({"type": "mcq", "text": "x", "image": "/static/samples/rocket.svg", "options": opts("a", "b"), "correct": ["o0"]})
    assert ok["image"] == "/static/samples/rocket.svg"


def test_type_specific_rules():
    with pytest.raises(ValidationError):
        validate_question({"type": "pin", "text": "x", "pin": {"x": 1, "y": 1}})                 # no picture
    with pytest.raises(ValidationError):
        validate_question({"type": "text", "text": "x", "answers": []})
    with pytest.raises(ValidationError):
        validate_question({"type": "number", "text": "x", "number": {}})
    with pytest.raises(ValidationError):
        validate_question({"type": "number", "text": "x", "number": {"answer": 5, "min": 10, "max": 20}})
    with pytest.raises(ValidationError):
        validate_question({"type": "match", "text": "x", "options": opts("a", "b"), "targets": opts("1")})
    with pytest.raises(ValidationError):
        validate_question({"type": "order", "text": "x", "options": opts("only")})
    poll = validate_question({"type": "poll", "text": "x", "points": 900, "speed_weight": 80, "winners": 3, "options": opts("a", "b")})
    assert (poll["points"], poll["speed_weight"], poll["winners"]) == (0, 0, 0)
    assert validate_question({"type": "slide", "text": "Hi", "points": 500})["points"] == 0


def test_image_effect_needs_an_image_and_a_known_name():
    base = {"type": "text", "text": "Who?", "answers": ["x"]}
    assert validate_question({**base, "image_effect": "blur"})["image_effect"] == "none"
    assert validate_question({**base, "image": "/uploads/0123456789abcdef.png", "image_effect": "blur"})["image_effect"] == "blur"
    assert validate_question({**base, "image": "/uploads/0123456789abcdef.png", "image_effect": "spin"})["image_effect"] == "none"


def test_text_format_covers_the_new_types():
    quiz = parse_text_quiz("""# Mixed
Q: Capital of France?
type: text
= Paris
= Paris, France
category: Geography
Q: Year of the Moon landing?
type: number
answer: 1969
tolerance: 1
closest: yes
unit: AD
Q: Order the planets from the sun
type: order
- Mercury
- Venus
- Earth
Q: Match
type: match
- Dog = Bark
- Cat = Meow
Q: Round 2: Music
type: slide
board: yes
Q: Best colour?
type: poll
- Red
- Blue
""")
    kinds = [q["type"] for q in quiz["questions"]]
    assert kinds == ["text", "number", "order", "match", "slide", "poll"]
    assert quiz["questions"][0]["answers"] == ["Paris", "Paris, France"] and quiz["questions"][0]["category"] == "Geography"
    assert quiz["questions"][1]["number"]["closest"] is True and quiz["questions"][1]["number"]["unit"] == "AD"
    assert [o["text"] for o in quiz["questions"][3]["options"]] == ["Dog", "Cat"]
    assert quiz["questions"][4]["board"] is True


# ------------------------------------------------------------------ grading
def test_text_answers_ignore_case_accents_punctuation_articles_and_small_typos():
    q = validate_question({"type": "text", "text": "x", "answers": ["The Eiffel Tower", "Café"]})
    assert grading.text_matches(q, "eiffel tower")
    assert grading.text_matches(q, "  THE   Eiffel-Tower!! ")
    assert grading.text_matches(q, "cafe")
    assert grading.text_matches(q, "Eifel Tower")            # one typo is forgiven on a long answer
    assert not grading.text_matches(q, "Big Ben")
    strict = validate_question({"type": "text", "text": "x", "answers": ["Paris"], "fuzzy": False})
    assert not grading.text_matches(strict, "Pariss")
    short = validate_question({"type": "text", "text": "x", "answers": ["Rome"]})
    assert not grading.text_matches(short, "Roma")           # very short answers never get typo forgiveness


def test_number_grading_exact_tolerance_and_closest():
    q = validate_question({"type": "number", "text": "x", "number": {"answer": 100, "tolerance": 2}})
    assert grading.grade(q, [101.5]) == (True, 1.0)
    assert grading.grade(q, [110]) == (False, 0.0)
    c = validate_question({"type": "number", "text": "x", "number": {"answer": 100, "tolerance": 0, "closest": True, "spread": 50}})
    ok, frac = grading.grade(c, [75])
    assert not ok and frac == pytest.approx(0.5)
    assert grading.grade(c, [200]) == (False, 0.0)


def test_order_and_match_give_partial_credit_but_never_fake_a_win():
    q = validate_question({"type": "order", "text": "x", "options": opts("a", "b", "c", "d")})
    ids = q["correct"]
    assert grading.grade(q, ids) == (True, 1.0)
    ok, frac = grading.grade(q, [ids[0], ids[1], ids[3], ids[2]])
    assert not ok and 0 < frac < 1
    assert grading.grade(q, [ids[1], ids[0], ids[3], ids[2]]) == (False, 0.0)
    m = validate_question({"type": "match", "text": "x", "options": opts("a", "b"), "targets": opts("1", "2")})
    good = [[o["id"], r] for o, r in zip(m["options"], m["correct"])]
    assert grading.grade(m, good) == (True, 1.0)
    swapped = [[good[0][0], good[1][1]], [good[1][0], good[0][1]]]
    assert grading.grade(m, swapped) == (False, 0.0)
    one = [good[0], [good[1][0], good[0][1]]]
    ok, frac = grading.grade(m, one)
    assert not ok and frac == pytest.approx(0.375)


def test_pin_scores_by_distance_in_image_width_units():
    q = validate_question({"type": "pin", "text": "x", "image": "/uploads/0123456789abcdef.png",
                           "pin": {"x": 50, "y": 50, "r": 10, "ar": 2.0}})
    assert grading.grade(q, [55, 50]) == (True, 1.0)
    assert grading.grade(q, [50, 59]) == (True, 1.0)         # 9% of the height is only 4.5% of the width on a 2:1 picture
    ok, frac = grading.grade(q, [65, 50])
    assert not ok and 0 < frac < 1
    assert grading.grade(q, [99, 99]) == (False, 0.0)


def test_garbage_answers_are_rejected_not_graded():
    order = validate_question({"type": "order", "text": "x", "options": opts("a", "b", "c")})
    assert grading.clean_answer(order, ["o0", "o1"]) is None                  # missing an item
    assert grading.clean_answer(order, ["o0", "o0", "o1"]) is None            # a repeated item
    assert grading.clean_answer(order, "nonsense") is None
    num = validate_question({"type": "number", "text": "x", "number": {"answer": 1}})
    assert grading.clean_answer(num, "abc") is None and grading.clean_answer(num, float("nan")) is None
    assert grading.clean_answer(num, "7") == [7.0]
    pin = validate_question({"type": "pin", "text": "x", "image": "/uploads/0123456789abcdef.png", "pin": {"x": 1, "y": 1}})
    assert grading.clean_answer(pin, [500, -5]) == [100.0, 0.0]               # clamped onto the picture
    assert grading.clean_answer(pin, ["a", "b"]) is None


# ------------------------------------------------------------------ the engine
def test_typed_answer_flow_scores_and_the_projector_never_sees_it_early():
    async def go():
        db, g, ps = setup([{"type": "text", "text": "Capital?", "answers": ["Paris"], "speed_weight": 0}])
        await g.next()
        screen_q = g.view_screen(g._ranks())["q"]
        assert "answers" not in screen_q and "correct" not in screen_q and screen_q["options"] == []
        await g.submit(ps[0], "paris")
        await g.submit(ps[1], "London")
        assert ps[0].score == 1000 and ps[1].score == 0
        await g.end_question()
        top = g._reveal()["detail"]["top"]
        assert {t["text"]: t["correct"] for t in top} == {"paris": True, "London": False}
        res = g.view_player(ps[1], g._ranks())["result"]
        assert res["yours"] == ["London"] and not res["correct"]

    run(go())


def test_host_can_accept_and_reject_typed_answers_after_the_reveal():
    async def go():
        db, g, ps = setup([{"type": "text", "text": "Capital?", "answers": ["Paris"], "speed_weight": 0, "fuzzy": False}])
        await g.next()
        await g.submit(ps[0], "Paris")
        await g.submit(ps[1], "Paname")                          # a nickname the quiz-setter did not think of
        await g.end_question()
        key = next(t["key"] for t in g._reveal()["detail"]["top"] if t["text"] == "Paname")
        await g.accept_answer(key, True)
        assert ps[1].score == 1000 and ps[1].streak == 1
        assert {a["player_id"]: (a["correct"], a["points"]) for a in db.game_answers(g.id)}[ps[1].id] == (1, 1000)
        assert "Paname" in g.questions[0]["answers"]
        await g.accept_answer(key, False)                        # changed their mind: points come back off
        assert ps[1].score == 0
        pkey = next(t["key"] for t in g._reveal()["detail"]["top"] if t["text"] == "Paris")
        await g.accept_answer(pkey, False)
        assert ps[0].score == 0 and not grading.text_matches(g.questions[0], "Paris")

    run(go())


def test_number_question_ranks_guesses_by_how_close_they_were():
    async def go():
        _, g, ps = setup([{"type": "number", "text": "Year?", "number": {"answer": 1969, "tolerance": 0, "closest": True, "spread": 100},
                           "speed_weight": 0}])
        await g.next()
        await g.submit(ps[0], 1969)
        await g.submit(ps[1], 1950)
        await g.submit(ps[2], "2500")
        await g.end_question()
        d = g._reveal()["detail"]
        assert [x["nickname"] for x in d["guesses"]] == ["P0", "P1", "P2"] and d["answer"] == 1969
        assert (ps[0].score, ps[1].score, ps[2].score) == (1000, 810, 0)
        assert g.view_player(ps[1], g._ranks())["result"]["partial"] is True

    run(go())


def test_order_question_shuffles_per_player_stably_and_never_leaks_the_answer():
    async def go():
        _, g, ps = setup([{"type": "order", "text": "Sort", "options": opts("a", "b", "c", "d", "e"), "speed_weight": 0}])
        await g.next()
        v0 = g.view_player(ps[0], g._ranks())["q"]["options"]
        assert v0 == g.view_player(ps[0], g._ranks())["q"]["options"]            # stable across pushes
        assert [o["id"] for o in v0] != ["o0", "o1", "o2", "o3", "o4"]
        await g.submit(ps[0], ["o0", "o1", "o2", "o3", "o4"])
        await g.submit(ps[1], ["o1", "o0", "o2", "o3", "o4"])
        assert ps[0].score == 1000 and 0 < ps[1].score < 1000

    run(go())


def test_match_and_pin_flow():
    async def go():
        _, g, ps = setup([
            {"type": "match", "text": "Match", "options": opts("a", "b", "c"), "targets": opts("1", "2", "3"), "speed_weight": 0},
            {"type": "pin", "text": "Where", "image": "/uploads/0123456789abcdef.png", "pin": {"x": 20, "y": 30, "r": 10, "ar": 1.5}, "speed_weight": 0}])
        await g.next()
        q = g.questions[0]
        right = [[o["id"], t] for o, t in zip(q["options"], q["correct"])]
        await g.submit(ps[0], right)
        await g.submit(ps[1], dict(right[:2] + [[right[2][0], right[0][1]]]))   # a one-to-many mapping is still graded pair by pair
        await g.submit(ps[2], [["nope", "nope"]])
        assert ps[0].score == 1000 and ps[2].score == 0
        await g.end_question()
        await g.next()
        await g.submit(ps[0], [22, 31])
        await g.submit(ps[1], [90, 90])
        await g.end_question()
        pts = g._reveal()["detail"]["points"]
        assert [p[2] for p in pts] == [True, False]
        assert ps[0].score == 2000

    run(go())


def test_poll_collects_votes_without_points_or_streaks_and_shows_live_to_the_screen():
    async def go():
        _, g, ps = setup([{"type": "mcq", "text": "Warm-up", "options": opts("a", "b"), "correct": ["o0"], "speed_weight": 0},
                          {"type": "poll", "text": "Fav?", "options": opts("a", "b", "c")},
                          {"type": "mcq", "text": "Next", "options": opts("a", "b"), "correct": ["o0"], "speed_weight": 0}])
        await g.next()
        await g.submit(ps[0], ["o0"])
        await g.end_question()
        assert ps[0].streak == 1
        await g.next()
        await g.submit(ps[0], ["o1"])
        await g.submit(ps[1], ["o1"])
        assert g.view_screen(g._ranks())["live_dist"] == {"o0": 0, "o1": 2, "o2": 0}
        await g.end_question()
        assert ps[0].score == 1000 and ps[0].streak == 1              # a poll neither adds to nor breaks the streak
        assert g._reveal()["distribution"]["o1"] == 2 and g._reveal()["correct"] == []
        data = game_results(g.db, g.id)
        poll = [a for a in data["analytics"] if a["type"] == "poll"][0]
        assert poll["pct_correct"] is None
        assert next(r for r in data["leaderboard"] if r["nickname"] == "P0")["answered"] == 1   # polls are not "answered" questions

    run(go())


def test_slides_are_not_questions_have_no_timer_and_can_show_standings():
    async def go():
        _, g, ps = setup([{"type": "slide", "text": "Welcome", "board": True},
                          {"type": "mcq", "text": "Q1", "options": opts("a", "b"), "correct": ["o0"], "speed_weight": 0},
                          {"type": "slide", "text": "Round 2"},
                          {"type": "mcq", "text": "Q2", "options": opts("a", "b"), "correct": ["o0"], "speed_weight": 0}])
        await g.next()
        assert g.phase == "slide" and g._timer is None
        v = g.view_player(ps[0], g._ranks())
        assert v["q"]["type"] == "slide" and v["q"]["n"] == 0 and v["q"]["of"] == 2 and "leaderboard" in v
        await g.submit(ps[0], ["o0"])                                 # nothing to answer on a slide
        assert not g.answers
        await g.next()
        assert g.phase == "question" and g.view_screen(g._ranks())["q"]["n"] == 1
        await g.submit(ps[0], ["o0"])
        await g.end_question()
        await g.next()
        assert g.phase == "slide" and "leaderboard" not in g.view_player(ps[0], g._ranks())
        await g.next()
        assert g.view_screen(g._ranks())["q"]["n"] == 2
        await g.end_question()
        await g.finish()
        assert [a["n"] for a in game_results(g.db, g.id)["analytics"]] == [1, 2]
        assert [r["n"] for r in player_detail(g.db, g.id, ps[0].id)["questions"]] == [1, 2]

    run(go())


def test_double_up_joker_is_once_per_game_and_costs_nothing_if_unused():
    async def go():
        qs = [{"type": "mcq", "text": f"Q{i}", "options": opts("a", "b"), "correct": ["o0"], "speed_weight": 0} for i in range(3)]
        db, g, ps = setup(qs, jokers=True)
        assert g.view_player(ps[0], g._ranks())["me"]["joker"] is True
        await g.next()
        await g.submit(ps[0], ["o0"], joker=True)
        await g.submit(ps[1], ["o0"])
        assert (ps[0].score, ps[1].score) == (2000, 1000)
        assert g.view_player(ps[0], g._ranks())["me"]["joker"] is False
        await g.end_question()
        await g.next()
        await g.submit(ps[0], ["o0"], joker=True)                     # already spent: a normal answer
        assert ps[0].score == 3000
        # the spent joker survives a restart
        g2 = GameManager(db)
        g2.restore()
        again = g2.get(g.code)
        assert again.players[ps[0].id].joker_qid == g.questions[0]["id"] and again.players[ps[1].id].joker_qid is None
        # a wasted joker on a wrong answer is still spent
        await g.end_question()
        await g.next()
        await g.submit(ps[1], ["o1"], joker=True)
        assert ps[1].score == 1000 and ps[1].joker_qid is not None

    run(go())


def test_jokers_do_nothing_unless_the_host_turned_them_on():
    async def go():
        _, g, ps = setup([{"type": "mcq", "text": "Q", "options": opts("a", "b"), "correct": ["o0"], "speed_weight": 0}])
        await g.next()
        await g.submit(ps[0], ["o0"], joker=True)
        assert ps[0].score == 1000 and g.view_player(ps[0], g._ranks())["me"]["joker"] is False

    run(go())


def test_skin_setting_accepts_only_known_skins():
    async def go():
        _, g, _ = setup([{"type": "slide", "text": "x"}], skin="ocean")
        assert g.view_screen(g._ranks())["skin"] == "ocean"
        await g.set_setting("skin", "hotpink")
        assert g.settings["skin"] == "ocean"
        await g.set_setting("skin", "royal")
        assert g.view_screen(g._ranks())["skin"] == "royal"

    run(go())


def test_player_report_describes_every_kind_of_answer():
    async def go():
        db, g, ps = setup([
            {"type": "text", "text": "Capital?", "answers": ["Paris"], "speed_weight": 0},
            {"type": "number", "text": "Year?", "number": {"answer": 1969}, "speed_weight": 0},
            {"type": "order", "text": "Sort", "options": opts("a", "b", "c"), "speed_weight": 0}])
        await g.next()
        await g.submit(ps[0], "Lyon"); await g.end_question(); await g.next()
        await g.submit(ps[0], 1969); await g.end_question(); await g.next()
        await g.submit(ps[0], ["o2", "o1", "o0"]); await g.end_question()
        d = player_detail(db, g.id, ps[0].id)
        t, n, o = d["questions"]
        assert t["yours"] == ["Lyon"] and t["expected"] == ["Paris"] and not t["correct"]
        assert n["yours"] == ["1969"] and n["correct"]
        assert o["yours"][0].startswith("1. c") and o["expected"][0].startswith("1. a")
        assert d["summary"]["correct"] == 1 and d["summary"]["asked"] == 3

    run(go())


def test_bots_can_play_every_type_without_crashing():
    async def go():
        from quickz.engine import Game
        qs = [validate_question(r) for r in [
            {"type": "text", "text": "x", "answers": ["a"]}, {"type": "number", "text": "x", "number": {"answer": 5}},
            {"type": "order", "text": "x", "options": opts("a", "b", "c")},
            {"type": "match", "text": "x", "options": opts("a", "b"), "targets": opts("1", "2")},
            {"type": "pin", "text": "x", "image": "/uploads/0123456789abcdef.png", "pin": {"x": 5, "y": 5}},
            {"type": "poll", "text": "x", "options": opts("a", "b")}, {"type": "mcq", "text": "x", "options": opts("a", "b"), "correct": ["o0"]}]]
        for q in qs:
            for _ in range(20):
                v = Game._bot_answer(q)
                assert grading.clean_answer(q, v) is not None, (q["type"], v)

    run(go())


# ------------------------------------------------------------------ API + websocket
def test_showcase_quiz_is_valid_and_can_be_added_by_a_host(client):
    from quickz.showcase import showcase_quiz
    quiz = validate_quiz(showcase_quiz())
    kinds = {q["type"] for q in quiz["questions"]}
    assert kinds == {"slide", "mcq", "multi", "text", "number", "order", "match", "pin", "jigsaw", "poll", "buzzer"}
    r = client.post("/api/quizzes/showcase")
    assert r.status_code == 200
    saved = client.get(f"/api/quizzes/{r.json()['id']}").json()
    assert len(saved["questions"]) == len(quiz["questions"])
    assert any(o["image"].startswith("/static/samples/") for q in saved["questions"] for o in q["options"])


def test_bundled_sample_art_exists_on_disk():
    from pathlib import Path
    from quickz.showcase import showcase_quiz
    root = Path(__file__).resolve().parents[1] / "quickz"
    urls = {q.get("image") for q in showcase_quiz()["questions"]} | {o["image"] for q in showcase_quiz()["questions"] for o in q.get("options", [])}
    for url in filter(None, urls):
        assert (root / url.lstrip("/")).is_file(), url


def test_picture_options_survive_the_quiz_api_round_trip(client):
    body = {"title": "Pics", "questions": [{"type": "mcq", "rich": True, "text": "<p>Which?</p>", "options": [
        {"id": "a", "text": "", "image": "/static/samples/flag-italy.svg"}, {"id": "b", "text": "B"}], "correct": ["a"], "category": "Flags", "image_effect": "none"}]}
    qid = client.post("/api/quizzes", json=body).json()["id"]
    got = client.get(f"/api/quizzes/{qid}").json()["questions"][0]
    assert got["options"][0]["image"] == "/static/samples/flag-italy.svg" and got["category"] == "Flags"
    bad = {**body, "questions": [{**body["questions"][0], "options": [{"id": "a", "text": "", "image": "http://evil/x.png"}, {"id": "b", "text": "B"}]}]}
    assert client.post("/api/quizzes", json=bad).status_code == 400


def test_typed_answer_over_websockets_end_to_end(client):
    qz = client.post("/api/quizzes", json={"title": "T", "questions": [{"type": "text", "rich": True, "text": "<p>Capital?</p>", "answers": ["Paris"], "speed_weight": 0}]}).json()["id"]
    code = client.post("/api/games", json={"quiz_id": qz, "settings": {"auto_reveal": False}}).json()["code"]
    tok = client.post("/api/join", json={"code": code, "nickname": "Sam"}).json()["token"]
    with client.websocket_connect(f"/ws/host/{code}") as host, client.websocket_connect(f"/ws/player/{code}?token={tok}") as ply:
        host.receive_json(); ply.receive_json()
        host.send_json({"t": "next"})
        host.receive_json(); v = ply.receive_json()["v"]
        assert v["q"]["type"] == "text" and v["q"]["options"] == [] and "answers" not in v["q"]
        ply.send_json({"t": "answer", "choice": "  PARIS "})
        host.send_json({"t": "end_question"})
        last = None
        for _ in range(6):
            m = ply.receive_json()
            if m["t"] == "state" and m["v"]["phase"] == "reveal":
                last = m["v"]
                break
        assert last and last["result"]["correct"] and last["me"]["score"] == 1000 and last["result"]["yours"] == ["PARIS"]

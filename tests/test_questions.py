import pytest

from quickz.questions import ValidationError, parse_text_quiz, validate_question, validate_quiz

SAMPLE = """# Geography

A short warm-up.

Q: Capital of France?
- Berlin
- Paris*
- Rome
time: 20
points: 500
? Paris since 987.

Q: Which are primes?
- 2*
- 4
- 7*

Q: Name the film from this quote
type: buzzer
"""


def test_parse_text_quiz():
    quiz = parse_text_quiz(SAMPLE)
    assert quiz["title"] == "Geography"
    assert quiz["description"] == "A short warm-up."
    q1, q2, q3 = quiz["questions"]
    assert q1["type"] == "mcq" and q1["time_limit"] == 20 and q1["points"] == 500
    assert [o["text"] for o in q1["options"]] == ["Berlin", "Paris", "Rome"]
    assert q1["correct"] == [q1["options"][1]["id"]]
    assert q1["explanation"] == "Paris since 987."
    assert q2["type"] == "multi" and len(q2["correct"]) == 2
    assert q3["type"] == "buzzer" and q3["options"] == [] and q3["correct"] == []


def test_parse_requires_questions():
    with pytest.raises(ValidationError):
        parse_text_quiz("# Empty")


def test_validate_question_rules():
    base = {"type": "mcq", "text": "Hi?", "options": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}], "correct": ["a"]}
    assert validate_question(base)["correct"] == ["a"]
    with pytest.raises(ValidationError):
        validate_question({**base, "correct": []})
    with pytest.raises(ValidationError):
        validate_question({**base, "correct": ["a", "b"]})  # mcq = exactly one
    with pytest.raises(ValidationError):
        validate_question({**base, "options": base["options"][:1]})
    with pytest.raises(ValidationError):
        validate_question({**base, "text": "  "})
    with pytest.raises(ValidationError):
        validate_question({**base, "type": "essay"})


def test_images_must_be_uploads():
    base = {"type": "buzzer", "text": "Q"}
    assert validate_question({**base, "image": "/uploads/a.png"})["image"] == "/uploads/a.png"
    for bad in ("http://evil.example/x.png", "javascript:alert(1)", "/uploads/../secret"):
        with pytest.raises(ValidationError):
            validate_question({**base, "image": bad})


def test_clamps_numbers_and_strips_control_chars():
    q = validate_question({"type": "buzzer", "text": "a\x00b", "time_limit": 99999, "points": -5})
    assert q["text"] == "ab" and q["time_limit"] == 300 and q["points"] == 0


def test_quiz_requires_title():
    with pytest.raises(ValidationError):
        validate_quiz({"title": " ", "questions": []})

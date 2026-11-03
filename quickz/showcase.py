"""The built-in "showcase" quiz: one of every question type, with bundled artwork, so a new host can see and try them all."""

S = "/static/samples/"


def _o(oid, text="", image=""):
    return {"id": oid, "text": text, "image": (S + image) if image else ""}


def showcase_quiz() -> dict:
    qs = [
        {"type": "slide", "category": "DiQuizBuzz showcase", "board": False,
         "text": "<p>Welcome! <b>Every kind of question</b>, in one quiz.</p><p>Grab your phone and play along.</p>"},
        {"type": "mcq", "category": "Pictures in the answers", "text": "<p>Which of these is the flag of <b>Italy</b>?</p>", "time_limit": 20, "speed_weight": 40,
         "options": [_o("fr", "France", "flag-france.svg"), _o("it", "Italy", "flag-italy.svg"), _o("ie", "Ireland", "flag-ireland.svg"), _o("de", "Germany", "flag-germany.svg")], "correct": ["it"],
         "explanation": "The Italian flag is green, white and red. Ireland's is green, white and orange."},
        {"type": "mcq", "category": "Pictures only", "text": "<p>Which shape has <b>six sides</b>?</p>", "time_limit": 20,
         "options": [_o("t", "", "shape-triangle.svg"), _o("p", "", "shape-pentagon.svg"), _o("h", "", "shape-hexagon.svg"), _o("o", "", "shape-octagon.svg")], "correct": ["h"],
         "explanation": "A hexagon has six sides: hex- is Greek for six."},
        {"type": "mcq", "category": "Science", "text": "<p>Which planet has the <b>most moons</b>?</p>", "time_limit": 20,
         "options": [_o("a", "Mercury"), _o("b", "Saturn"), _o("c", "Mars"), _o("d", "Venus")], "correct": ["b"], "explanation": "Saturn has well over a hundred known moons."},
        {"type": "mcq", "category": "Fun", "text": "<p>True or false: a group of flamingos is called a <b>flamboyance</b>.</p>", "time_limit": 15,
         "options": [_o("t", "True"), _o("f", "False")], "correct": ["t"]},
        {"type": "multi", "category": "Maths", "text": "<p>Which of these are <b>prime numbers</b>? Select all that apply.</p>", "time_limit": 25,
         "options": [_o("a", "2"), _o("b", "9"), _o("c", "7"), _o("d", "15")], "correct": ["a", "c"]},
        {"type": "text", "category": "Type the answer", "text": "<p>What is the <b>capital of France</b>?</p>", "time_limit": 20,
         "answers": ["Paris"], "explanation": "Capitals, accents and small typos are forgiven, so “paris” and “Pariss” both count."},
        {"type": "text", "category": "Guess the picture", "text": "<p>What is this? It sharpens as the clock runs down.</p>", "time_limit": 30, "speed_weight": 70,
         "image": S + "rocket.svg", "image_effect": "blur", "answers": ["Rocket", "Space rocket", "Spaceship", "Spacecraft"]},
        {"type": "number", "category": "Closest wins", "text": "<p>In what <b>year</b> did humans first land on the Moon?</p>", "time_limit": 25,
         "number": {"answer": 1969, "tolerance": 0, "closest": True, "spread": 30, "min": 1900, "max": 2020, "unit": ""}, "explanation": "Apollo 11, July 1969."},
        {"type": "order", "category": "Put in order", "text": "<p>Put these planets in order, <b>starting nearest the Sun</b>.</p>", "time_limit": 30,
         "options": [_o("me", "Mercury"), _o("ve", "Venus"), _o("ea", "Earth"), _o("ma", "Mars")]},
        {"type": "match", "category": "Match the pairs", "text": "<p>Match each animal to the sound it makes.</p>", "time_limit": 35,
         "options": [_o("l1", "\U0001F436 Dog"), _o("l2", "\U0001F431 Cat"), _o("l3", "\U0001F42E Cow"), _o("l4", "\U0001F411 Sheep")],
         "targets": [_o("r1", "Woof"), _o("r2", "Meow"), _o("r3", "Moo"), _o("r4", "Baa")]},
        {"type": "pin", "category": "Pin it", "text": "<p>Tap the <b>train station</b> on the map.</p>", "time_limit": 25,
         "image": S + "map.svg", "pin": {"x": 78.5, "y": 41.5, "r": 9, "ar": 720 / 450, "partial": True}, "explanation": "It is the dark building with the yellow stripes."},
        {"type": "jigsaw", "category": "Jigsaw race", "text": "<p>Put the <b>picture back together</b>. The first three to finish score the most.</p>", "time_limit": 90,
         "image": S + "map.svg", "jigsaw": {"rows": 3, "cols": 4, "hint": 200, "partial": False}, "winners": 3, "place_weights": [100, 60, 30],
         "explanation": "Marks follow the order people finish in: 100%, 60% and 30% of the points."},
        {"type": "poll", "category": "Poll", "text": "<p>Quick one, no points: <b>tea or coffee</b>?</p>", "time_limit": 15,
         "options": [_o("t", "☕ Coffee"), _o("c", "\U0001FAD6 Tea"), _o("n", "\U0001F964 Neither")]},
        {"type": "slide", "category": "Halfway", "board": True, "text": "<p>Time for the <b>standings</b></p>"},
        {"type": "buzzer", "category": "Buzzer", "text": "<p>Name the band that wrote <b>“Bohemian Rhapsody”</b>.</p>", "time_limit": 30, "points": 1500},
    ]
    return {"title": "DiQuizBuzz showcase: every question type",
            "description": "One of everything: pictures in the answers, typing, estimating, ordering, matching, pinning, a jigsaw race, a poll, slides and a buzzer.",
            "questions": [{**q, "rich": True} for q in qs]}

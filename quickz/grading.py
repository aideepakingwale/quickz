"""Per-type answer cleaning and grading.

A stored answer ("choice" in the database) is always JSON, whatever the question type:

    mcq / multi / poll   ["optionId", ...]
    text                 ["what the player typed"]
    number               [12.5]
    order                ["optionId", ...]           every item, in the player's order
    match                [["leftId", "rightId"], ...] in the order of the question's items
    pin                  [x, y]                       percent of the image's width / height
    jigsaw               [pieceIndex, ...]            what sits in each slot, left to right, top to bottom (0..n-1; solved = 0,1,2...)

`clean_answer` validates what a phone sent and returns that canonical shape (or None if it is not a possible
answer for this question); `grade` turns it into (fully_correct, fraction_of_points_earned).
Partial credit never makes a wrong answer "correct": it only scales the points.
"""

import math
import re
import unicodedata

from .questions import strip_tags

SCORED_TYPES = ("mcq", "multi", "text", "number", "order", "match", "pin", "jigsaw")   # have a right answer and earn points
ANSWERED_TYPES = SCORED_TYPES + ("poll",)                                    # players can send an answer
CHOICE_TYPES = ("mcq", "multi", "poll")

_ARTICLES = ("the ", "a ", "an ")


# ---------------------------------------------------------------- text answers
def normalize_text(value) -> str:
    """Case, accents, punctuation, leading articles and spacing never decide a type-in answer."""
    s = unicodedata.normalize("NFKD", str(value or "")).casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for a in _ARTICLES:
        if s.startswith(a) and len(s) > len(a) + 1:
            s = s[len(a):]
            break
    return s


def _distance_within(a: str, b: str, limit: int) -> bool:
    """True when the Levenshtein distance of a and b is <= limit (bounded, so it is cheap)."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def typo_allowance(accepted: str) -> int:
    n = len(accepted.replace(" ", ""))
    return 0 if n < 5 else 1 if n < 9 else 2


def text_matches(q: dict, given: str) -> bool:
    key = normalize_text(given)
    if not key:
        return False
    if key in {normalize_text(r) for r in q.get("rejected") or []}:
        return False
    squashed = key.replace(" ", "")
    for ans in q.get("answers") or []:
        want = normalize_text(ans)
        if not want:
            continue
        if key == want or squashed == want.replace(" ", ""):
            return True
        if q.get("fuzzy", True):
            allow = typo_allowance(want)
            if allow and _distance_within(squashed, want.replace(" ", ""), allow):
                return True
    return False


# ---------------------------------------------------------------- numbers / pins
def number_spec(q: dict) -> dict:
    return q.get("number") or {}


def number_error(q: dict, value: float) -> float:
    return abs(value - number_spec(q).get("answer", 0.0))


def number_fraction(q: dict, value: float) -> tuple[bool, float]:
    spec = number_spec(q)
    err = abs(value - spec.get("answer", 0.0))
    tol = spec.get("tolerance", 0.0)
    if err <= tol + 1e-9:
        return True, 1.0
    if spec.get("closest"):
        spread = max(spec.get("spread", 1.0), 1e-9)
        return False, max(0.0, 1.0 - (err - tol) / spread)
    return False, 0.0


def pin_distance(q: dict, x: float, y: float) -> float:
    """Distance from the target in percent of the image WIDTH (so the target is a true circle on any aspect ratio)."""
    p = q.get("pin") or {}
    ar = p.get("ar") or 1.5
    return math.hypot(x - p.get("x", 50.0), (y - p.get("y", 50.0)) / ar)


def pin_fraction(q: dict, x: float, y: float) -> tuple[bool, float]:
    p = q.get("pin") or {}
    r = max(p.get("r", 8.0), 0.5)
    d = pin_distance(q, x, y)
    if d <= r:
        return True, 1.0
    if p.get("partial", True):
        return False, max(0.0, 1.0 - (d - r) / (2.5 * r))     # fades to nothing at 3.5 radii
    return False, 0.0


# ---------------------------------------------------------------- cleaning + grading
def _finite(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def clean_answer(q: dict, raw):
    """The canonical stored answer for what a phone sent, or None when it is not a valid answer."""
    t = q["type"]
    if t in CHOICE_TYPES:
        items = raw if isinstance(raw, list) else [raw]
        valid = {o["id"] for o in q["options"]}
        picked = [c for c in dict.fromkeys(items) if isinstance(c, str) and c in valid]
        if not picked or (t != "multi" and len(picked) != 1):
            return None
        return picked
    if t == "text":
        s = raw[0] if isinstance(raw, list) and raw else raw
        s = re.sub(r"\s+", " ", "".join(ch for ch in str(s or "") if ch.isprintable())).strip()[:120]
        return [s] if s else None
    if t == "number":
        v = _finite(raw[0] if isinstance(raw, list) and raw else raw)
        if v is None or abs(v) > 1e12:
            return None
        spec = number_spec(q)
        if spec.get("min") is not None and spec.get("max") is not None:
            v = min(max(v, spec["min"]), spec["max"])
        return [round(v, 6)]
    if t == "order":
        ids = [o["id"] for o in q["options"]]
        if not isinstance(raw, list) or len(raw) != len(ids) or set(raw) != set(ids) or len(set(raw)) != len(raw):
            return None
        return list(raw)
    if t == "match":
        lefts = [o["id"] for o in q["options"]]
        rights = {o["id"] for o in q.get("targets") or []}
        pairs = {}
        src = raw.items() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        for item in src:
            try:
                l, r = item
            except (TypeError, ValueError):
                return None
            if l in lefts and r in rights and isinstance(l, str) and isinstance(r, str):
                pairs[l] = r
        if len(pairs) != len(lefts):
            return None
        return [[l, pairs[l]] for l in lefts]
    if t == "pin":
        xy = raw if isinstance(raw, list) else [raw.get("x"), raw.get("y")] if isinstance(raw, dict) else None
        if not xy or len(xy) != 2:
            return None
        x, y = _finite(xy[0]), _finite(xy[1])
        if x is None or y is None:
            return None
        return [round(min(max(x, 0.0), 100.0), 2), round(min(max(y, 0.0), 100.0), 2)]
    if t == "jigsaw":
        n = jigsaw_pieces(q)
        if not isinstance(raw, list) or len(raw) != n:
            return None
        if any(isinstance(v, bool) or not isinstance(v, int) for v in raw) or sorted(raw) != list(range(n)):
            return None                                      # every piece exactly once
        return list(raw)
    return None


def jigsaw_pieces(q: dict) -> int:
    j = q.get("jigsaw") or {}
    return int(j.get("rows", 3)) * int(j.get("cols", 3))


def jigsaw_right(value) -> int:
    """Pieces sitting in their own slot."""
    return sum(1 for slot, piece in enumerate(value) if slot == piece)


def grade(q: dict, value) -> tuple[bool, float]:
    """(fully correct?, share of the question's points earned 0..1). A poll is never graded."""
    t = q["type"]
    if t == "mcq" or t == "multi":
        ok = set(value) == set(q["correct"])
        return ok, 1.0 if ok else 0.0
    if t == "text":
        ok = text_matches(q, value[0])
        return ok, 1.0 if ok else 0.0
    if t == "number":
        return number_fraction(q, value[0])
    if t == "order":
        right = sum(1 for a, b in zip(value, q["correct"]) if a == b)
        n = len(q["correct"])
        if right == n:
            return True, 1.0
        if q.get("partial", True) and n > 1:
            return False, max(0.0, (right - 1) / (n - 1)) * 0.75     # a lucky single match earns nothing
        return False, 0.0
    if t == "match":
        want = dict(zip((o["id"] for o in q["options"]), q["correct"]))
        right = sum(1 for l, r in value if want.get(l) == r)
        n = len(want)
        if right == n:
            return True, 1.0
        if q.get("partial", True):
            return False, (right / n) * 0.75
        return False, 0.0
    if t == "pin":
        return pin_fraction(q, value[0], value[1])
    if t == "jigsaw":
        right, n = jigsaw_right(value), len(value)
        if right == n:
            return True, 1.0
        if (q.get("jigsaw") or {}).get("partial") and n:
            return False, (right / n) * 0.5                  # unfinished: at most half marks, and never "correct"
        return False, 0.0
    return False, 0.0


# ---------------------------------------------------------------- describing answers (reports, phones)
def option_label(o: dict) -> str:
    return strip_tags(o.get("text", "")) or ("Picture" if o.get("image") else "")


def describe(q: dict, value) -> list[str]:
    """Human-readable lines for what a player answered (plain text, safe to show anywhere)."""
    t = q["type"]
    by_id = {o["id"]: o for o in q["options"]}
    if t in CHOICE_TYPES:
        return [option_label(by_id[c]) for c in value if c in by_id]
    if t == "text":
        return [str(value[0])]
    if t == "number":
        return [fmt_number(value[0], q)]
    if t == "order":
        return [f"{i}. {option_label(by_id[c])}" for i, c in enumerate(value, 1) if c in by_id]
    if t == "match":
        targets = {o["id"]: o for o in q.get("targets") or []}
        return [f"{option_label(by_id[l])} → {option_label(targets[r])}" for l, r in value if l in by_id and r in targets]
    if t == "pin":
        return [f"Placed at {value[0]:.0f}% across, {value[1]:.0f}% down"]
    if t == "jigsaw":
        right = jigsaw_right(value)
        return ["Puzzle solved" if right == len(value) else f"{right} of {len(value)} pieces in the right place"]
    return []


def expected(q: dict) -> list[str]:
    t = q["type"]
    by_id = {o["id"]: o for o in q["options"]}
    if t in ("mcq", "multi"):
        return [option_label(by_id[c]) for c in q["correct"] if c in by_id]
    if t == "text":
        return list(q.get("answers") or [])
    if t == "number":
        spec = number_spec(q)
        tol = spec.get("tolerance", 0.0)
        return [fmt_number(spec.get("answer", 0.0), q) + (f" (± {fmt_plain(tol)})" if tol else "")]
    if t == "order":
        return [f"{i}. {option_label(by_id[c])}" for i, c in enumerate(q["correct"], 1) if c in by_id]
    if t == "match":
        targets = {o["id"]: o for o in q.get("targets") or []}
        return [f"{option_label(o)} → {option_label(targets[r])}" for o, r in zip(q["options"], q["correct"]) if r in targets]
    if t == "pin":
        return ["The marked spot on the picture"]
    if t == "jigsaw":
        return ["The finished picture"]
    return []


def fmt_plain(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def fmt_number(v: float, q: dict) -> str:
    unit = number_spec(q).get("unit") or ""
    return fmt_plain(v) + (f" {unit}" if unit else "")

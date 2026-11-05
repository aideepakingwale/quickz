"""Validation of quizzes/questions, rich-text sanitising, and the plain-text import format.

Question and answer text is stored as a small, *sanitised* HTML subset (what the WYSIWYG editor produces).
Nothing else is ever stored or sent to players' devices: unknown tags are dropped, attributes are
whitelisted, and colours/alignment are validated.
"""

import html
import re
import secrets
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

QUESTION_TYPES = ("mcq", "multi", "buzzer", "text", "number", "order", "match", "pin", "jigsaw", "poll", "slide")
CHOICE_TYPES = ("mcq", "multi", "poll")          # pick from the options
LISTED_TYPES = CHOICE_TYPES + ("order",)         # 2-8 options, text and/or a picture each
NO_POINTS_TYPES = ("poll", "slide")              # nothing to win
RACE_TYPES = ("mcq", "multi", "text", "number", "order", "match", "pin", "jigsaw")   # "first correct answer wins" makes sense
JIGSAW_MAX_PIECES = 48                         # a phone screen cannot hold more pieces than a thumb can pick up
IMAGE_EFFECTS = ("none", "blur", "zoom")
SAMPLE_PREFIX = "/static/samples/"
# Pictures and GIFs may also be linked from these well-known hosts (and their subdomains). A host can add more with
# QUICKZ_IMAGE_HOSTS. Anything else must be uploaded, so a quiz can never point players' phones at an unknown server.
IMAGE_HOSTS = {"giphy.com", "tenor.com", "wikimedia.org", "imgur.com", "unsplash.com", "pexels.com", "pixabay.com", "staticflickr.com"}
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIMEO_ID = re.compile(r"^\d{5,12}$")
MEDIA_KINDS = ("audio", "video", "embed")
MEDIA_WHERE = ("screen", "players", "both")
MAX_QUESTIONS = 200                      # the Super Admin can change this on the Settings page
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
MUSIC_EXT = {".mp3", ".m4a", ".ogg", ".wav"}                 # lobby music: only these
VIDEO_EXT = {".mp4", ".webm"}

INLINE_TAGS = {"b", "strong", "i", "em", "u", "s", "strike", "sub", "sup", "code", "span"}
BLOCK_TAGS = {"p", "div", "ul", "ol", "li"}
VOID_TAGS = {"br"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math", "textarea", "title"}
CANON = {"strong": "b", "em": "i", "strike": "s", "div": "p"}
COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d+)\s*)?\))$")
MAX_TEXT_PLAIN = 1000
MAX_HTML = 6000
MAX_OPTION_PLAIN = 200


class ValidationError(ValueError):
    pass


def new_id() -> str:
    return secrets.token_hex(4)


# ---------------------------------------------------------------- rich text
class _Sanitizer(HTMLParser):
    """Allow-list sanitiser. `stack` remembers EVERY open tag (with the tag we actually emitted for it, if any), so a
    closing tag always closes the right thing even when a wrapper (bare <span>, <a>, <font> without a valid colour)
    was dropped."""

    def __init__(self, inline: bool):
        super().__init__(convert_charrefs=True)
        self.inline = inline
        self.out: list[str] = []
        self.stack: list[tuple[str, str | None]] = []
        self.skip = 0

    @staticmethod
    def _style(attrs, allowed: set[str]) -> str:
        raw = dict(attrs).get("style") or ""
        keep = []
        for part in raw.split(";"):
            if ":" not in part:
                continue
            k, v = (x.strip().lower() for x in part.split(":", 1))
            if k in allowed:
                if k in ("color", "background-color") and COLOR_RE.match(v):
                    keep.append(f"{k}:{v}")
                elif k == "text-align" and v in ("left", "center", "right"):
                    keep.append(f"{k}:{v}")
        return ";".join(keep)

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in VOID_TAGS:
            self.out.append(" " if self.inline else "<br>")
            return
        canon = CANON.get(tag, tag)
        emit: tuple[str, str] | None = None
        if tag == "font":                                     # what browsers produce for a caret colour
            c = (dict(attrs).get("color") or "").strip().lower()
            if COLOR_RE.match(c):
                emit = ("span", f"color:{c}")
        elif canon in BLOCK_TAGS:
            if self.inline:
                self.out.append(" ")
            else:
                emit = (canon, self._style(attrs, {"text-align"}) if canon == "p" else "")
        elif canon in INLINE_TAGS:
            if canon == "span":
                style = self._style(attrs, {"color"})
                if style:
                    emit = ("span", style)
            else:
                emit = (canon, "")
        if emit:
            name, style = emit
            self.out.append(f"<{name}" + (f' style="{style}"' if style else "") + ">")
        self.stack.append((tag, emit[0] if emit else None))

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID_TAGS:
            return
        idx = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if idx is None:
            return                                           # stray closing tag
        while len(self.stack) > idx:                         # close anything opened after it, then it
            _, out = self.stack.pop()
            if out:
                self.out.append(f"</{out}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def close(self):
        super().close()
        while self.stack:
            _, out = self.stack.pop()
            if out:
                self.out.append(f"</{out}>")


def sanitize_html(raw, inline: bool = False) -> str:
    s = _Sanitizer(inline)
    s.feed("".join(ch for ch in str(raw or "") if ch >= " " or ch in "\n\t"))
    s.close()
    out = "".join(s.out)
    out = re.sub(r"(<br>\s*){3,}", "<br><br>", out)
    out = re.sub(r"<p>\s*(<br>)?\s*</p>", "", out) if not inline else out
    out = re.sub(r"<p>(\s*<(ul|ol)>.*?</\2>\s*)</p>", r"\1", out, flags=re.S)    # browsers wrap new lists in a <p>
    return re.sub(r"\s+", " ", out).strip() if inline else out.strip()


def strip_tags(value) -> str:
    """Plain text of a rich-text string (for outlines, analytics and length limits)."""
    parts: list[str] = []

    class _T(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag in ("br", "p", "div", "li", "ul", "ol"):
                parts.append(" ")

        def handle_data(self, data):
            parts.append(data)

    t = _T(convert_charrefs=True)
    t.feed(str(value or ""))
    t.close()
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def md_to_html(text) -> str:
    """Legacy/import format: **bold**, *italic*, `code` and newlines -> sanitised HTML."""
    s = html.escape(str(text or ""), quote=False)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(^|[^*])\*([^*\n]+)\*", r"\1<i>\2</i>", s)
    return s.replace("\n", "<br>")


def upgrade_question(q: dict) -> dict:
    """In-place: questions stored before the WYSIWYG editor hold markdown-lite text; convert once."""
    if q.get("rich"):
        return q
    q["text"] = sanitize_html(md_to_html(q.get("text", "")))
    for o in q.get("options") or []:
        o["text"] = sanitize_html(md_to_html(o.get("text", "")), inline=True)
    q["rich"] = True
    return q


def _clean(text, limit: int) -> str:
    text = "" if text is None else str(text)
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")
    return text.strip()[:limit]


def _int(value, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _image_host_ok(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS)


def _upload_url(url, what: str) -> str:
    url = _clean(url, 500)
    if not url:
        return url
    if url.lower().startswith("https://"):                    # a picture linked from an allowed host
        u = urlparse(url)
        if _image_host_ok(u.hostname) and not any(c.isspace() for c in url) and "\\" not in url and not u.username:
            return url
        raise ValidationError(f"{what} can be linked from: {', '.join(sorted(IMAGE_HOSTS))}. Otherwise upload the file")
    url = url[:200]
    clean = ".." not in url and "\\" not in url and "?" not in url and "#" not in url
    sample = url.startswith(SAMPLE_PREFIX) and url.rsplit(".", 1)[-1].lower() in ("svg", "png", "jpg", "jpeg", "webp")
    if not (clean and (url.startswith("/uploads/") or sample)):
        raise ValidationError(f"{what} must be uploaded through the host console")
    return url


def _music_url(url) -> str:
    """Lobby music has to be an audio file uploaded through the host console (never a link to somewhere else)."""
    url = _clean(url, 200)
    if not url:
        return ""
    if not url.startswith("/uploads/") or ".." in url or "\\" in url or "?" in url or "#" in url:
        raise ValidationError("Music must be uploaded through the host console")
    if "." + url.rsplit(".", 1)[-1].lower() not in MUSIC_EXT:
        raise ValidationError("Music must be an MP3, M4A, OGG or WAV file")
    return url


def _seconds(value) -> int:
    """'90', '1m30s', '1h2m3s' or '1:30' -> seconds (0 when absent or unreadable)."""
    v = str(value or "").strip().lower()
    if not v:
        return 0
    if v.isdigit():
        return int(v)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", v)
    if m and any(m.groups()):
        h, mi, se = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + se
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", v):
        n = 0
        for part in v.split(":"):
            n = n * 60 + int(part)
        return n
    return 0


def parse_embed_link(link) -> dict:
    """A pasted YouTube or Vimeo address -> {provider, id, hash, start, link}. Only these two providers are ever embedded,
    and only by their validated ID: the quiz never stores or renders an arbitrary address."""
    link = _clean(link, 300)
    if not link:
        raise ValidationError("Paste a YouTube or Vimeo link")
    if "://" not in link:
        link = "https://" + link
    u = urlparse(link)
    host = (u.hostname or "").lower()
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    qs = parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    start = _seconds((qs.get("t") or qs.get("start") or [""])[0] or (u.fragment[2:] if u.fragment.startswith("t=") else ""))
    if u.scheme not in ("http", "https"):
        raise ValidationError("That is not a web link")
    if host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com", "youtu.be"):
        vid = None
        if host == "youtu.be":
            vid = parts[0] if parts else None
        elif parts[:1] == ["watch"]:
            vid = (qs.get("v") or [None])[0]
        elif parts[:1] in (["embed"], ["shorts"], ["live"], ["v"]) and len(parts) > 1:
            vid = parts[1]
        if not vid or not YT_ID.match(vid):
            raise ValidationError("Could not find a video in that YouTube link")
        return {"provider": "youtube", "id": vid, "hash": "", "start": start, "link": f"https://www.youtube.com/watch?v={vid}"}
    if host in ("vimeo.com", "player.vimeo.com"):
        nums = [p for p in parts if p.isdigit()]
        vid = nums[0] if nums else None
        if not vid or not VIMEO_ID.match(vid):
            raise ValidationError("Could not find a video in that Vimeo link")
        after = parts[parts.index(vid) + 1] if parts.index(vid) + 1 < len(parts) else ""
        h = (qs.get("h") or [after])[0]
        h = h if re.fullmatch(r"[A-Za-z0-9]{6,20}", h or "") else ""    # unlisted videos carry a private hash
        return {"provider": "vimeo", "id": vid, "hash": h, "start": start, "link": f"https://vimeo.com/{vid}" + (f"/{h}" if h else "")}
    raise ValidationError("Only YouTube and Vimeo links can be embedded")


def _validate_media(raw, qtype: str):
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValidationError("Invalid media")
    kind = raw.get("kind")
    if kind not in MEDIA_KINDS:
        raise ValidationError("Media must be audio, video or a YouTube/Vimeo link")
    where = raw.get("where", "screen")
    if where not in MEDIA_WHERE:
        raise ValidationError("Choose where the media plays")
    # buzzer questions are answered while the clip plays (name that tune), so the timer never waits for them
    timer_after = bool(raw.get("timer_after")) and qtype not in ("buzzer", "slide")
    if kind == "embed":
        info = parse_embed_link(raw.get("link") or raw.get("url"))
        start = _int(raw.get("start"), 0, 86400, info["start"]) if raw.get("start") not in (None, "") else info["start"]
        end = _int(raw.get("end"), 0, 86400, 0)
        if end and end <= start:
            raise ValidationError("The clip must stop after it starts")
        return {"kind": "embed", "provider": info["provider"], "id": info["id"], "hash": info["hash"], "link": info["link"], "url": info["link"],
                "start": start, "end": end, "cover": bool(raw.get("cover")), "where": where, "timer_after": timer_after}
    url = _upload_url(raw.get("url"), "Media")
    if not url or url.startswith("https://"):
        if url:
            raise ValidationError("Audio and video must be uploaded; to use a web video, paste a YouTube or Vimeo link")
        return None
    ext = "." + url.rsplit(".", 1)[-1].lower() if "." in url else ""
    if ext not in (AUDIO_EXT if kind == "audio" else VIDEO_EXT) and ext not in (AUDIO_EXT | VIDEO_EXT):
        raise ValidationError("Unsupported media file type")
    return {"kind": kind, "url": url, "where": where, "timer_after": timer_after}


def _float(value, lo: float, hi: float, default=None):
    if value is None or value == "":
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValidationError("Numbers must be numeric") from None
    if f != f or f in (float("inf"), float("-inf")):
        raise ValidationError("Numbers must be finite")
    return max(lo, min(hi, f))


def _items(raw_list, rich: bool, lo: int, hi: int, what: str) -> list[dict]:
    """Options / pairs: each has text and/or a picture, a stable id, and is cleaned like any rich text."""
    raw_list = raw_list or []
    if not lo <= len(raw_list) <= hi:
        raise ValidationError(f"Provide between {lo} and {hi} {what}" if lo != hi else f"Provide exactly {lo} {what}")
    out, seen = [], set()
    for o in raw_list:
        oid = _clean(o.get("id"), 16) if isinstance(o, dict) else ""
        raw_text = o.get("text") if isinstance(o, dict) else o
        otext = sanitize_html(raw_text if rich else md_to_html(raw_text), inline=True)
        oplain = strip_tags(otext)
        oimg = _upload_url(o.get("image"), "Images") if isinstance(o, dict) else ""
        if not oplain and not oimg:
            raise ValidationError(f"Each of the {what} needs some text or a picture")
        if len(oplain) > MAX_OPTION_PLAIN:
            raise ValidationError(f"Text is limited to {MAX_OPTION_PLAIN} characters")
        if not oid or oid in seen:
            oid = new_id()
        seen.add(oid)
        out.append({"id": oid, "text": otext, "image": oimg})
    return out


def _validate_number(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    answer = _float(raw.get("answer"), -1e12, 1e12)
    if answer is None:
        raise ValidationError("Enter the correct number")
    tol = _float(raw.get("tolerance"), 0, 1e12, 0.0)
    lo, hi = _float(raw.get("min"), -1e12, 1e12), _float(raw.get("max"), -1e12, 1e12)
    if (lo is None) != (hi is None):
        raise ValidationError("Give both a lowest and a highest value for the slider, or neither")
    if lo is not None and not lo < hi:
        raise ValidationError("The slider's lowest value must be below its highest")
    if lo is not None and not lo <= answer <= hi:
        raise ValidationError("The correct number must be within the slider range")
    spread = _float(raw.get("spread"), 1e-9, 1e12) or max(abs(answer) * 0.1, 1.0)
    return {"answer": answer, "tolerance": tol, "closest": bool(raw.get("closest")), "spread": spread,
            "min": lo, "max": hi, "unit": _clean(raw.get("unit"), 12)}


def _validate_pin(raw, image: str) -> dict:
    if not image:
        raise ValidationError("A pin question needs a picture to point at")
    raw = raw if isinstance(raw, dict) else {}
    x, y = _float(raw.get("x"), 0, 100), _float(raw.get("y"), 0, 100)
    if x is None or y is None:
        raise ValidationError("Click the picture to mark the right spot")
    return {"x": x, "y": y, "r": _float(raw.get("r"), 1, 50, 8.0), "ar": _float(raw.get("ar"), 0.2, 5, 1.5),
            "partial": raw.get("partial", True) is not False}


def _validate_jigsaw(raw, image: str) -> dict:
    if not image:
        raise ValidationError("A jigsaw needs a picture to cut up")
    raw = raw if isinstance(raw, dict) else {}
    rows, cols = _int(raw.get("rows"), 2, 8, 3), _int(raw.get("cols"), 2, 8, 3)
    if rows * cols > JIGSAW_MAX_PIECES:
        raise ValidationError(f"At most {JIGSAW_MAX_PIECES} pieces ({rows} x {cols} is {rows * cols})")
    return {"rows": rows, "cols": cols,
            "hint": _int(raw.get("hint"), 0, 10000, 0),                # marks a player pays to peek at the finished picture; 0 = no hint button
            "partial": bool(raw.get("partial", False))}                # points for pieces in the right place even if unfinished


def _weights(raw) -> list[int]:
    """Marks for 1st, 2nd, 3rd... correct answer, as percentages of the question's points (e.g. 100, 70, 40)."""
    if isinstance(raw, str):
        raw = re.split(r"[,\s;/]+", raw.strip())
    out = []
    for w in raw if isinstance(raw, list) else []:
        try:
            out.append(max(0, min(100, int(round(float(str(w).strip().rstrip("%")))))))
        except ValueError:
            raise ValidationError("Marks for each place must be numbers like 100, 70, 40") from None
    if len(out) > 50:
        raise ValidationError("At most 50 places")
    return out


def _validate_answers(raw) -> list[str]:
    seen, out = set(), []
    for a in raw if isinstance(raw, list) else []:
        a = re.sub(r"\s+", " ", _clean(a, 100))
        if a and a.casefold() not in seen:
            seen.add(a.casefold())
            out.append(a)
    if not out:
        raise ValidationError("Add at least one accepted answer")
    if len(out) > 20:
        raise ValidationError("At most 20 accepted answers")
    return out


def validate_question(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("Question must be an object")
    qtype = raw.get("type", "mcq")
    if qtype not in QUESTION_TYPES:
        raise ValidationError(f"Unknown question type: {qtype}")
    rich = bool(raw.get("rich"))
    text = sanitize_html(raw.get("text") if rich else md_to_html(raw.get("text")))
    plain = strip_tags(text)
    if not plain:
        raise ValidationError("Question text is required" if qtype != "slide" else "A slide needs some text")
    if len(plain) > MAX_TEXT_PLAIN or len(text) > MAX_HTML:
        raise ValidationError(f"Question text is too long (max {MAX_TEXT_PLAIN} characters)")
    image = _upload_url(raw.get("image"), "Images")

    q = {
        "id": _clean(raw.get("id"), 16) or new_id(),
        "type": qtype,
        "rich": True,
        "text": text,
        "image": image,
        "image_effect": raw.get("image_effect") if image and raw.get("image_effect") in IMAGE_EFFECTS else "none",
        "media": _validate_media(raw.get("media"), qtype),
        "category": _clean(raw.get("category"), 30),
        "options": [], "correct": [],
        "time_limit": _int(raw.get("time_limit"), 5, 300, 30),
        "points": _int(raw.get("points"), 0, 10000, 1000),
        # share of the points that depends on answering quickly: 0 = flat, 100 = a last-second answer earns nothing
        "speed_weight": _int(raw.get("speed_weight"), 0, 100, 50),
        # how many correct answers can score: 0 = everyone, 1 = only the first (they get 100%), N = the first N.
        # Buzzer questions are always "first player wins" and polls/slides have nothing to win.
        "winners": _int(raw.get("winners"), 0, 50, 0) if qtype in RACE_TYPES else 0,
        # marks by finishing order: the 1st correct answer earns weights[0]% of the points, the 2nd weights[1]%...
        # (past the end of the list the last value applies). Empty = the usual speed-weighted points.
        "place_weights": _weights(raw.get("place_weights")) if qtype in RACE_TYPES else [],
        "explanation": _clean(raw.get("explanation"), 500),
        "explain_image": _upload_url(raw.get("explain_image"), "Images"),
    }
    if qtype in NO_POINTS_TYPES:
        q["points"], q["speed_weight"] = 0, 0

    if qtype in LISTED_TYPES:
        q["options"] = _items(raw.get("options"), rich, 2, 8, "options")
        ids = [o["id"] for o in q["options"]]
        if qtype == "order":
            q["correct"] = ids                                    # the editor lists them in the right order
            q["partial"] = raw.get("partial", True) is not False
        elif qtype == "poll":
            q["correct"] = []
        else:
            correct = list(dict.fromkeys(c for c in (raw.get("correct") or []) if c in ids))
            if not correct:
                raise ValidationError("Mark at least one correct option")
            if qtype == "mcq" and len(correct) != 1:
                raise ValidationError("Single-choice questions need exactly one correct option")
            q["correct"] = correct
    elif qtype == "text":
        q["answers"] = _validate_answers(raw.get("answers"))
        q["fuzzy"] = raw.get("fuzzy", True) is not False
    elif qtype == "number":
        q["number"] = _validate_number(raw.get("number"))
    elif qtype == "match":
        q["options"] = _items(raw.get("options"), rich, 2, 6, "pairs")
        q["targets"] = _items(raw.get("targets"), rich, len(q["options"]), len(q["options"]), "matching answers")
        q["correct"] = [t["id"] for t in q["targets"]]            # pair i = options[i] <-> targets[i]
        q["partial"] = raw.get("partial", True) is not False
    elif qtype == "pin":
        q["pin"] = _validate_pin(raw.get("pin"), image)
    elif qtype == "jigsaw":
        q["jigsaw"] = _validate_jigsaw(raw.get("jigsaw"), image)
    elif qtype == "slide":
        q["board"] = bool(raw.get("board"))
    return q


def validate_quiz(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("Quiz must be an object")
    title = _clean(raw.get("title"), 120)
    if not title:
        raise ValidationError("Quiz title is required")
    questions = raw.get("questions") or []
    if len(questions) > MAX_QUESTIONS:
        raise ValidationError(f"A quiz can have at most {MAX_QUESTIONS} questions")
    cleaned = []
    for i, q in enumerate(questions, 1):
        try:
            cleaned.append(validate_question(q))
        except ValidationError as e:
            raise ValidationError(f"Question {i}: {e}") from None
    return {
        "title": title,
        "description": _clean(raw.get("description"), 1000),
        "cover": _upload_url(raw.get("cover"), "Images"),
        "screen_bg": _upload_url(raw.get("screen_bg"), "Images"),
        "screen_bg_wash": _int(raw.get("screen_bg_wash"), 0, 85, 40),
        "lobby_music": _music_url(raw.get("lobby_music")),
        "questions": cleaned,
    }


_KEYS = ("time", "points", "type", "image", "speed", "winners", "category", "effect", "answer", "tolerance", "closest", "unit",
         "spread", "min", "max", "fuzzy", "board", "grid", "places", "preview", "hint_cost", "partial")
_KEYVAL = re.compile(r"^(" + "|".join(_KEYS) + r")\s*:\s*(.+)$", re.I)
_YES = ("yes", "true", "1", "on", "y")
_FIELD = {"time": "time_limit", "speed": "speed_weight", "effect": "image_effect"}
_NUMBER_KEYS = ("answer", "tolerance", "closest", "unit", "spread", "min", "max")


def parse_text_quiz(text: str) -> dict:
    """Parse the plain-text format documented in README.md ("Writing questions").

        Q: Capital of France?          a choice question: put * after the correct option(s)
        - Berlin
        - Paris*
        type: text / number / order / match / poll / slide / buzzer / tf
        = Paris                        an accepted answer for a "type: text" question
        - Left side = Right side       a pair, for "type: match"
        answer: 1969                   (number) plus tolerance / closest / unit / min / max
    """
    title, desc_lines, questions, cur = "", [], [], None

    def flush():
        nonlocal cur
        if cur is None:
            return
        opts = cur["options"]
        starred = [o["id"] for o in opts if o.pop("_ok")]
        qtype = (cur.get("type") or "").lower() or ("multi" if len(starred) > 1 else "mcq")
        if qtype == "tf":
            qtype = "mcq"
        cur["type"] = qtype
        if qtype in ("mcq", "multi"):
            cur["correct"] = starred
        elif qtype == "text":
            cur["answers"] = cur.pop("answers", None) or [re.sub(r"<[^>]+>", "", o["text"]) for o in opts if o["id"] in starred]
            cur["options"] = []
            cur["fuzzy"] = str(cur.get("fuzzy", "yes")).lower() in _YES
        elif qtype == "number":
            cur["number"] = {k: cur.pop(k) for k in _NUMBER_KEYS if k in cur}
            cur["number"]["closest"] = str(cur["number"].get("closest", "")).lower() in _YES
            cur["options"] = []
        elif qtype == "match":
            pairs = [o for o in opts if "_right" in o]
            cur["options"] = [{"id": o["id"], "text": o["text"]} for o in pairs]
            cur["targets"] = [{"id": new_id(), "text": o["_right"]} for o in pairs]
        elif qtype == "slide":
            cur["options"] = []
            cur["board"] = str(cur.get("board", "")).lower() in _YES
        elif qtype in ("buzzer", "pin"):
            cur["options"] = []
        elif qtype == "jigsaw":
            cur["options"] = []
            m = re.fullmatch(r"\s*(\d+)\s*[x×*]\s*(\d+)\s*", str(cur.pop("grid", "3x3")))        # "4x3" = 4 pieces across, 3 down
            cols, rows = (int(m.group(1)), int(m.group(2))) if m else (3, 3)
            cur.pop("preview", None)                                                  # old quizzes: phones no longer show the picture unasked
            cur["jigsaw"] = {"rows": rows, "cols": cols, "hint": cur.pop("hint_cost", 0),
                             "partial": str(cur.pop("partial", "no")).lower() in _YES}
        if "places" in cur:
            cur["place_weights"] = cur.pop("places")
        if qtype in ("order", "match") and "partial" in cur:
            cur["partial"] = str(cur["partial"]).lower() in _YES
        for o in cur["options"]:
            o.pop("_right", None)
        if qtype != "text":
            cur.pop("fuzzy", None)
        questions.append(cur)
        cur = None

    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("# ") and cur is None and not title:
            title = s[2:].strip()
        elif s[:2].lower() == "q:":
            flush()
            cur = {"text": s[2:].strip(), "options": []}
        elif cur is None:
            desc_lines.append(s)
        elif s.startswith(("- ", "* ")) and len(s) > 2:
            body, ok = s[2:].strip(), False
            if body.endswith("*"):
                body, ok = body[:-1].rstrip(), True
            elif body.lower().startswith("[x]"):
                body, ok = body[3:].strip(), True
            opt = {"id": new_id(), "text": body, "_ok": ok}
            if " = " in body:                                  # a pair for "type: match"
                left, right = body.split(" = ", 1)
                opt.update(text=left.strip(), _right=right.strip())
            cur["options"].append(opt)
        elif s.startswith("="):
            cur.setdefault("answers", []).append(s[1:].strip())
        elif s.startswith("?"):
            cur["explanation"] = s[1:].strip()
        elif (m := _KEYVAL.match(s)):
            key, val = m.group(1).lower(), m.group(2).strip()
            cur[_FIELD.get(key, key)] = val
        else:  # continuation of the question text
            cur["text"] += "\n" + s
    flush()
    if not questions:
        raise ValidationError("No questions found (each must start with 'Q:')")
    return validate_quiz({
        "title": title or "Imported quiz",
        "description": "\n".join(desc_lines),
        "questions": questions,
    })

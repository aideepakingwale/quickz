"""Validation of quizzes/questions, rich-text sanitising, and the plain-text import format.

Question and answer text is stored as a small, *sanitised* HTML subset (what the WYSIWYG editor produces).
Nothing else is ever stored or sent to players' devices: unknown tags are dropped, attributes are
whitelisted, and colours/alignment are validated.
"""

import html
import re
import secrets
from html.parser import HTMLParser

QUESTION_TYPES = ("mcq", "multi", "buzzer")
MEDIA_KINDS = ("audio", "video")
MEDIA_WHERE = ("screen", "players", "both")
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
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


def _upload_url(url, what: str) -> str:
    url = _clean(url, 200)
    if url and not (url.startswith("/uploads/") and ".." not in url and "\\" not in url):
        raise ValidationError(f"{what} must be uploaded through the host console")
    return url


def _validate_media(raw, qtype: str):
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValidationError("Invalid media")
    kind = raw.get("kind")
    if kind not in MEDIA_KINDS:
        raise ValidationError("Media must be audio or video")
    url = _upload_url(raw.get("url"), "Media")
    if not url:
        return None
    ext = "." + url.rsplit(".", 1)[-1].lower() if "." in url else ""
    if ext not in (AUDIO_EXT if kind == "audio" else VIDEO_EXT) and ext not in (AUDIO_EXT | VIDEO_EXT):
        raise ValidationError("Unsupported media file type")
    where = raw.get("where", "screen")
    if where not in MEDIA_WHERE:
        raise ValidationError("Choose where the media plays")
    return {
        "kind": kind, "url": url, "where": where,
        # buzzer questions are answered while the clip plays (name that tune), so the timer never waits for them
        "timer_after": bool(raw.get("timer_after")) and qtype != "buzzer",
    }


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
        raise ValidationError("Question text is required")
    if len(plain) > MAX_TEXT_PLAIN or len(text) > MAX_HTML:
        raise ValidationError(f"Question text is too long (max {MAX_TEXT_PLAIN} characters)")
    image = _upload_url(raw.get("image"), "Images")

    options, correct = [], []
    if qtype != "buzzer":
        raw_opts = raw.get("options") or []
        if not 2 <= len(raw_opts) <= 6:
            raise ValidationError("Provide between 2 and 6 options")
        seen = set()
        for o in raw_opts:
            oid = _clean(o.get("id"), 16) if isinstance(o, dict) else ""
            raw_text = o.get("text") if isinstance(o, dict) else o
            otext = sanitize_html(raw_text if rich else md_to_html(raw_text), inline=True)
            oplain = strip_tags(otext)
            if not oplain:
                raise ValidationError("Options cannot be empty")
            if len(oplain) > MAX_OPTION_PLAIN:
                raise ValidationError(f"Options are limited to {MAX_OPTION_PLAIN} characters")
            if not oid or oid in seen:
                oid = new_id()
            seen.add(oid)
            options.append({"id": oid, "text": otext})
        ids = {o["id"] for o in options}
        correct = [c for c in (raw.get("correct") or []) if c in ids]
        correct = list(dict.fromkeys(correct))
        if not correct:
            raise ValidationError("Mark at least one correct option")
        if qtype == "mcq" and len(correct) != 1:
            raise ValidationError("Single-choice questions need exactly one correct option")

    return {
        "id": _clean(raw.get("id"), 16) or new_id(),
        "type": qtype,
        "rich": True,
        "text": text,
        "image": image,
        "media": _validate_media(raw.get("media"), qtype),
        "options": options,
        "correct": correct,
        "time_limit": _int(raw.get("time_limit"), 5, 300, 30),
        "points": _int(raw.get("points"), 0, 10000, 1000),
        # share of the points that depends on answering quickly: 0 = flat, 100 = a last-second answer earns nothing
        "speed_weight": _int(raw.get("speed_weight"), 0, 100, 50),
        # how many correct answers can score: 0 = everyone, 1 = only the first (they get 100%), N = the first N.
        # Buzzer questions are always "first player wins", so the flag does not apply to them.
        "winners": 0 if qtype == "buzzer" else _int(raw.get("winners"), 0, 50, 0),
        "explanation": _clean(raw.get("explanation"), 500),
    }


def validate_quiz(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("Quiz must be an object")
    title = _clean(raw.get("title"), 120)
    if not title:
        raise ValidationError("Quiz title is required")
    questions = raw.get("questions") or []
    if len(questions) > 200:
        raise ValidationError("A quiz can have at most 200 questions")
    cleaned = []
    for i, q in enumerate(questions, 1):
        try:
            cleaned.append(validate_question(q))
        except ValidationError as e:
            raise ValidationError(f"Question {i}: {e}") from None
    return {
        "title": title,
        "description": _clean(raw.get("description"), 1000),
        "questions": cleaned,
    }


_KEYVAL = re.compile(r"^(time|points|type|image|speed|winners)\s*:\s*(.+)$", re.I)


def parse_text_quiz(text: str) -> dict:
    """Parse the plain-text format documented in docs/PRD_v2.md section 7."""
    title, desc_lines, questions, cur = "", [], [], None

    def flush():
        nonlocal cur
        if cur is None:
            return
        correct = [o["id"] for o in cur["options"] if o.pop("_ok")]
        qtype = cur.get("type") or ("multi" if len(correct) > 1 else "mcq")
        cur.update(type=qtype, correct=[] if qtype == "buzzer" else correct)
        if qtype == "buzzer":
            cur["options"] = []
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
            cur["options"].append({"id": new_id(), "text": body, "_ok": ok})
        elif s.startswith("?"):
            cur["explanation"] = s[1:].strip()
        elif (m := _KEYVAL.match(s)):
            key, val = m.group(1).lower(), m.group(2).strip()
            cur[{"time": "time_limit", "speed": "speed_weight"}.get(key, key)] = val
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

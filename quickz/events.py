"""Events: a registration page that exists long before the game does.

A host creates an Event (title, date, quiz). Its permanent link/QR can be published weeks ahead; people register once
(real name, email, 4-digit PIN, avatar). When the host opens the lobby the same page turns into "Join now" and registered
people are let straight in, with no form. Nothing here sends email: the email only identifies a person (one registration
each), and the PIN lets them sign in again on another phone.
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PIN_RE = re.compile(r"\d{4}")
TZ_RE = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")
PIN_ROUNDS = 2000          # a 4-digit PIN has only 10,000 values: slowing down hashing buys nothing, the sign-in throttle does the work
ENTER_STATES = ("live", "running")        # the lobby is open / the quiz is under way


def make_slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")[:30].strip("-") or "quiz"
    return f"{base}-{''.join(secrets.choice(SLUG_ALPHABET) for _ in range(4))}"


def clean_tz(value) -> str:
    v = str(value or "").strip()
    return v if TZ_RE.fullmatch(v) else "UTC"


def clean_pin(value) -> str | None:
    v = str(value or "").strip()
    return v if PIN_RE.fullmatch(v) else None


def new_pin() -> str:
    return f"{secrets.randbelow(10000):04d}"


def new_ticket() -> str:
    return secrets.token_urlsafe(24)


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(8)
    return f"{salt.hex()}${hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, PIN_ROUNDS).hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        salt, want = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(salt), PIN_ROUNDS).hex()
        return hmac.compare_digest(got, want)
    except ValueError:
        return False


def event_state(ev: dict, game) -> str:
    """open: no game yet / closed: host closed registration / live: lobby open / running: quiz under way / finished."""
    if ev["game_code"]:
        if game is None:
            return "finished"                       # the game ended (and was dropped on a restart) or was removed
        return {"lobby": "live", "finished": "finished"}.get(game.phase, "running")
    return "open" if ev["reg_open"] else "closed"


def registration_status(ev: dict, state: str, registered: int) -> str:
    """open | full | closed | started | finished - may a NEW person register right now?"""
    if state == "finished":
        return "finished"
    if state == "running":
        return "started"                            # walk-ins are welcome until the quiz starts, not after
    if not ev["reg_open"]:
        return "closed"
    return "full" if registered >= ev["capacity"] else "open"


def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """RFC 5545: lines are at most 75 octets; continuation lines start with one space."""
    out, cur = [], ""
    for ch in line:
        limit = 75 if not out else 74
        if len((cur + ch).encode("utf-8")) > limit:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return [out[0]] + [" " + p for p in out[1:]]


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def make_ics(ev: dict, url: str) -> str:
    """A calendar file with two built-in reminders (a day before and 15 minutes before)."""
    start = ev["starts_at"]
    end = start + ev["duration_min"] * 60
    about = f"{ev['description'].strip()}\n\n" if ev["description"].strip() else ""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DiQuizBuzz//Events//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
             f"UID:{ev['slug']}@quickz", f"DTSTAMP:{_utc(ev['created_at'])}", f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}",
             f"SUMMARY:{_esc(ev['title'])}", f"DESCRIPTION:{_esc(about + 'Open this link when the quiz starts: ' + url)}",
             f"URL:{url}", f"LOCATION:{_esc(url)}"]
    for trigger, text in (("-P1D", "Quiz tomorrow"), ("-PT15M", "Quiz starts in 15 minutes")):
        lines += ["BEGIN:VALARM", f"TRIGGER:{trigger}", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(text)}", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"


def clean_text(value, limit: int) -> str:
    s = "".join(ch for ch in str(value or "") if ch.isprintable() or ch == "\n")
    return re.sub(r"[ \t]+", " ", s).strip()[:limit]


def duration_ok(value, default: int = 60) -> int:
    try:
        return max(5, min(int(value), 720))
    except (TypeError, ValueError):
        return default

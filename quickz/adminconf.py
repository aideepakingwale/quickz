"""Platform settings the Super Admin can change while the server runs.

Each setting already existed as a fixed number or a server environment variable. The values here are saved in the database,
checked against a safe range, applied immediately, and re-applied when the server starts. Anything not set here keeps the
value the server was started with (its default).
"""

from __future__ import annotations

import re
from pathlib import Path

from . import auth, engine, questions

GROUPS = (("limits", "Limits"), ("uploads", "Uploads"), ("security", "Security"))

# key: (group, label, help, min, max, unit)
SPECS: dict[str, dict] = {
    "max_players": {"group": "limits", "label": "Players per game", "min": 10, "max": 5000, "unit": "players",
                    "help": "The most players who can join one game. Raise it only if the server has the memory for it."},
    "max_questions": {"group": "limits", "label": "Questions per quiz", "min": 10, "max": 500, "unit": "questions",
                      "help": "Quizzes with more questions than this cannot be saved. Existing quizzes are not changed."},
    "max_image_mb": {"group": "uploads", "label": "Largest picture", "min": 1, "max": 5, "unit": "MB",
                     "help": "Pictures are shrunk in the browser first, so most are far smaller. Bigger limits use more storage and slow phones."},
    "max_gif_mb": {"group": "uploads", "label": "Largest animated GIF to accept", "min": 3, "max": 50, "unit": "MB",
                   "help": "A GIF bigger than the largest picture is shrunk automatically (smaller, fewer colours, fewer frames) to fit it. Bigger GIFs than this are refused."},
    "max_music_mb": {"group": "uploads", "label": "Largest lobby music file", "min": 1, "max": 20, "unit": "MB",
                     "help": "For the waiting-screen music (MP3, M4A, OGG or WAV)."},
    "max_media_mb": {"group": "uploads", "label": "Largest question audio or video", "min": 1, "max": 500, "unit": "MB",
                     "help": "For clips attached to questions."},
    "session_days": {"group": "security", "label": "Stay signed in for", "min": 1, "max": 90, "unit": "days",
                     "help": "How long a host stays signed in on a device. It applies to new sign-ins."},
    "password_min": {"group": "security", "label": "Shortest password", "min": 8, "max": 64, "unit": "characters",
                     "help": "Applies when a password is next set or changed. Existing passwords still work."},
    "login_attempts": {"group": "security", "label": "Wrong passwords before a lock", "min": 3, "max": 20, "unit": "tries",
                       "help": "After this many wrong passwords for one username, sign-in is paused."},
    "login_lock_minutes": {"group": "security", "label": "Sign-in pause after a lock", "min": 1, "max": 120, "unit": "minutes",
                           "help": "How long sign-in stays paused after too many wrong passwords."},
}


DEFAULT_PASSWORD_MIN = 10        # fixed numbers, so one server's changes never leak into another's idea of "default"
DEFAULT_MAX_QUESTIONS = 200


def defaults(settings, guard) -> dict:
    """What the server was started with: the value used when nothing has been changed here."""
    return {"max_players": settings.max_players, "max_questions": DEFAULT_MAX_QUESTIONS, "max_image_mb": 2, "max_gif_mb": 25, "max_music_mb": 5, "max_media_mb": settings.max_media_mb,
            "session_days": settings.session_days, "password_min": DEFAULT_PASSWORD_MIN,
            "login_attempts": guard.user_limit if guard else 5, "login_lock_minutes": int((guard.lock if guard else 300) // 60)}


def clean(key: str, value) -> int:
    spec = SPECS[key]
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{spec['label']} must be a number.") from None
    if not spec["min"] <= n <= spec["max"]:
        raise ValueError(f"{spec['label']} must be between {spec['min']} and {spec['max']} {spec['unit']}.")
    return n


def apply(settings, guard, values: dict) -> None:
    """Put the effective values to work. Called at start-up and after every change."""
    settings.max_players = values["max_players"]
    engine.MAX_PLAYERS = values["max_players"]
    questions.MAX_QUESTIONS = values["max_questions"]
    settings.max_image_mb = values["max_image_mb"]
    settings.max_music_mb = values["max_music_mb"]
    settings.max_gif_mb = values["max_gif_mb"]
    settings.max_media_mb = values["max_media_mb"]
    settings.session_days = values["session_days"]
    auth.MIN_PASSWORD = values["password_min"]
    if guard is not None:
        guard.user_limit = values["login_attempts"]
        guard.lock = values["login_lock_minutes"] * 60


STALE_GAME_HOURS = 12            # a game that never finished is treated as abandoned after this long


def game_block_reason(row: dict, now: float) -> str:
    """Why a game cannot be deleted right now, or "" when it can. A finished game can always go; one that is still open must be old."""
    if row["status"] == "finished":
        return ""
    if now - row["created_at"] < STALE_GAME_HOURS * 3600:
        return f"It is still open (started less than {STALE_GAME_HOURS} hours ago)."
    return ""


UPLOAD_REF = re.compile(r"/uploads/([0-9a-f]{16}\.[a-z0-9]{3,4})")


def referenced_uploads(db) -> set[str]:
    """Every uploaded file name that a quiz, a game or an event still points at."""
    found: set[str] = set()
    for table, cols in (("quizzes", ("questions", "cover", "screen_bg", "lobby_music", "description")), ("games", ("questions", "settings", "description")),
                        ("events", ("description",))):
        for row in db._all(f"SELECT {', '.join(cols)} FROM {table}"):
            for v in row.values():
                if v:
                    found.update(UPLOAD_REF.findall(str(v)))
    return found


def scan_uploads(db, uploads_dir: Path, min_age_s: float = 86400, now: float | None = None) -> dict:
    """Files on disk that nothing refers to any more. Files newer than min_age_s are left alone: someone may still be
    editing a quiz they have not saved yet."""
    import time
    now = now or time.time()
    used = referenced_uploads(db)
    total = unused = 0
    total_b = unused_b = 0
    files = []
    if uploads_dir.is_dir():
        for p in uploads_dir.iterdir():
            if not p.is_file() or p.suffix == ".part" or not re.fullmatch(r"[0-9a-f]{16}\.[a-z0-9]{3,4}", p.name):
                continue
            size = p.stat().st_size
            total += 1
            total_b += size
            if p.name not in used and now - p.stat().st_mtime >= min_age_s:
                unused += 1
                unused_b += size
                files.append(p.name)
    return {"files": total, "bytes": total_b, "unused_files": unused, "unused_bytes": unused_b, "names": files}

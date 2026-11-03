"""Authentication primitives: password hashing (stdlib scrypt), session tokens, login throttling.

No third-party crypto dependency: hashlib.scrypt is memory-hard and ships with Python.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSWORD = 10
MAX_PASSWORD = 128
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
COMMON_PASSWORDS = {
    "password", "password1", "password123", "1234567890", "12345678910", "qwertyuiop", "iloveyou12",
    "admin12345", "letmein1234", "welcome123", "quickz12345", "changeme123",
}
# no 0/O/1/l/I so a temporary password can be read out or typed without mistakes
TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class AuthError(ValueError):
    pass


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(want))
    except Exception:
        return False


# used to burn the same CPU time for unknown usernames, so response time doesn't reveal which usernames exist
_DUMMY_HASH = hash_password("quickz-timing-equaliser")


def verify_unknown_user(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def normalize_username(username) -> str:
    u = str(username or "").strip().lower()
    if not USERNAME_RE.match(u):
        raise AuthError("Usernames are 3-32 characters: letters, numbers, dot, dash or underscore.")
    return u


def clean_display_name(name, fallback: str) -> str:
    n = " ".join("".join(ch for ch in str(name or "") if ch.isprintable()).split())[:60]
    return n or fallback


def validate_password(password, username: str = "") -> str:
    pw = str(password or "")
    if len(pw) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.")
    if len(pw) > MAX_PASSWORD:
        raise AuthError(f"Use at most {MAX_PASSWORD} characters.")
    low = pw.lower()
    if low in COMMON_PASSWORDS or (username and username.lower() in low and len(low) < len(username) + 6):
        raise AuthError("That password is too easy to guess. Try a longer phrase.")
    if len(set(pw)) < 4:
        raise AuthError("That password is too repetitive.")
    return pw


def temp_password() -> str:
    raw = "".join(secrets.choice(TEMP_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Only the hash of a session token is stored, so a copy of the database can't be replayed as cookies."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginGuard:
    """In-memory throttle. Failures count per username and per IP inside a sliding window."""

    def __init__(self, user_limit=5, ip_limit=25, window=600, lock=300, clock=time.monotonic):
        self.user_limit, self.ip_limit, self.window, self.lock, self.clock = user_limit, ip_limit, window, lock, clock
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def _prune(self, key: str) -> list[float]:
        now = self.clock()
        hits = [t for t in self._fails.get(key, []) if now - t < self.window]
        self._fails[key] = hits
        return hits

    def retry_after(self, username: str, ip: str) -> int:
        """Seconds the caller must wait, or 0 if the attempt may proceed."""
        now = self.clock()
        wait = 0.0
        for key in (f"u:{username}", f"i:{ip}"):
            until = self._locked.get(key, 0)
            if until > now:
                wait = max(wait, until - now)
            elif key in self._locked:
                del self._locked[key]
        return int(wait) + 1 if wait > 0 else 0

    def failure(self, username: str, ip: str) -> None:
        now = self.clock()
        for key, limit in ((f"u:{username}", self.user_limit), (f"i:{ip}", self.ip_limit)):
            hits = self._prune(key)
            hits.append(now)
            if len(hits) >= limit:
                self._locked[key] = now + self.lock
                self._fails[key] = []

    def success(self, username: str) -> None:
        self._fails.pop(f"u:{username}", None)
        self._locked.pop(f"u:{username}", None)

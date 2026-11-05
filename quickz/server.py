"""FastAPI app: static pages, accounts + REST API for hosts, and role-based WebSockets.

Roles
  admin  - Super Admin: manages host accounts and can see every quiz/game.
  host   - Quiz Host: sees and manages only their own quizzes, games and results.
Players and the projector never sign in.
"""

import asyncio
import contextlib
import csv
import hmac
import io
import json
import logging
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

import segno
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import ai as ai_mod
from . import aimage as aimage_mod
from . import adminconf as adminconf_mod
from . import gifshrink
from . import mcp as mcp_mod
from . import auth
from .cloud import CloudSync
from .config import Settings
from .db import Database
from .engine import GameError, GameManager, game_results, player_detail
from .showcase import showcase_quiz
from . import engine as engine_mod
from . import events as ev_mod
from .proxy import BehindCloudFront
from . import questions as questions_mod
from .questions import ValidationError, parse_embed_link, parse_text_quiz, validate_quiz

log = logging.getLogger("quickz")
STATIC = Path(__file__).parent / "static"
COOKIE = "quickz_session"
CSRF_HEADER = "x-quickz"
MAX_WS_BYTES = 4096
MAX_MSGS_PER_SEC = 25
MAX_SCREENS = 20
MUSIC_EXT = (".mp3", ".m4a", ".ogg", ".wav")        # the few music formats we accept
IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
]


class RevalidatedStatic(StaticFiles):
    """Static files that the browser re-checks on every load (a cheap 304 when unchanged), so an update to a script and the
    scripts it works with always arrive together instead of a stale mix."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "", headers: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.headers = status, message, code, headers or {}


def sniff_upload(data: bytes) -> tuple[str, str] | None:
    """(kind, extension) judged from the file's own bytes - never from its name or declared type."""
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return "image", ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", ".webp"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio", ".wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return ("audio", ".aac") if len(data) > 1 and data[0] == 0xFF and (data[1] & 0xF6) == 0xF0 else ("audio", ".mp3")
    if data[:4] == b"OggS":
        return "audio", ".ogg"
    if data[:4] == b"fLaC":
        return "audio", ".flac"
    if data[4:8] == b"ftyp":
        return ("audio", ".m4a") if data[8:12] in (b"M4A ", b"M4B ", b"M4P ") else ("video", ".mp4")
    if data[:4] == b"\x1aE\xdf\xa3":
        return "video", ".webm"
    return None


def fmt_wait(seconds: int) -> str:
    return f"{seconds} second{'s' if seconds != 1 else ''}" if seconds < 90 else f"{round(seconds / 60)} minutes"


def public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"], "role": u["role"], "must_change": bool(u["must_change"])}


UPLOAD_NAME = re.compile(r"^[0-9a-f]{16}\.[a-z0-9]{3,4}$")


def create_app(settings: Settings, guard: auth.LoginGuard | None = None, cloud: CloudSync | None = None) -> FastAPI:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    cloud = cloud if cloud is not None else CloudSync.from_env()
    if cloud:
        cloud.restore_if_needed(settings.db_path)   # empty disk + snapshot in the bucket -> get our data back
    db = Database(settings.db_path)
    if cloud:
        cloud.attach(db, settings.uploads_dir)
    questions_mod.IMAGE_HOSTS.update(settings.image_hosts)
    engine_mod.MAX_PLAYERS = settings.max_players
    manager = GameManager(db)
    manager.restore()
    db.purge_sessions()
    guard = guard or auth.LoginGuard()
    conf_defaults = adminconf_mod.defaults(settings, guard)       # what this server was started with
    if not gifshrink.HAVE_PILLOW:
        log.warning("Pillow is not installed: animated GIFs over the picture limit will be refused instead of shrunk")
    gif_slot = asyncio.Semaphore(1)                               # one GIF is shrunk at a time, so a small server is never asked for much memory at once

    def conf_effective() -> dict:
        eff = dict(conf_defaults)
        for k, v in db.app_settings().items():
            if k in adminconf_mod.SPECS:
                try:
                    eff[k] = adminconf_mod.clean(k, v)
                except ValueError:
                    pass                                          # a stored value that is no longer in range falls back to the default
        return eff

    adminconf_mod.apply(settings, guard, conf_effective())

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        if cloud:
            cloud.start()

        async def lobby_watch():                                   # a waiting lobby follows edits to its quiz (background, music, text)
            while True:
                await asyncio.sleep(5)
                try:
                    await manager.refresh_lobbies()
                except Exception:
                    log.exception("could not refresh the waiting lobbies")
        watcher = asyncio.create_task(lobby_watch())
        yield
        watcher.cancel()
        if cloud:
            await cloud.stop()

    app = FastAPI(title="DiQuizBuzz", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if settings.origin_secret or settings.trust_proxy:
        app.add_middleware(BehindCloudFront, secret=settings.origin_secret, trust_proto=settings.trust_proxy)
    app.state.settings, app.state.db, app.state.manager, app.state.guard, app.state.cloud = settings, db, manager, guard, cloud
    # Until the first Super Admin exists, setup needs this one-time token (printed in the terminal).
    app.state.setup_token = (settings.setup_token or secrets.token_urlsafe(9)) if db.count_users() == 0 else None

    # ---- errors + headers --------------------------------------------------------
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status, headers=exc.headers)

    # Anything unexpected: a reference for the person, the full traceback in data/errors.log (and the console) for whoever fixes it.
    errlog = logging.FileHandler(settings.data_dir / "errors.log", encoding="utf-8", delay=True)
    errlog.setLevel(logging.ERROR)
    errlog.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename == errlog.baseFilename for h in log.handlers):
        log.addHandler(errlog)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        ref = secrets.token_hex(3)
        log.error("unexpected error %s on %s %s", ref, request.method, request.url.path, exc_info=exc)
        return JSONResponse({"error": f"Something went wrong on the server (reference {ref}). Please try again. If it keeps happening, send this reference to the administrator: details are in the data folder's errors.log.",
                             "code": "server_error", "ref": ref}, status_code=500)

    @app.exception_handler(GameError)
    async def game_error(_: Request, exc: GameError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(ValidationError)
    async def validation_error(_: Request, exc: ValidationError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(auth.AuthError)
    async def auth_error(_: Request, exc: auth.AuthError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- session helpers ------------------------------------------------------------
    def session_token(request) -> str | None:
        return request.cookies.get(COOKIE)

    def user_from_cookie(cookies) -> dict | None:
        token = cookies.get(COOKIE)
        return db.get_session_user(auth.hash_token(token)) if token else None

    def set_cookie(response: Response, request: Request, token: str):
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/",
                            max_age=settings.session_days * 86400, secure=request.url.scheme == "https")

    def start_session(user: dict, request: Request, response: Response):
        token = auth.new_token()
        db.create_session(auth.hash_token(token), user["id"], time.time() + settings.session_days * 86400,
                          request.headers.get("user-agent", ""))
        set_cookie(response, request, token)

    def csrf(request: Request):
        # A cross-site form/script cannot add custom headers, so requiring one defeats CSRF on top of SameSite=Lax.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            raise ApiError(403, "Missing request header", "csrf")

    def user_pending(request: Request) -> dict:
        """Signed-in user, even if they still must choose a new password."""
        csrf(request)
        u = user_from_cookie(request.cookies)
        if not u:
            raise ApiError(401, "Please sign in.", "unauthenticated")
        return u

    def user_dep(request: Request) -> dict:
        u = user_pending(request)
        if u["must_change"]:
            raise ApiError(403, "Choose a new password before continuing.", "password_change_required")
        return u

    def admin_dep(user: dict = Depends(user_dep)) -> dict:
        if user["role"] != "admin":
            raise ApiError(403, "Only a Super Admin can do that.", "forbidden")
        return user

    def can_access(user: dict, owner_id) -> bool:
        return user["role"] == "admin" or owner_id == user["id"]

    def quiz_for(user: dict, quiz_id: int) -> dict:
        quiz = db.get_quiz(quiz_id)
        if not quiz or not can_access(user, quiz["owner_id"]):
            raise HTTPException(404, "Quiz not found")  # 404, never 403: don't reveal other hosts' content
        return quiz

    def game_for(user: dict, game_id: int) -> dict:
        game = db.get_game(game_id)
        if not game or not can_access(user, game["owner_id"]):
            raise HTTPException(404, "Game not found")
        return game

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # ---- auth routes ------------------------------------------------------------------
    @app.get("/api/auth/state")
    async def auth_state(request: Request):
        u = user_from_cookie(request.cookies)
        return {"setup_needed": db.count_users() == 0, "user": public_user(u) if u else None, "public_url": settings.public_url,
                "max_media_mb": settings.max_media_mb, "max_image_mb": settings.max_image_mb, "max_music_mb": settings.max_music_mb, "max_gif_mb": settings.max_gif_mb}

    @app.post("/api/auth/setup")
    async def auth_setup(request: Request, response: Response):
        csrf(request)
        body = await request.json()
        ip = client_ip(request)
        wait = guard.retry_after("setup", ip)
        if wait:
            raise ApiError(429, f"Too many attempts. Try again in {fmt_wait(wait)}.", "rate_limited", {"Retry-After": str(wait)})
        if db.count_users() > 0 or not app.state.setup_token:
            raise ApiError(409, "Setup has already been completed. Sign in instead.", "setup_done")
        if not hmac.compare_digest(str(body.get("token", "")).strip().encode(), app.state.setup_token.encode()):
            guard.failure("setup", ip)
            raise ApiError(403, "That setup token is not right. Copy it from the terminal where DiQuizBuzz is running.", "bad_token")
        username = auth.normalize_username(body.get("username"))
        password = auth.validate_password(body.get("password"), username)
        uid = db.create_user(username, auth.clean_display_name(body.get("display_name"), username), auth.hash_password(password), "admin")
        db.claim_orphans(uid)
        app.state.setup_token = None
        user = db.get_user(uid)
        start_session(user, request, response)
        return {"user": public_user(user)}

    @app.post("/api/auth/login")
    async def auth_login(request: Request, response: Response):
        csrf(request)
        body = await request.json()
        username = str(body.get("username") or "").strip().lower()[:64]
        password = str(body.get("password") or "")[: auth.MAX_PASSWORD]
        ip = client_ip(request)
        wait = guard.retry_after(username, ip)
        if wait:
            raise ApiError(429, f"Too many sign-in attempts. Try again in {fmt_wait(wait)}.", "rate_limited", {"Retry-After": str(wait)})
        user = db.get_user_by_username(username) if username else None
        ok = auth.verify_password(password, user["password_hash"]) if user else (auth.verify_unknown_user(password) or False)
        if not (user and ok):
            guard.failure(username, ip)
            raise ApiError(401, "Incorrect username or password.", "bad_credentials")
        if user["disabled"]:
            raise ApiError(403, "This account has been disabled. Ask your Super Admin to re-enable it.", "disabled")
        guard.success(username)
        db.update_user(user["id"], last_login=time.time())
        db.purge_sessions()
        start_session(user, request, response)
        return {"user": public_user(user)}

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request, response: Response):
        csrf(request)
        token = session_token(request)
        if token:
            db.delete_session(auth.hash_token(token))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/auth/logout-all")
    async def auth_logout_all(request: Request, response: Response, user: dict = Depends(user_pending)):
        db.delete_user_sessions(user["id"])
        db.revoke_user_tokens(user["id"])
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/auth/password")
    async def auth_password(request: Request, user: dict = Depends(user_pending)):
        body = await request.json()
        current, new = str(body.get("current") or ""), str(body.get("new") or "")
        if not auth.verify_password(current, user["password_hash"]):
            raise ApiError(403, "Your current password is not right.", "bad_credentials")
        if new == current:
            raise ApiError(400, "Choose a password different from the current one.")
        pw = auth.validate_password(new, user["username"])
        db.update_user(user["id"], password_hash=auth.hash_password(pw), must_change=0)
        keep = auth.hash_token(session_token(request))
        db.delete_user_sessions(user["id"], keep_hash=keep)  # other devices are signed out
        return {"ok": True}

    @app.patch("/api/me")
    async def update_me(request: Request, user: dict = Depends(user_dep)):
        body = await request.json()
        name = auth.clean_display_name(body.get("display_name"), user["username"])
        db.update_user(user["id"], display_name=name)
        return public_user(db.get_user(user["id"]))

    # ---- user management (Super Admin) ----------------------------------------------------
    def user_row(u: dict) -> dict:
        return {k: u[k] for k in ("id", "username", "display_name", "role", "disabled", "must_change", "created_at", "last_login", "quizzes", "games")}

    @app.get("/api/users")
    async def list_users(admin: dict = Depends(admin_dep)):
        return [user_row(u) for u in db.list_users()]

    @app.post("/api/users")
    async def create_user(request: Request, admin: dict = Depends(admin_dep)):
        body = await request.json()
        username = auth.normalize_username(body.get("username"))
        if db.get_user_by_username(username):
            raise ApiError(409, "That username is already taken.", "username_taken")
        role = "admin" if body.get("role") == "admin" else "host"
        temp = auth.temp_password()
        uid = db.create_user(username, auth.clean_display_name(body.get("display_name"), username), auth.hash_password(temp), role, must_change=True)
        return {"id": uid, "username": username, "temp_password": temp}

    @app.patch("/api/users/{user_id}")
    async def update_user(user_id: int, request: Request, admin: dict = Depends(admin_dep)):
        target = db.get_user(user_id)
        if not target:
            raise HTTPException(404, "User not found")
        body = await request.json()
        changes = {}
        if "display_name" in body:
            changes["display_name"] = auth.clean_display_name(body["display_name"], target["username"])
        new_role = body.get("role", target["role"])
        new_disabled = int(bool(body.get("disabled", target["disabled"])))
        if new_role not in ("admin", "host"):
            raise ApiError(400, "Unknown role.")
        losing_admin = target["role"] == "admin" and not target["disabled"] and (new_role != "admin" or new_disabled)
        if target["id"] == admin["id"] and (new_role != "admin" or new_disabled):
            raise ApiError(400, "You can’t demote or disable your own account. Ask another Super Admin.", "self_lockout")
        if losing_admin and db.count_active_admins() <= 1:
            raise ApiError(400, "There must always be at least one active Super Admin.", "last_admin")
        changes.update(role=new_role, disabled=new_disabled)
        db.update_user(user_id, **changes)
        if new_disabled or new_role != target["role"]:
            db.delete_user_sessions(user_id)  # take effect immediately
        return user_row(next(u for u in db.list_users() if u["id"] == user_id))

    @app.post("/api/users/{user_id}/reset-password")
    async def reset_password(user_id: int, admin: dict = Depends(admin_dep)):
        target = db.get_user(user_id)
        if not target:
            raise HTTPException(404, "User not found")
        temp = auth.temp_password()
        db.update_user(user_id, password_hash=auth.hash_password(temp), must_change=1)
        db.delete_user_sessions(user_id)
        db.revoke_user_tokens(user_id)
        return {"temp_password": temp}

    # ---- quizzes (owner-scoped) --------------------------------------------------------------
    @app.get("/api/quizzes")
    async def list_quizzes(scope: str = "", user: dict = Depends(user_dep)):
        everything = user["role"] == "admin" and scope != "mine"
        rows = db.list_quizzes(None if everything else user["id"])
        for r in rows:
            r["mine"] = r["owner_id"] == user["id"]
        return rows

    @app.post("/api/quizzes")
    async def create_quiz(request: Request, user: dict = Depends(user_dep)):
        quiz = validate_quiz(await request.json())
        return {"id": db.create_quiz(quiz, user["id"])}

    @app.post("/api/quizzes/import")
    async def import_quiz(request: Request, user: dict = Depends(user_dep)):
        body = await request.json()
        quiz = parse_text_quiz(body["text"]) if isinstance(body.get("text"), str) else validate_quiz(body.get("quiz"))
        return {"id": db.create_quiz(quiz, user["id"])}

    @app.post("/api/quizzes/showcase")
    async def showcase_quiz_route(user: dict = Depends(user_dep)):
        """A ready-made quiz with one of every question type and bundled artwork: the fastest way to see what DiQuizBuzz can do."""
        return {"id": db.create_quiz(validate_quiz(showcase_quiz()), user["id"])}

    @app.post("/api/embed/parse")
    async def embed_parse(request: Request, user: dict = Depends(user_dep)):
        """Check a pasted YouTube/Vimeo address and say what it is, so the editor can preview it before saving."""
        body = await request.json()
        return parse_embed_link(body.get("link") if isinstance(body, dict) else "")

    @app.post("/api/quizzes/parse")
    async def parse_quiz(request: Request, user: dict = Depends(user_dep)):
        """Validate pasted text without saving - used by the editor's 'paste questions' dialog."""
        body = await request.json()
        return parse_text_quiz(str(body.get("text", "")))

    @app.get("/api/quizzes/{quiz_id}")
    async def get_quiz(quiz_id: int, user: dict = Depends(user_dep)):
        return quiz_for(user, quiz_id)

    @app.put("/api/quizzes/{quiz_id}")
    async def update_quiz(quiz_id: int, request: Request, user: dict = Depends(user_dep)):
        quiz_for(user, quiz_id)
        db.update_quiz(quiz_id, validate_quiz(await request.json()))
        return {"ok": True}

    @app.delete("/api/quizzes/{quiz_id}")
    async def delete_quiz(quiz_id: int, user: dict = Depends(user_dep)):
        quiz_for(user, quiz_id)
        db.delete_quiz(quiz_id)
        return {"ok": True}

    @app.post("/api/quizzes/{quiz_id}/duplicate")
    async def duplicate_quiz(quiz_id: int, user: dict = Depends(user_dep)):
        quiz = quiz_for(user, quiz_id)
        quiz["title"] = f"{quiz['title']} (copy)"[:120]
        return {"id": db.create_quiz(quiz, user["id"])}  # the copy belongs to whoever copied it

    @app.get("/api/quizzes/{quiz_id}/export")
    async def export_quiz(quiz_id: int, user: dict = Depends(user_dep)):
        quiz = quiz_for(user, quiz_id)
        payload = {"title": quiz["title"], "description": quiz["description"], "cover": quiz.get("cover", ""), "screen_bg": quiz.get("screen_bg", ""), "screen_bg_wash": quiz.get("screen_bg_wash", 40), "lobby_music": quiz.get("lobby_music", ""), "questions": quiz["questions"]}
        return Response(json.dumps(payload, indent=2), media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="quiz-{quiz_id}.json"'})

    @app.post("/api/uploads")
    async def upload(request: Request, user: dict = Depends(user_dep)):
        """Images (2 MB), lobby music (?purpose=music: MP3, M4A, OGG or WAV, 5 MB) and other audio/video (QUICKZ_MAX_MEDIA_MB).
        Streamed to disk, type decided by sniffing the bytes."""
        media_cap = settings.max_media_mb * 1024 * 1024
        image_cap, music_cap = settings.max_image_mb * 1024 * 1024, settings.max_music_mb * 1024 * 1024
        gif_cap = max(settings.max_gif_mb, settings.max_image_mb) * 1024 * 1024          # an animated GIF over image_cap is accepted up to here, then shrunk
        music = request.query_params.get("purpose") == "music"
        if int(request.headers.get("content-length") or 0) > (music_cap if music else max(media_cap, gif_cap)):
            raise HTTPException(413, f"Music is too large (max {settings.max_music_mb} MB)" if music else f"File too large (max {settings.max_media_mb} MB)")

        def check_music(kind, ext):
            if music and (kind != "audio" or ext not in MUSIC_EXT):
                raise HTTPException(400, "Music must be an MP3, M4A, OGG or WAV file.")
        name = secrets.token_hex(8)
        tmp = settings.uploads_dir / (name + ".part")
        kind = ext = None
        head, size = b"", 0
        try:
            with open(tmp, "wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if kind is None:
                        head += chunk
                        if len(head) >= 16:
                            sniffed = sniff_upload(head)
                            if not sniffed:
                                raise HTTPException(400, "Unsupported file. Use PNG/JPEG/GIF/WebP images, MP3/M4A/WAV/OGG/FLAC audio, or MP4/WebM video.")
                            kind, ext = sniffed
                            check_music(kind, ext)
                            fh.write(head)
                            head = b""
                    else:
                        fh.write(chunk)
                    if size > ((gif_cap if ext == ".gif" else image_cap) if kind == "image" else music_cap if music else media_cap):
                        raise HTTPException(413, (f"This GIF is too large (max {max(settings.max_gif_mb, settings.max_image_mb)} MB)" if ext == ".gif" else f"Image too large (max {settings.max_image_mb} MB)") if kind == "image" else f"Music is too large (max {settings.max_music_mb} MB)" if music else f"File too large (max {settings.max_media_mb} MB)")
                if kind is None:  # tiny body
                    sniffed = sniff_upload(head)
                    if not sniffed:
                        raise HTTPException(400, "Unsupported file.")
                    kind, ext = sniffed
                    check_music(kind, ext)
                    fh.write(head)
            final = settings.uploads_dir / (name + ext)
            for attempt in range(6):          # Windows: antivirus or an indexer can hold a just-written file for a moment
                try:
                    tmp.replace(final)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    await asyncio.sleep(0.15 * (attempt + 1))
        except OSError as e:                  # disk full, folder not writable, file locked: say so instead of a bare 500
            log.exception("could not store an upload in %s", settings.uploads_dir)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise ApiError(500, f"The server could not save the file ({e.strerror or e}). Check that the data folder is writable and has free space.", "storage") from e
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        shrunk = None
        if kind == "image" and ext == ".gif" and size > image_cap:           # too big for a picture: make it smaller, but keep it animated
            try:
                try:
                    await asyncio.wait_for(gif_slot.acquire(), timeout=20)
                except asyncio.TimeoutError:
                    raise HTTPException(503, "The server is busy shrinking another GIF. Try again in a moment.") from None
                try:
                    shrunk = await asyncio.to_thread(gifshrink.shrink_file, final, int(image_cap * 0.97))
                finally:
                    gif_slot.release()
            except HTTPException:
                with contextlib.suppress(OSError):
                    final.unlink(missing_ok=True)
                raise
            except gifshrink.GifError as e:
                with contextlib.suppress(OSError):
                    final.unlink(missing_ok=True)
                raise HTTPException(413, f"{e} (Pictures can be at most {settings.max_image_mb} MB.)") from e
            except Exception:
                log.exception("could not shrink the GIF %s", final.name)
                with contextlib.suppress(OSError):
                    final.unlink(missing_ok=True)
                raise HTTPException(400, "That GIF could not be processed. Try saving it again, or use a smaller one.") from None
        if cloud:
            cloud.push_upload(final)
        out = {"url": f"/uploads/{name}{ext}", "kind": kind}
        if shrunk:
            out["shrunk"] = {"from": shrunk["from"], "to": shrunk["to"]}
        return out

    @app.get("/uploads/{name}")
    async def serve_upload(name: str):
        """Uploaded media. Names are random; if the local disk was wiped the file is pulled back from the bucket."""
        if not UPLOAD_NAME.match(name):
            raise HTTPException(404, "Not found")
        path = settings.uploads_dir / name
        if cloud and settings.public_bucket_url and await asyncio.to_thread(cloud.public_ready, name):
            # the file lives in the (free-egress) bucket: send the phone there instead of carrying the bytes ourselves
            return RedirectResponse(f"{settings.public_bucket_url}/{cloud.upload_key(name)}", status_code=302,
                                    headers={"Cache-Control": "public, max-age=86400"})
        if not path.is_file() and cloud:
            blob = await asyncio.to_thread(cloud.fetch_upload, name)
            if blob is not None:
                path.write_bytes(blob)
        if not path.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/backup/status")
    async def backup_status(admin: dict = Depends(admin_dep)):
        return cloud.status if cloud else {"enabled": False}

    # ---- AI-assisted question drafting ----------------------------------------------------------
    vault = ai_mod.Vault(settings.data_dir)
    ai_state = {"purged": 0.0}

    def ai_note(user: dict, request: Request, action: str, message: str, status: str = "ok", provider: str = ""):
        db.ai_audit_add({"user_id": user["id"], "username": user["username"], "action": action, "status": status, "provider": provider,
                         "message": message, "ip": client_ip(request)})

    @app.get("/api/ai/options")
    async def ai_options(user: dict = Depends(user_dep)):
        st = db.ai_settings()
        order = ai_mod.provider_order(st, db.ai_providers(with_keys=True))
        used = db.ai_audit_count(user["id"], time.time() - 86400, ("ok", "partial", "error", "refused"))
        used_pics = db.ai_audit_count(user["id"], time.time() - 86400, ("ok", "partial", "error", "refused"), "image")
        pic_order = aimage_mod.image_order(st, db.ai_providers(with_keys=True))
        images = {"enabled": st["images_enabled"], "available": bool(st["images_enabled"] and pic_order), "kinds": list(aimage_mod.PICTURE_KINDS),
                  "styles": {k: v[0] for k, v in aimage_mod.STYLES.items()}, "remaining_today": max(0, st["images_per_user_day"] - used_pics),
                  "can_suggest": bool(order)}
        return {"images": images, "available": bool(st["enabled"] and order), "enabled": st["enabled"], "configured": bool(order),
                "max_questions": st["max_questions"], "remaining_today": max(0, st["per_user_day"] - used),
                "difficulties": ai_mod.DIFFICULTIES, "levels": ai_mod.LEVELS, "quiz_types": ai_mod.QUIZ_TYPES,
                "question_types": ai_mod.QUESTION_TYPES, "age_groups": ai_mod.AGE_LABELS, "languages": ai_mod.LANGUAGES,
                "tones": ai_mod.TONES}

    @app.post("/api/ai/generate")
    async def ai_generate(request: Request, user: dict = Depends(user_dep)):
        now = time.time()
        if now - ai_state["purged"] > 3600:                       # keep the audit log within the retention period
            ai_state["purged"] = now
            keep = now - db.ai_settings()["retention_days"] * 86400
            db.ai_audit_purge(keep)
            db.mcp_audit_purge(keep)
        try:
            body = await request.json()
        except ValueError:
            body = None
        try:
            return await ai_mod.generate(db, vault, user, body, client_ip(request))
        except ai_mod.AIError as e:
            raise ApiError(e.http, e.message, e.status) from e

    @app.post("/api/ai/image/idea")
    async def ai_image_idea(request: Request, user: dict = Depends(user_dep)):
        try:
            body = await request.json()
        except ValueError:
            body = None
        try:
            return await aimage_mod.suggest_picture(db, vault, user, body, client_ip(request))
        except ai_mod.AIError as e:
            raise ApiError(e.http, e.message, e.status) from e

    @app.post("/api/ai/image")
    async def ai_image(request: Request, user: dict = Depends(user_dep)):
        try:
            body = await request.json()
        except ValueError:
            body = None
        try:
            return await aimage_mod.generate_image(db, vault, user, body, client_ip(request))
        except ai_mod.AIError as e:
            raise ApiError(e.http, e.message, e.status) from e

    @app.post("/api/ai/accept")
    async def ai_accept(request: Request, user: dict = Depends(user_dep)):
        """The host tells us how many drafted questions they added, so the audit log shows what was actually used."""
        body = await request.json()
        try:
            db.ai_audit_accept(int(body.get("audit_id")), user["id"], int(body.get("count")))
        except (TypeError, ValueError):
            raise ApiError(400, "Bad request", "bad_request") from None
        return {"ok": True}

    def ai_admin_view() -> dict:
        st, now = db.ai_settings(), time.time()
        provs = db.ai_providers()
        return {"settings": st, "providers": [{"name": n, "label": spec["label"], "model_default": spec["model"], "key_url": spec["key_url"],
                                               "needs_account": bool(spec.get("needs_account")), "needs_base": bool(spec.get("needs_base")),
                                               "image_capable": n in aimage_mod.IMAGE_SERVICES,
                                               "image_model_default": aimage_mod.IMAGE_SERVICES.get(n, {}).get("model", ""),
                                               **provs[n]} for n, spec in ai_mod.PROVIDERS.items()],
                "stats_day": db.ai_stats(now - 86400), "stats_week": db.ai_stats(now - 7 * 86400),
                "builtin_blocked": len(ai_mod.BLOCKED_TERMS)}

    @app.get("/api/admin/ai")
    async def ai_admin_get(admin: dict = Depends(admin_dep)):
        return ai_admin_view()

    @app.put("/api/admin/ai/settings")
    async def ai_admin_settings(request: Request, admin: dict = Depends(admin_dep)):
        body = await request.json()
        if not isinstance(body, dict):
            raise ApiError(400, "Bad request", "bad_request")
        cur, new = db.ai_settings(), {}
        for k in ai_mod.DEFAULT_SETTINGS:
            if k not in body:
                continue
            v = body[k]
            if k in ("enabled", "fallback", "mcp_enabled", "images_enabled"):
                v = bool(v)
            elif k == "active_provider":
                v = v if v in ai_mod.PROVIDERS else ""
            elif k == "images_provider":
                v = v if v in aimage_mod.IMAGE_SERVICES else ""
            elif k == "blocked_terms":
                v = "\n".join(ai_mod.clean_line(t, 60) for t in str(v).splitlines() if t.strip())[:4000]
            else:
                lo, hi = ai_mod.SETTING_LIMITS[k]
                try:
                    v = max(lo, min(hi, int(v)))
                except (TypeError, ValueError):
                    raise ApiError(400, f"{k.replace('_', ' ')} must be a number", "bad_request") from None
            new[k] = v
        changed = {k: v for k, v in new.items() if cur.get(k) != v}
        if changed:
            db.ai_set_settings(changed)
            shown = ", ".join(f"{k.replace('_', ' ')}: " + ("updated" if k == "blocked_terms" else str(v).lower()) for k, v in changed.items())
            ai_note(admin, request, "config", "Settings changed: " + shown)
        return ai_admin_view()

    @app.put("/api/admin/ai/providers/{name}")
    async def ai_admin_provider(name: str, request: Request, admin: dict = Depends(admin_dep)):
        if name not in ai_mod.PROVIDERS:
            raise HTTPException(404, "Unknown service")
        body = await request.json()
        if not isinstance(body, dict):
            raise ApiError(400, "Bad request", "bad_request")
        fields, notes = {}, []
        if "enabled" in body:
            fields["enabled"] = int(bool(body["enabled"]))
            notes.append("enabled" if body["enabled"] else "disabled")
        for k, lim in (("model", 100), ("image_model", 100), ("base_url", 200), ("account_id", 64)):
            if k in body:
                fields[k] = ai_mod.clean_line(body[k], lim)
                notes.append(f"{k} set")
        if "priority" in body:
            try:
                fields["priority"] = max(1, min(99, int(body["priority"])))
            except (TypeError, ValueError):
                raise ApiError(400, "Priority must be a number", "bad_request") from None
        key = str(body.get("api_key") or "").strip()
        if key:
            if len(key) < 8 or len(key) > 400 or re.search(r"\s", key):
                raise ApiError(400, "That does not look like an API key.", "bad_request")
            fields["key_enc"], fields["key_hint"] = vault.seal(key), ai_mod.key_hint(key)
            notes.append("key replaced")
        elif body.get("clear_key"):
            fields["key_enc"], fields["key_hint"], fields["enabled"] = "", "", 0
            notes.append("key removed")
        if fields:
            db.ai_save_provider(name, fields, admin["username"])
            ai_note(admin, request, "config", f"{ai_mod.PROVIDERS[name]['label']}: {', '.join(notes) or 'updated'}", provider=name)
        return ai_admin_view()

    @app.get("/api/admin/ai/providers/{name}/models")
    async def ai_admin_models(name: str, request: Request, admin: dict = Depends(admin_dep)):
        if name not in ai_mod.PROVIDERS:
            raise HTTPException(404, "Unknown service")
        cfg = db.ai_providers(with_keys=True)[name]
        if not cfg["has_key"]:
            raise ApiError(400, "Save an API key first.", "no_key")
        purpose = "image" if request.query_params.get("purpose") == "image" else "text"
        if purpose == "image" and name not in aimage_mod.IMAGE_SERVICES:
            raise ApiError(400, "This service cannot draw pictures.", "bad_request")
        t0 = time.time()
        row = {"user_id": admin["id"], "username": admin["username"], "action": "models", "provider": name, "model": "", "ip": client_ip(request)}
        try:
            models = await asyncio.to_thread(ai_mod.list_models, name, cfg, vault.open(cfg["key_enc"]), db.ai_settings()["timeout_s"], purpose)
            row.update(status="ok", message=f"{len(models)} models listed.")
        except ai_mod.AIError as e:
            row.update(status=e.status, message=e.message)
            row["latency_ms"] = int((time.time() - t0) * 1000)
            db.ai_audit_add(row)
            raise ApiError(502, e.message, "models_failed")
        row["latency_ms"] = int((time.time() - t0) * 1000)
        db.ai_audit_add(row)
        return {"models": models, "current": (cfg["image_model"] or aimage_mod.IMAGE_SERVICES[name]["model"]) if purpose == "image" else (cfg["model"] or ai_mod.PROVIDERS[name]["model"])}

    @app.post("/api/admin/ai/providers/{name}/test")
    async def ai_admin_test(name: str, request: Request, admin: dict = Depends(admin_dep)):
        if name not in ai_mod.PROVIDERS:
            raise HTTPException(404, "Unknown service")
        cfg = db.ai_providers(with_keys=True)[name]
        if not cfg["has_key"]:
            raise ApiError(400, "Save an API key first.", "no_key")
        t0 = time.time()
        row = {"user_id": admin["id"], "username": admin["username"], "action": "test", "provider": name,
               "model": cfg["model"] or ai_mod.PROVIDERS[name]["model"], "ip": client_ip(request)}
        try:
            reply = await asyncio.to_thread(ai_mod.call_provider, name, cfg, vault.open(cfg["key_enc"]),
                                            "You reply with JSON only.", 'Reply with exactly {"ok": true}', db.ai_settings()["timeout_s"])
            ok, msg = True, "Connection works."
            row.update(status="ok", message=msg, tokens_in=reply["tokens_in"], tokens_out=reply["tokens_out"])
        except ai_mod.AIError as e:
            ok, msg = False, e.message
            row.update(status=e.status, message=msg)
        row["latency_ms"] = int((time.time() - t0) * 1000)
        db.ai_audit_add(row)
        return {"ok": ok, "message": msg}

    @app.get("/api/admin/ai/audit")
    async def ai_admin_audit(status: str = "", user: str = "", action: str = "", days: int = 30, limit: int = 50, offset: int = 0,
                             admin: dict = Depends(admin_dep)):
        return db.ai_audit_list(status[:20], user[:40], action[:20], max(1, min(limit, 200)), max(0, offset),
                                time.time() - max(1, min(days, 3650)) * 86400)

    @app.get("/api/admin/ai/audit.csv")
    async def ai_admin_audit_csv(days: int = 30, admin: dict = Depends(admin_dep)):
        data = db.ai_audit_list(limit=100000, since=time.time() - max(1, min(days, 3650)) * 86400)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Time (UTC)", "User", "Action", "Status", "Service", "Model", "Topic", "Difficulty", "Level", "Quiz type", "Question type",
                    "Age group", "Asked", "Returned", "Added", "Tokens in", "Tokens out", "Latency ms", "Note"])
        for r in data["rows"]:
            p = r["params"]
            cells = [time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["ts"])), r["username"], r["action"], r["status"], r["provider"], r["model"],
                     p.get("topic", ""), p.get("difficulty", ""), p.get("level", ""), p.get("quiz_type", ""), p.get("question_type", ""),
                     p.get("age_group", ""), r["requested"], r["returned"], r["accepted"], r["tokens_in"], r["tokens_out"], r["latency_ms"], r["message"]]
            w.writerow([("'" + c) if isinstance(c, str) and c[:1] in "=+-@" else c for c in cells])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="ai-audit.csv"'})

    @app.get("/api/admin/ai/audit/{audit_id}")
    async def ai_admin_audit_one(audit_id: int, admin: dict = Depends(admin_dep)):
        row = db.ai_audit_one(audit_id)
        if not row:
            raise HTTPException(404, "Not found")
        return row

    # ---- platform settings and data clean-up (Super Admin) ---------------------------------------
    def conf_view() -> dict:
        eff = conf_effective()
        groups = [{"id": g, "label": label} for g, label in adminconf_mod.GROUPS]
        specs = [{"key": k, **spec, "default": conf_defaults[k], "value": eff[k], "changed": eff[k] != conf_defaults[k]} for k, spec in adminconf_mod.SPECS.items()]
        changes = sorted(db.ai_audit_list(action="settings", limit=8)["rows"] + db.ai_audit_list(action="cleanup", limit=8)["rows"], key=lambda r: -r["ts"])[:8]
        return {"groups": groups, "settings": specs, "features": {"gif_shrink": gifshrink.HAVE_PILLOW}, "changes": [{"ts": r["ts"], "username": r["username"], "message": r["message"]} for r in changes]}

    @app.get("/api/admin/settings")
    async def admin_settings_get(admin: dict = Depends(admin_dep)):
        return conf_view()

    @app.put("/api/admin/settings")
    async def admin_settings_put(request: Request, admin: dict = Depends(admin_dep)):
        body = await request.json()
        if not isinstance(body, dict) or not body:
            raise ApiError(400, "Nothing to change.", "bad_request")
        cur, new = conf_effective(), {}
        for k, v in body.items():
            if k not in adminconf_mod.SPECS:
                raise ApiError(400, "Unknown setting.", "bad_request")
            try:
                new[k] = adminconf_mod.clean(k, v)
            except ValueError as e:
                raise ApiError(400, str(e), "bad_request") from None
        changed = {k: v for k, v in new.items() if cur[k] != v}
        for k, v in changed.items():
            if v == conf_defaults[k]:
                db.app_unset([k])
            else:
                db.app_set({k: v})
        if changed:
            adminconf_mod.apply(settings, guard, conf_effective())
            ai_note(admin, request, "settings", "Changed " + ", ".join(f"{adminconf_mod.SPECS[k]['label'].lower()} {cur[k]} to {v}" for k, v in changed.items()))
        return conf_view()

    @app.post("/api/admin/settings/reset")
    async def admin_settings_reset(request: Request, admin: dict = Depends(admin_dep)):
        body = await request.json()
        keys = list(adminconf_mod.SPECS) if body.get("keys") == "all" else [k for k in (body.get("keys") or []) if k in adminconf_mod.SPECS]
        cur = conf_effective()
        back = [k for k in keys if cur[k] != conf_defaults[k]]
        db.app_unset(keys)
        adminconf_mod.apply(settings, guard, conf_effective())
        if back:
            ai_note(admin, request, "settings", "Reset to the default: " + ", ".join(adminconf_mod.SPECS[k]["label"].lower() for k in back))
        return conf_view()

    def storage_view() -> dict:
        up = adminconf_mod.scan_uploads(db, settings.uploads_dir)
        try:
            db_bytes = settings.db_path.stat().st_size
        except OSError:
            db_bytes = 0
        return {"counts": db.storage_counts(), "db_bytes": db_bytes, "uploads": {k: up[k] for k in ("files", "bytes", "unused_files", "unused_bytes")}}

    @app.get("/api/admin/storage")
    async def admin_storage(admin: dict = Depends(admin_dep)):
        return storage_view()

    @app.get("/api/admin/games")
    async def admin_games(admin: dict = Depends(admin_dep)):
        now = time.time()
        return [{**g, "blocked": adminconf_mod.game_block_reason(g, now)} for g in db.game_overview()]

    @app.post("/api/admin/games/delete")
    async def admin_games_delete(request: Request, admin: dict = Depends(admin_dep)):
        body = await request.json()
        if not isinstance(body, dict) or body.get("confirm") is not True:
            raise ApiError(400, "Deleting games needs your confirmation.", "confirm_required")
        raw = body.get("ids")
        if not isinstance(raw, list) or not raw or len(raw) > 500:
            raise ApiError(400, "Choose between 1 and 500 games to delete.", "bad_request")
        try:
            ids = {int(i) for i in raw}
        except (TypeError, ValueError):
            raise ApiError(400, "Bad request", "bad_request") from None
        now = time.time()
        rows = {g["id"]: g for g in db.game_overview(limit=100000) if g["id"] in ids}
        ok, refused = [], []
        for gid, g in rows.items():
            reason = adminconf_mod.game_block_reason(g, now)
            (refused if reason else ok).append({"id": gid, "title": g["title"], "code": g["code"], "reason": reason} if reason else g)
        gone = db.delete_games([g["id"] for g in ok]) if ok else {"games": 0, "players": 0, "answers": 0}
        for g in ok:
            manager.games.pop(g["code"], None)                   # a stale open game may still be loaded in memory
        if gone["games"]:
            shown = "; ".join(f"{g['title'][:40]} ({g['code']})" for g in ok[:5]) + (f" and {len(ok) - 5} more" if len(ok) > 5 else "")
            ai_note(admin, request, "cleanup", f"Deleted {gone['games']} game{'s' if gone['games'] != 1 else ''} with {gone['players']} players and {gone['answers']} answers: {shown}")
        return {**gone, "refused": refused, "storage": storage_view()}

    @app.get("/api/admin/quizzes")
    async def admin_quizzes(admin: dict = Depends(admin_dep)):
        return db.quiz_overview()

    @app.post("/api/admin/quizzes/delete")
    async def admin_quizzes_delete(request: Request, admin: dict = Depends(admin_dep)):
        body = await request.json()
        raw = body.get("ids") if isinstance(body, dict) else None
        if not isinstance(raw, list) or not raw or len(raw) > 500:
            raise ApiError(400, "Choose between 1 and 500 quizzes to delete.", "bad_request")
        try:
            ids = sorted({int(i) for i in raw})
        except (TypeError, ValueError):
            raise ApiError(400, "Bad request", "bad_request") from None
        titles = [q["title"] for q in db.quiz_overview() if q["id"] in set(ids)]
        deleted = db.delete_quizzes(ids)
        if deleted:
            shown = "; ".join(t[:40] for t in titles[:5]) + (f" and {len(titles) - 5} more" if len(titles) > 5 else "")
            ai_note(admin, request, "cleanup", f"Deleted {deleted} quiz{'zes' if deleted != 1 else ''}: {shown}")
        return {"deleted": deleted, "storage": storage_view()}

    @app.post("/api/admin/uploads/cleanup")
    async def admin_uploads_cleanup(request: Request, admin: dict = Depends(admin_dep)):
        body = await request.json()
        dry = not (isinstance(body, dict) and body.get("dry_run") is False)
        scan = adminconf_mod.scan_uploads(db, settings.uploads_dir)
        if dry:
            return {"dry_run": True, "unused_files": scan["unused_files"], "unused_bytes": scan["unused_bytes"]}
        gone = freed = 0
        for name in scan["names"]:
            path = settings.uploads_dir / name
            try:
                size = path.stat().st_size
                path.unlink()
                gone += 1
                freed += size
            except OSError:
                continue
        if gone:
            ai_note(admin, request, "cleanup", f"Deleted {gone} unused uploaded file{'s' if gone != 1 else ''} ({freed // 1024} KB)")
        return {"dry_run": False, "deleted": gone, "freed_bytes": freed, "storage": storage_view()}

    # ---- personal access tokens and the MCP server --------------------------------------------------
    mcp_limiter = mcp_mod.RateLimiter()

    def mcp_note(user: dict, tool: str, message: str):
        db.mcp_audit_add({"user_id": user["id"], "username": user["username"], "tool": tool, "status": "ok", "message": message})

    def token_view(r: dict) -> dict:
        now = time.time()
        state = "revoked" if r["revoked_at"] else "expired" if r["expires_at"] <= now else "active"
        return {**r, "state": state}

    @app.get("/api/tokens")
    async def tokens_list(user: dict = Depends(user_dep)):
        return {"enabled": db.ai_settings()["mcp_enabled"], "tokens": [token_view(r) for r in db.list_api_tokens(user["id"])],
                "endpoint": settings.public_url.rstrip("/") + "/mcp"}

    @app.post("/api/tokens")
    async def tokens_create(request: Request, user: dict = Depends(user_dep)):
        if not db.ai_settings()["mcp_enabled"]:
            raise ApiError(403, "Assistant access is switched off. Ask a Super Admin to turn it on.", "mcp_off")
        body = await request.json()
        name = re.sub(r"\s+", " ", mcp_mod.TOKEN_NAME_RE.sub("", str(body.get("name") or ""))).strip()[:60]
        if not name:
            raise ApiError(400, "Give the token a name, such as the app you will use it in.", "bad_request")
        scope = body.get("scope")
        if scope not in ("read", "write"):
            raise ApiError(400, "Choose read-only or read and write.", "bad_request")
        try:
            days = max(1, min(365, int(body.get("days", 90))))
        except (TypeError, ValueError):
            raise ApiError(400, "Days must be a number.", "bad_request") from None
        if db.active_token_count(user["id"]) >= 10:
            raise ApiError(400, "You already have 10 active tokens. Revoke one you no longer use.", "too_many")
        token = mcp_mod.new_token()
        token_id = db.create_api_token(user["id"], name, auth.hash_token(token), "\u2026" + token[-4:], scope, time.time() + days * 86400)
        mcp_note(user, "token.create", f"{name} ({scope}, {days} days)")
        return {"id": token_id, "token": token, "endpoint": settings.public_url.rstrip("/") + "/mcp"}

    @app.delete("/api/tokens/{token_id}")
    async def tokens_revoke(token_id: int, user: dict = Depends(user_dep)):
        if not db.revoke_api_token(token_id, user["id"]):
            raise HTTPException(404, "Token not found")
        mcp_note(user, "token.revoke", f"token {token_id}")
        return {"ok": True}

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        return await mcp_mod.handle_post(request, db, manager, settings, mcp_limiter)

    @app.get("/mcp")
    @app.delete("/mcp")
    async def mcp_other():
        # no server-initiated stream and no sessions to end: this server only answers POSTs
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.get("/api/admin/mcp/audit")
    async def mcp_admin_audit(user: str = "", status: str = "", days: int = 30, limit: int = 50, offset: int = 0, admin: dict = Depends(admin_dep)):
        return db.mcp_audit_list(max(1, min(limit, 200)), max(0, offset), user[:40], status[:20], time.time() - max(1, min(days, 3650)) * 86400)

    # ---- games (owner-scoped) -----------------------------------------------------------------
    @app.post("/api/games")
    async def start_game(request: Request, user: dict = Depends(user_dep)):
        body = await request.json()
        try:
            quiz_id = int(body.get("quiz_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "quiz_id required") from None
        quiz_for(user, quiz_id)
        game = manager.create(quiz_id, body.get("settings"), owner_id=user["id"])
        return {"id": game.id, "code": game.code}

    @app.get("/api/games")
    async def list_games(scope: str = "", user: dict = Depends(user_dep)):
        everything = user["role"] == "admin" and scope != "mine"
        rows = db.list_games(owner_id=None if everything else user["id"])
        for r in rows:
            r["live"] = r["code"] in manager.games and manager.games[r["code"]].phase != "finished"
            r["mine"] = r["owner_id"] == user["id"]
            # lets the dashboard open a projector that can drive the game (media timing); list is already owner-scoped
            r["screen_key"] = manager.games[r["code"]].screen_key if r["live"] else None
        return rows

    @app.get("/api/games/{game_id}/results")
    async def results(game_id: int, user: dict = Depends(user_dep)):
        game_for(user, game_id)
        return game_results(db, game_id)

    @app.get("/api/games/{game_id}/players/{player_id}")
    async def player_report(game_id: int, player_id: int, user: dict = Depends(user_dep)):
        game_for(user, game_id)
        data = player_detail(db, game_id, player_id)
        if not data:
            raise HTTPException(404, "Player not found")
        return data

    @app.get("/api/games/{game_id}/results.csv")
    async def results_csv(game_id: int, user: dict = Depends(user_dep)):
        game_for(user, game_id)
        data = game_results(db, game_id)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Rank", "Nickname", "Email", "Score", "Correct answers", "Answered", "Bot"])
        for r in data["leaderboard"]:
            # prefix formula-looking cells so spreadsheets don't execute them
            cells = [("'" + c) if isinstance(c, str) and c[:1] in "=+-@" else c for c in (r["nickname"], r["email"])]
            w.writerow([r["rank"], cells[0], cells[1], r["score"], r["correct"], r["answered"], int(r["is_bot"])])
        return Response(out.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="results-{game_id}.csv"'})

    # ---- public (players + projector) ------------------------------------------------------------
    def base_url(request: Request) -> str:
        """Address players should use: the one the projector browsed to, unless that is localhost."""
        if settings.origin_secret and settings.public_url_set:
            return settings.public_url                    # behind CloudFront the Host header is the server's own AWS name, not the site's
        host = request.headers.get("host", "")
        if host and host.split(":")[0] not in ("localhost", "127.0.0.1", "[::1]"):
            return f"{request.url.scheme}://{host}"
        return settings.public_url

    @app.get("/api/game/{code}")
    async def game_info(code: str, request: Request, response: Response):
        game = manager.get(code)
        if not game:
            raise HTTPException(404, "No game with that code")
        return {"title": game.title, "phase": game.phase, "ask_email": game.settings["ask_email"], "real_names": game.settings["real_names"],
                "join_url": base_url(request), "skin": game.settings.get("skin", "classic"), "avatar_groups": engine_mod.AVATAR_GROUPS}

    @app.post("/api/join")
    async def join(request: Request):
        body = await request.json()
        game = manager.get(body.get("code"))
        if not game:
            raise GameError("No game with that code", 404)
        player = game.join(body.get("nickname"), body.get("email", ""), body.get("token"), avatar=body.get("avatar"))
        return {"token": player.token, "nickname": player.nickname, "avatar": player.avatar, "code": game.code}

    @app.get("/qr/{code}.svg")
    async def qr(code: str, request: Request):
        if not manager.get(code):
            raise HTTPException(404, "No game with that code")
        buf = io.BytesIO()
        segno.make(f"{base_url(request)}/j/{code}", error="m").save(buf, kind="svg", scale=10, border=2, dark="#000", light="#fff")
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    # ---- pages ----------------------------------------------------------------------------------------
    def page(name: str):
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})

    @app.get("/")
    async def index():
        return page("index.html")

    @app.get("/host")
    async def host_page():
        return page("host.html")

    @app.get("/j/{code}")
    async def join_page(code: str):
        return page("play.html")

    @app.get("/play")
    async def play_page():
        return page("play.html")

    @app.get("/screen/{code}")
    async def screen_page(code: str):
        return page("screen.html")

    # ---- events: register ahead of time, walk straight in when the host opens the lobby -----------------------
    pin_guard = auth.LoginGuard(user_limit=5, ip_limit=60, window=600, lock=900)     # PIN guesses: per person and per address
    reg_counts: dict[int, tuple[float, int]] = {}

    def reg_count(event_id: int) -> int:
        """Registrations so far, cached for 5 s: the public event page is polled by every waiting phone."""
        hit, now = reg_counts.get(event_id), time.monotonic()
        if hit and now - hit[0] < 5:
            return hit[1]
        n = db.count_registrations(event_id)
        reg_counts[event_id] = (now, n)
        return n

    def event_by_slug(slug: str) -> dict:
        ev = db.get_event_by_slug(str(slug or "").lower())
        if not ev:
            raise HTTPException(404, "Event not found")
        return ev

    def event_game(ev: dict):
        return manager.get(ev["game_code"]) if ev["game_code"] else None

    def event_ticket(ev: dict, body: dict) -> dict:
        t = str(body.get("ticket") or "")
        reg = db.registration_by_ticket(ev["id"], t) if t else None
        if not reg:
            raise GameError("That registration was not found. Please sign in again.", 404)
        return reg

    def reg_public(reg: dict) -> dict:
        return {"ticket": reg["ticket"], "name": reg["name"], "avatar": reg["avatar"], "email": reg["email"]}

    @app.get("/api/event/{slug}")
    async def event_info(slug: str, groups: int = 0):
        ev = event_by_slug(slug)
        state, n = ev_mod.event_state(ev, event_game(ev)), reg_count(ev["id"])
        out = {"slug": ev["slug"], "title": ev["title"], "description": ev["description"], "starts_at": ev["starts_at"], "duration_min": ev["duration_min"],
               "tz": ev["tz"], "state": state, "registration": ev_mod.registration_status(ev, state, n), "registered": n, "capacity": ev["capacity"],
               "server_now": time.time()}
        if groups:
            out["avatar_groups"] = engine_mod.AVATAR_GROUPS
        return out

    @app.post("/api/event/{slug}/register")
    async def event_register(slug: str, request: Request):
        ev, body = event_by_slug(slug), await request.json()
        state = ev_mod.event_state(ev, event_game(ev))
        status = ev_mod.registration_status(ev, state, db.count_registrations(ev["id"]))
        if status != "open":
            raise GameError({"full": "Sorry, this event is full.", "closed": "Registration for this event is closed.",
                             "started": "The quiz has already started, so registration is closed.", "finished": "This quiz has finished."}[status], 403)
        name = engine_mod.clean_nick(body.get("name"), engine_mod.REAL_NAME_MAX)
        if not engine_mod.is_full_name(name):
            raise GameError("Please enter your first and last name")
        email = str(body.get("email") or "").strip().lower()[:120]
        if not engine_mod.EMAIL_RE.fullmatch(email):
            raise GameError("Please enter a valid email address")
        pin = ev_mod.clean_pin(body.get("pin"))
        if not pin:
            raise GameError("Choose a 4-digit PIN")
        if body.get("consent") is not True:
            raise GameError("Please tick the box to agree to how your details are used")
        reg_id = db.add_registration(ev["id"], email, name, engine_mod.clean_avatar(body.get("avatar")), ev_mod.hash_pin(pin), ev_mod.new_ticket())
        if reg_id is None:
            raise GameError("That email is already registered for this event. Use “Sign in” with your PIN.", 409)
        reg_counts.pop(ev["id"], None)
        return reg_public(db.registration_by_email(ev["id"], email))

    @app.post("/api/event/{slug}/signin")
    async def event_signin(slug: str, request: Request):
        ev, body = event_by_slug(slug), await request.json()
        email, ip = str(body.get("email") or "").strip().lower()[:120], client_ip(request)
        key = f"{ev['id']}:{email}"
        wait = pin_guard.retry_after(key, ip)
        if wait:
            raise ApiError(429, f"Too many tries. Please wait {fmt_wait(wait)} and try again.", "throttled", {"Retry-After": str(wait)})
        reg = db.registration_by_email(ev["id"], email)
        if reg and ev_mod.verify_pin(str(body.get("pin") or ""), reg["pin_hash"]):
            pin_guard.success(key)
            return reg_public(reg)
        pin_guard.failure(key, ip)
        raise GameError("That email and PIN do not match a registration.", 403)

    @app.post("/api/event/{slug}/me")
    async def event_me(slug: str, request: Request):
        ev = event_by_slug(slug)
        return reg_public(event_ticket(ev, await request.json()))

    @app.post("/api/event/{slug}/enter")
    async def event_enter(slug: str, request: Request):
        ev = event_by_slug(slug)
        reg, game = event_ticket(ev, await request.json()), event_game(ev)
        state = ev_mod.event_state(ev, game)
        if state == "finished":
            raise GameError("This quiz has finished.", 410)
        if state not in ev_mod.ENTER_STATES:
            raise GameError("The quiz has not opened yet. Hang tight!", 409)
        if any(p.kicked and p.email.lower() == reg["email"] for p in game.players.values()):
            raise GameError("You were removed from this game", 403)
        player = game.join(reg["name"], reg["email"], avatar=reg["avatar"])
        return {"code": game.code, "token": player.token}

    @app.post("/api/event/{slug}/forget")
    async def event_forget(slug: str, request: Request):
        ev = event_by_slug(slug)
        db.delete_registration(ev["id"], event_ticket(ev, await request.json())["id"])
        reg_counts.pop(ev["id"], None)
        return {"ok": True}

    @app.get("/e/{slug}.ics")
    async def event_ics(slug: str, request: Request):
        ev = event_by_slug(slug)
        return Response(ev_mod.make_ics(ev, f"{base_url(request)}/e/{ev['slug']}"), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{ev["slug"]}.ics"'})

    @app.get("/e/{slug}")
    async def event_page(slug: str):
        return page("event.html")

    @app.get("/qr/event/{slug}.svg")
    async def event_qr(slug: str, request: Request):
        ev = event_by_slug(slug)
        buf = io.BytesIO()
        segno.make(f"{base_url(request)}/e/{ev['slug']}", error="m").save(buf, kind="svg", scale=10, border=2, dark="#000", light="#fff")
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    # ---- events: host side (owner-scoped) -------------------------------------------------------------------
    def event_for(user: dict, event_id: int) -> dict:
        ev = db.get_event(event_id)
        if not ev or not can_access(user, ev["owner_id"]):
            raise HTTPException(404, "Event not found")
        return ev

    def host_event(ev: dict, request: Request, registered: int | None = None) -> dict:
        state = ev_mod.event_state(ev, event_game(ev))
        quiz = db.get_quiz(ev["quiz_id"]) if ev["quiz_id"] else None
        return {**ev, "reg_open": bool(ev["reg_open"]), "state": state, "quiz_title": quiz["title"] if quiz else "",
                "registered": db.count_registrations(ev["id"]) if registered is None else registered,
                "url": f"{base_url(request)}/e/{ev['slug']}"}

    def event_fields(body: dict, user: dict, base: dict | None = None) -> dict:
        """Validated event fields from a request body; only the keys that were sent (all of them when creating)."""
        out: dict = {}
        if "title" in body or base is None:
            out["title"] = ev_mod.clean_text(body.get("title"), 120)
            if not out["title"]:
                raise GameError("Give the event a title")
        if "description" in body or base is None:
            out["description"] = ev_mod.clean_text(body.get("description"), 1000)
        if "starts_at" in body or base is None:
            try:
                out["starts_at"] = float(body.get("starts_at"))
            except (TypeError, ValueError):
                raise GameError("Choose when the quiz starts") from None
        if "duration_min" in body or base is None:
            out["duration_min"] = ev_mod.duration_ok(body.get("duration_min"))
        if "tz" in body or base is None:
            out["tz"] = ev_mod.clean_tz(body.get("tz"))
        if "capacity" in body or base is None:
            try:
                out["capacity"] = max(1, min(int(body.get("capacity") or settings.max_players), 5000))
            except (TypeError, ValueError):
                raise GameError("Capacity must be a number") from None
        if "quiz_id" in body or base is None:
            qid = body.get("quiz_id")
            if qid in (None, "", 0):
                out["quiz_id"] = None
            else:
                try:
                    out["quiz_id"] = quiz_for(user, int(qid))["id"]
                except (TypeError, ValueError):
                    raise GameError("Choose one of your quizzes") from None
        if "reg_open" in body and isinstance(body["reg_open"], bool):
            out["reg_open"] = int(body["reg_open"])
        return out

    @app.get("/api/events")
    async def list_events(request: Request, scope: str = "", user: dict = Depends(user_dep)):
        everything = user["role"] == "admin" and scope != "mine"
        return [{**host_event(e, request, e["registered"]), "owner_name": e["owner_name"], "mine": e["owner_id"] == user["id"]}
                for e in db.list_events(owner_id=None if everything else user["id"])]

    @app.post("/api/events")
    async def create_event(request: Request, user: dict = Depends(user_dep)):
        fields = event_fields(await request.json(), user)
        for _ in range(8):
            slug = ev_mod.make_slug(fields["title"])
            if not db.get_event_by_slug(slug):
                break
        event_id = db.create_event(slug, user["id"], fields["title"], fields["description"], fields["starts_at"], fields["duration_min"],
                                   fields["tz"], fields["quiz_id"], fields["capacity"])
        return host_event(db.get_event(event_id), request)

    @app.get("/api/events/{event_id}")
    async def get_event(event_id: int, request: Request, user: dict = Depends(user_dep)):
        ev = event_for(user, event_id)
        game = event_game(ev)
        here = {p.email.lower() for p in game.players.values() if not p.kicked and p.email} if game else set()
        regs = [{"id": r["id"], "name": r["name"], "email": r["email"], "avatar": r["avatar"], "created_at": r["created_at"], "arrived": r["email"] in here}
                for r in db.event_registrations(ev["id"])]
        return {**host_event(ev, request, len(regs)), "registrations": regs, "arrived": sum(1 for r in regs if r["arrived"])}

    @app.patch("/api/events/{event_id}")
    async def patch_event(event_id: int, request: Request, user: dict = Depends(user_dep)):
        ev = event_for(user, event_id)
        db.update_event(ev["id"], **event_fields(await request.json(), user, base=ev))
        return host_event(db.get_event(ev["id"]), request)

    @app.delete("/api/events/{event_id}")
    async def delete_event(event_id: int, user: dict = Depends(user_dep)):
        ev = event_for(user, event_id)
        db.delete_event(ev["id"])                      # every registration (names, emails) is deleted with it
        reg_counts.pop(ev["id"], None)
        return {"ok": True}

    @app.post("/api/events/{event_id}/open-lobby")
    async def event_open_lobby(event_id: int, request: Request, user: dict = Depends(user_dep)):
        """Create the live game for this event (or return the one already running). The event page turns into 'Join now'."""
        ev = event_for(user, event_id)
        game = event_game(ev)
        if game and game.phase != "finished":
            return {"code": game.code}
        if not ev["quiz_id"]:
            raise GameError("Choose the quiz for this event first")
        quiz_for(user, ev["quiz_id"])
        try:
            body = await request.json()
        except ValueError:
            body = {}
        chosen = {k: v for k, v in (body.get("settings") or {}).items() if k in ("speed_bonus", "auto_reveal", "jokers", "skin")}
        game = manager.create(ev["quiz_id"], {**chosen, "real_names": True, "ask_email": True}, owner_id=ev["owner_id"] or user["id"])
        db.update_event(ev["id"], game_code=game.code)
        return {"code": game.code}

    @app.delete("/api/events/{event_id}/registrations/{reg_id}")
    async def remove_registration(event_id: int, reg_id: int, user: dict = Depends(user_dep)):
        ev = event_for(user, event_id)
        if not db.delete_registration(ev["id"], reg_id):
            raise HTTPException(404, "Registration not found")
        reg_counts.pop(ev["id"], None)
        return {"ok": True}

    @app.post("/api/events/{event_id}/registrations/{reg_id}/reset-pin")
    async def reset_registration_pin(event_id: int, reg_id: int, user: dict = Depends(user_dep)):
        """For someone who forgot their PIN: a fresh one for the host to pass on in person or by message."""
        ev = event_for(user, event_id)
        if not any(r["id"] == reg_id for r in db.event_registrations(ev["id"])):
            raise HTTPException(404, "Registration not found")
        pin = ev_mod.new_pin()
        db.set_registration_pin(reg_id, ev_mod.hash_pin(pin))
        return {"pin": pin}

    @app.get("/api/events/{event_id}/registrations.csv")
    async def registrations_csv(event_id: int, user: dict = Depends(user_dep)):
        ev = event_for(user, event_id)
        game = event_game(ev)
        here = {p.email.lower() for p in game.players.values() if not p.kicked and p.email} if game else set()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Name", "Email", "Registered (UTC)", "Arrived"])
        for r in db.event_registrations(ev["id"]):
            cells = [("'" + c) if c and c[0] in "=+-@" else c for c in (r["name"], r["email"])]   # spreadsheets must not run formulas
            w.writerow([*cells, time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["created_at"])), "yes" if r["email"] in here else "no"])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{ev["slug"]}-registrations.csv"'})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    # ---- websockets ---------------------------------------------------------------------------------------
    @app.websocket("/ws/{role}/{code}")
    async def ws_endpoint(websocket: WebSocket, role: str, code: str):
        game = manager.get(code)
        player = None
        controller = False
        if role not in ("host", "screen", "player") or not game:
            await websocket.close(code=4404)
            return
        if role == "host":
            origin = websocket.headers.get("origin")
            sites = {websocket.headers.get("host")}
            if settings.origin_secret and settings.public_url_set:
                sites.add(urlparse(settings.public_url).netloc)       # CloudFront rewrites Host; the browser's Origin is still the site
            same_origin = not origin or urlparse(origin).netloc in sites
            user = user_from_cookie(websocket.cookies) if same_origin else None
            if not user or user["must_change"]:
                await websocket.close(code=4401)
                return
            if not can_access(user, game.owner_id):
                await websocket.close(code=4403)  # somebody else's game
                return
        elif role == "screen":
            if len(game.screens) >= MAX_SCREENS:  # the projector view is public, so don't let it be a socket sink
                await websocket.close(code=4429)
                return
            controller = hmac.compare_digest(websocket.query_params.get("k", "").encode(), game.screen_key.encode())
        elif role == "player":
            player = game.by_token.get(websocket.query_params.get("token", ""))
            if not player:
                await websocket.close(code=4401)
                return
            if player.kicked:
                await websocket.close(code=4403)
                return
        await websocket.accept()
        await game.connect(role, websocket, player)

        window_start, count = time.monotonic(), 0
        try:
            while True:
                text = await websocket.receive_text()
                now = time.monotonic()
                if now - window_start >= 1:
                    window_start, count = now, 0
                count += 1
                if count > MAX_MSGS_PER_SEC or len(text) > MAX_WS_BYTES:
                    continue
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("t") == "ping":
                    await websocket.send_json({"t": "pong", "c": msg.get("c"), "s": time.time() * 1000})
                    continue
                try:
                    if role == "host":
                        await game.on_host(msg)
                    elif role == "screen":
                        await game.on_screen(msg, controller)
                    elif role == "player":
                        await game.on_player(player, msg)
                except GameError as e:
                    await websocket.send_json({"t": "error", "msg": e.message})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("websocket handler failed")
        finally:
            game.disconnect(role, websocket, player)

    # ---- static assets ---------------------------------------------------------------------------------------
    app.mount("/static", RevalidatedStatic(directory=STATIC), name="static")
    return app

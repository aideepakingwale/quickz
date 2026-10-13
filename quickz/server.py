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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .cloud import CloudSync
from .config import Settings
from .db import Database
from .engine import GameError, GameManager, game_results, player_detail
from .questions import ValidationError, parse_text_quiz, validate_quiz

log = logging.getLogger("quickz")
STATIC = Path(__file__).parent / "static"
COOKIE = "quickz_session"
CSRF_HEADER = "x-quickz"
MAX_WS_BYTES = 4096
MAX_MSGS_PER_SEC = 25
MAX_SCREENS = 20
MAX_IMAGE = 5 * 1024 * 1024
IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
]


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
    manager = GameManager(db)
    manager.restore()
    db.purge_sessions()
    guard = guard or auth.LoginGuard()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        if cloud:
            cloud.start()
        yield
        if cloud:
            await cloud.stop()

    app = FastAPI(title="Quickz", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings, app.state.db, app.state.manager, app.state.guard, app.state.cloud = settings, db, manager, guard, cloud
    # Until the first Super Admin exists, setup needs this one-time token (printed in the terminal).
    app.state.setup_token = (settings.setup_token or secrets.token_urlsafe(9)) if db.count_users() == 0 else None

    # ---- errors + headers --------------------------------------------------------
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status, headers=exc.headers)

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
                "max_media_mb": settings.max_media_mb}

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
            raise ApiError(403, "That setup token is not right. Copy it from the terminal where Quickz is running.", "bad_token")
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
        payload = {"title": quiz["title"], "description": quiz["description"], "questions": quiz["questions"]}
        return Response(json.dumps(payload, indent=2), media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="quiz-{quiz_id}.json"'})

    @app.post("/api/uploads")
    async def upload(request: Request, user: dict = Depends(user_dep)):
        """Images (5 MB) and audio/video (QUICKZ_MAX_MEDIA_MB). Streamed to disk, type decided by sniffing the bytes."""
        media_cap = settings.max_media_mb * 1024 * 1024
        if int(request.headers.get("content-length") or 0) > max(media_cap, MAX_IMAGE):
            raise HTTPException(413, f"File too large (max {settings.max_media_mb} MB)")
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
                            fh.write(head)
                            head = b""
                    else:
                        fh.write(chunk)
                    if size > (MAX_IMAGE if kind == "image" else media_cap):
                        raise HTTPException(413, "Image too large (max 5 MB)" if kind == "image" else f"File too large (max {settings.max_media_mb} MB)")
                if kind is None:  # tiny body
                    sniffed = sniff_upload(head)
                    if not sniffed:
                        raise HTTPException(400, "Unsupported file.")
                    kind, ext = sniffed
                    fh.write(head)
            final = settings.uploads_dir / (name + ext)
            tmp.replace(final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if cloud:
            cloud.push_upload(final)
        return {"url": f"/uploads/{name}{ext}", "kind": kind}

    @app.get("/uploads/{name}")
    async def serve_upload(name: str):
        """Uploaded media. Names are random; if the local disk was wiped the file is pulled back from the bucket."""
        if not UPLOAD_NAME.match(name):
            raise HTTPException(404, "Not found")
        path = settings.uploads_dir / name
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
        host = request.headers.get("host", "")
        if host and host.split(":")[0] not in ("localhost", "127.0.0.1", "[::1]"):
            return f"{request.url.scheme}://{host}"
        return settings.public_url

    @app.get("/api/game/{code}")
    async def game_info(code: str, request: Request, response: Response):
        game = manager.get(code)
        if not game:
            raise HTTPException(404, "No game with that code")
        return {"title": game.title, "phase": game.phase, "ask_email": game.settings["ask_email"], "join_url": base_url(request)}

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
            same_origin = not origin or urlparse(origin).netloc == websocket.headers.get("host")
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
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
